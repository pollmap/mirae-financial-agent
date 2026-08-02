"""Native HyperCLOVA X Structured Outputs client for QueryPlan generation.

The adapter deliberately has no generic provider abstraction or alternate-model
fallback.  It always calls CLOVA Studio's native v3 Chat Completions endpoint
and requires HCX-007, the HCX model that supports Structured Outputs.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast

import httpx

DEFAULT_BASE_URL = "https://clovastudio.stream.ntruss.com"
DEFAULT_MODEL_ID = "HCX-007"
DEFAULT_SYSTEM_PROMPT = (
    "사용자의 금융상품 질문을 제공된 JSON Schema의 QueryPlan으로만 변환합니다. "
    "데이터 값이나 상품 정보를 추측하지 말고, 불명확하거나 누락된 조건은 "
    "명시적인 clarification 필드로 표현합니다."
)

_RESET_SECONDS_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$", re.IGNORECASE)
_RETRIABLE_STATUS_CODES = frozenset({429, *range(500, 600)})

PlanT = TypeVar("PlanT")


class PydanticValidator(Protocol[PlanT]):
    """The subset of a Pydantic model class used by this adapter."""

    @classmethod
    def model_validate(cls, value: Any) -> PlanT: ...


PlanValidator = Callable[[Mapping[str, Any]], PlanT | Awaitable[PlanT]] | PydanticValidator[PlanT]
Sleep = Callable[[float], Awaitable[None]]
RequestIdFactory = Callable[[], str]
BeforeAttempt = Callable[[int], Awaitable[None]]


class HCXError(RuntimeError):
    """Base exception for safe, redacted HCX failures."""


class HCXConfigurationError(HCXError):
    """Raised before a request when HCX configuration is invalid."""


class HCXTransportError(HCXError):
    """Raised for a network failure or a non-retriable HTTP response."""


class HCXRetryExhausted(HCXTransportError):
    """Raised after the bounded retry budget for 429/5xx is exhausted."""

    def __init__(self, *, status_code: int | None, attempts: int) -> None:
        self.status_code = status_code
        self.attempts = attempts
        failure = f"HTTP {status_code}" if status_code is not None else "transport error"
        super().__init__(f"HCX request failed after {attempts} attempts ({failure})")


class HCXResponseError(HCXError):
    """Raised when CLOVA Studio returns a malformed or unsuccessful body."""


class HCXFinishReasonError(HCXResponseError):
    """Raised when HCX did not complete the structured output normally."""


class HCXValidationError(HCXResponseError):
    """Raised when structured output fails JSON or local model validation."""


@dataclass(frozen=True, slots=True)
class HCXPlanResult[PlanT]:
    """Validated plan plus non-sensitive operational metadata."""

    plan: PlanT
    model_id: str
    request_id: str
    usage: Mapping[str, int]


class HCXStructuredPlanner:
    """Generate and locally validate a typed QueryPlan with HCX-007.

    ``validator`` may be either a callback or a Pydantic model class exposing
    ``model_validate``.  The generated JSON is never used until that local
    validator succeeds.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_id: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float | httpx.Timeout = 20.0,
        max_retries: int = 2,
        max_backoff_seconds: float = 30.0,
        sleep: Sleep = asyncio.sleep,
        request_id_factory: RequestIdFactory | None = None,
    ) -> None:
        resolved_key = api_key if api_key is not None else os.getenv("CLOVA_STUDIO_API_KEY")
        if not resolved_key or not resolved_key.strip():
            raise HCXConfigurationError("CLOVA_STUDIO_API_KEY is required")

        resolved_model = model_id or os.getenv("HCX_MODEL_ID", DEFAULT_MODEL_ID)
        if resolved_model != DEFAULT_MODEL_ID:
            raise HCXConfigurationError(
                "Native Structured Outputs currently require HCX_MODEL_ID=HCX-007"
            )

        resolved_base_url = base_url or os.getenv("HCX_BASE_URL", DEFAULT_BASE_URL)
        if not resolved_base_url or not resolved_base_url.strip():
            raise HCXConfigurationError("HCX_BASE_URL must not be empty")
        if not 0 <= max_retries <= 4:
            raise HCXConfigurationError("max_retries must be between 0 and 4")
        if not 0 <= max_backoff_seconds <= 60:
            raise HCXConfigurationError("max_backoff_seconds must be between 0 and 60")

        self._api_key = resolved_key.strip()
        self._model_id = resolved_model
        self._base_url = resolved_base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout)
        self._max_retries = max_retries
        self._max_backoff_seconds = max_backoff_seconds
        self._sleep = sleep
        self._request_id_factory = request_id_factory or (lambda: str(uuid.uuid4()))

    async def __aenter__(self) -> HCXStructuredPlanner:
        await self._get_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close only the internally-created HTTP client."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_plan(
        self,
        *,
        question: str,
        schema: Mapping[str, Any],
        validator: PlanValidator[PlanT],
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_completion_tokens: int = 1_024,
        seed: int = 2_026_080_2,
        before_attempt: BeforeAttempt | None = None,
    ) -> PlanT:
        """Return only the locally validated plan."""

        result = await self.create_plan_result(
            question=question,
            schema=schema,
            validator=validator,
            system_prompt=system_prompt,
            max_completion_tokens=max_completion_tokens,
            seed=seed,
            before_attempt=before_attempt,
        )
        return result.plan

    async def create_plan_result(
        self,
        *,
        question: str,
        schema: Mapping[str, Any],
        validator: PlanValidator[PlanT],
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_completion_tokens: int = 1_024,
        seed: int = 2_026_080_2,
        before_attempt: BeforeAttempt | None = None,
    ) -> HCXPlanResult[PlanT]:
        """Generate a plan and return it with safe operational metadata."""

        if not question or not question.strip():
            raise HCXValidationError("question must not be empty")
        if schema.get("type") != "object":
            raise HCXValidationError("HCX response schema must have object as its root type")
        if not 1 <= max_completion_tokens <= 32_768:
            raise HCXValidationError("max_completion_tokens must be between 1 and 32768")
        if not 1 <= seed <= 4_294_967_295:
            raise HCXValidationError("seed must be between 1 and 4294967295")

        body = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            "topP": 0.1,
            "topK": 0,
            "maxCompletionTokens": max_completion_tokens,
            "temperature": 0.0,
            "repetitionPenalty": 1.05,
            "seed": seed,
            "thinking": {"effort": "none"},
            "stop": [],
            "responseFormat": {"type": "json", "schema": dict(schema)},
        }
        response, request_id = await self._post_with_retry(
            body, before_attempt=before_attempt
        )
        content, usage = self._extract_completed_content(response)

        try:
            decoded = json.loads(content)
        except (TypeError, ValueError) as exc:
            raise HCXValidationError("HCX structured output was not valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise HCXValidationError("HCX structured output must be a JSON object")

        plan = await self._run_validator(decoded, validator)
        return HCXPlanResult(
            plan=plan,
            model_id=self._model_id,
            request_id=request_id,
            usage=usage,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        return self._client

    async def _post_with_retry(
        self,
        body: Mapping[str, Any],
        *,
        before_attempt: BeforeAttempt | None = None,
    ) -> tuple[httpx.Response, str]:
        client = await self._get_client()
        endpoint = f"{self._base_url}/v3/chat-completions/{self._model_id}"
        total_attempts = self._max_retries + 1

        for attempt in range(total_attempts):
            if before_attempt is not None:
                await before_attempt(attempt)
            request_id = self._request_id_factory()
            try:
                response = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "X-NCP-CLOVASTUDIO-REQUEST-ID": request_id,
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=self._timeout,
                )
            except httpx.TransportError as exc:
                # Connect resets and timeouts are normally transient.  Retry them
                # under the same bounded budget as 429/5xx while keeping request
                # headers and the private evaluation question out of the error.
                if attempt == self._max_retries:
                    raise HCXRetryExhausted(
                        status_code=None,
                        attempts=total_attempts,
                    ) from exc
                await self._sleep(min(0.5 * (2**attempt), self._max_backoff_seconds))
                continue

            if response.status_code < 400:
                return response, request_id

            if response.status_code not in _RETRIABLE_STATUS_CODES:
                raise HCXTransportError(f"HCX returned non-retriable HTTP {response.status_code}")

            if attempt == self._max_retries:
                raise HCXRetryExhausted(
                    status_code=response.status_code,
                    attempts=total_attempts,
                )

            await self._sleep(self._retry_delay(response, attempt))

        raise AssertionError("bounded HCX retry loop exited unexpectedly")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        reset_delays: list[float] = []
        for header in (
            "retry-after",
            "x-ratelimit-reset-requests",
            "x-ratelimit-reset-tokens",
        ):
            raw = response.headers.get(header)
            if raw is None:
                continue
            match = _RESET_SECONDS_RE.match(raw)
            if match:
                reset_delays.append(float(match.group(1)))
                continue
            if header == "retry-after":
                with suppress(ValueError):
                    reset_delays.append(max(float(raw.strip()), 0.0))

        delay = max(reset_delays, default=0.5 * (2**attempt))
        return min(delay, self._max_backoff_seconds)

    @staticmethod
    def _extract_completed_content(
        response: httpx.Response,
    ) -> tuple[str, Mapping[str, int]]:
        try:
            body = response.json()
        except ValueError as exc:
            raise HCXResponseError("HCX response body was not valid JSON") from exc
        if not isinstance(body, Mapping):
            raise HCXResponseError("HCX response body must be a JSON object")

        status = body.get("status")
        if not isinstance(status, Mapping) or str(status.get("code")) != "20000":
            raise HCXResponseError("HCX response status was not successful")

        result = body.get("result")
        if not isinstance(result, Mapping):
            raise HCXResponseError("HCX response did not include a result object")

        finish_reason = result.get("finishReason")
        if finish_reason != "stop":
            raise HCXFinishReasonError(
                f"HCX structured output was incomplete (finishReason={finish_reason!r})"
            )

        message = result.get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise HCXResponseError("HCX response did not include message content")

        usage_raw = result.get("usage")
        usage: dict[str, int] = {}
        if isinstance(usage_raw, Mapping):
            for key in ("promptTokens", "completionTokens", "totalTokens"):
                value = usage_raw.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[key] = value

        return cast(str, message["content"]), usage

    @staticmethod
    async def _run_validator(decoded: Mapping[str, Any], validator: PlanValidator[PlanT]) -> PlanT:
        try:
            model_validate = getattr(validator, "model_validate", None)
            if callable(model_validate):
                return cast(PlanT, model_validate(decoded))

            result = cast(Callable[[Mapping[str, Any]], Any], validator)(decoded)
            if inspect.isawaitable(result):
                result = await result
            return cast(PlanT, result)
        except Exception as exc:
            raise HCXValidationError("HCX structured output failed local validation") from exc


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL_ID",
    "HCXConfigurationError",
    "HCXError",
    "HCXFinishReasonError",
    "HCXPlanResult",
    "HCXResponseError",
    "HCXRetryExhausted",
    "HCXStructuredPlanner",
    "HCXTransportError",
    "HCXValidationError",
]
