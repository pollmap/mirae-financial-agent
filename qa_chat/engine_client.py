"""Strict client for the immutable five-string contest engine contract."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from qa_chat.models import EngineResponse

ENGINE_FIELDS = {
    "question_id",
    "question",
    "retrieved_context",
    "think_trace",
    "answer",
}
READY_IDENTITY_FIELDS = (
    "engine_git_sha",
    "engine_image_digest",
    "data_hash",
    "model_id",
    "hcx_base_url",
    "planner_stage",
)

# httpx's INFO line includes the complete GET URL. The immutable contest API
# carries the question and clarification token in its query string, so those
# library request logs must never be enabled in the QA process.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class EngineError(RuntimeError):
    reason_code = "ENGINE_UNAVAILABLE"
    retryable = True


class EngineContractError(EngineError):
    reason_code = "ENGINE_SCHEMA_DRIFT"
    retryable = False


class EngineRejected(EngineError):
    reason_code = "ENGINE_REJECTED_REQUEST"
    retryable = False


class EngineClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 25.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def ready(self) -> dict[str, Any]:
        try:
            response = await self._client.get("/health/ready")
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return {"status": "unavailable", "reason": "ENGINE_HEALTH_UNAVAILABLE"}
        if (
            response.status_code != 200
            or response.headers.get("content-type", "").split(";", 1)[0]
            != "application/json"
            or not isinstance(payload, dict)
            or payload.get("status") != "ready"
        ):
            return {"status": "unavailable", "reason": "ENGINE_NOT_READY"}
        identity = {field: payload.get(field) for field in READY_IDENTITY_FIELDS}
        if not all(isinstance(value, str) and value for value in identity.values()):
            return {"status": "unavailable", "reason": "ENGINE_IDENTITY_MISSING"}
        snapshot = payload.get("data_snapshot_date")
        if not isinstance(snapshot, str) or not snapshot:
            return {"status": "unavailable", "reason": "ENGINE_IDENTITY_MISSING"}
        return {
            "status": "ready",
            "data_snapshot_date": snapshot,
            **identity,
        }

    async def answer(
        self,
        *,
        question_id: str,
        question: str,
        clarification_token: str | None = None,
        clarification_response: str | None = None,
    ) -> tuple[EngineResponse, dict[str, Any]]:
        params = {"question_id": question_id, "question": question}
        if clarification_token is not None or clarification_response is not None:
            if not clarification_token or not clarification_response:
                raise EngineRejected("clarification state must be supplied as a complete pair")
            params["clarification_token"] = clarification_token
            params["clarification_response"] = clarification_response
        try:
            response = await self._client.get("/answer", params=params)
        except httpx.RequestError as exc:
            raise EngineError("engine transport failed") from exc
        if response.status_code == 429:
            raise EngineError("engine rate limited the request")
        if response.status_code == 400:
            raise EngineRejected("engine rejected the request")
        if response.status_code >= 500:
            raise EngineError("engine returned a controlled unavailable response")
        if response.status_code != 200:
            raise EngineContractError("unexpected engine status")
        if response.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise EngineContractError("engine response is not JSON")
        try:
            payload = response.json()
        except ValueError as exc:
            raise EngineContractError("engine response JSON is malformed") from exc
        if not isinstance(payload, dict) or set(payload) != ENGINE_FIELDS:
            raise EngineContractError("engine response fields changed")
        if not all(isinstance(payload[field], str) for field in ENGINE_FIELDS):
            raise EngineContractError("all engine response fields must remain strings")
        try:
            parsed = EngineResponse.model_validate(payload)
        except ValidationError as exc:
            raise EngineContractError("engine response failed validation") from exc
        if parsed.question_id != question_id:
            raise EngineContractError("engine question_id does not match the request")
        if parsed.question != question:
            raise EngineContractError("engine question does not match the request")
        try:
            context = json.loads(parsed.retrieved_context)
        except ValueError as exc:
            raise EngineContractError("retrieved_context is not JSON") from exc
        if not isinstance(context, dict):
            raise EngineContractError("retrieved_context must be an object")
        return parsed, context
