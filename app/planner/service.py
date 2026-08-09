"""Concrete planner selection; no generic non-HCX LLM provider exists."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque

from app.config import Settings
from app.domain.models import QueryPlan
from app.planner.deterministic import DeterministicPlanner
from app.planner.hcx import HCXStructuredPlanner, HCXTransportError
from app.planner.schema import (
    HCX_QUERY_PLAN_SCHEMA,
    HCX_SEMANTIC_PLAN_SCHEMA,
    QUERY_PLANNER_SYSTEM_PROMPT,
    SEMANTIC_PLANNER_SYSTEM_PROMPT,
)


class HcxQueryPlanner:
    def __init__(self, settings: Settings) -> None:
        self.name = settings.hcx_model_id
        self._client = HCXStructuredPlanner(
            api_key=settings.clova_studio_api_key,
            model_id=settings.hcx_model_id,
            approved_model_id=settings.approved_hcx_model_id,
            base_url=settings.hcx_base_url,
            timeout=settings.hcx_timeout_seconds,
            max_retries=settings.hcx_max_retries,
        )
        self._semaphore = asyncio.Semaphore(settings.hcx_max_concurrency)
        self._qpm_limit = settings.hcx_qpm_limit
        self._request_starts: deque[float] = deque()
        self._rate_lock = asyncio.Lock()
        self._tpm_budget = settings.hcx_tpm_budget
        self._token_reservations: deque[tuple[float, int]] = deque()
        self._token_lock = asyncio.Lock()
        self._stage = settings.planner_stage
        if self._stage == "two":
            self._system_prompt = SEMANTIC_PLANNER_SYSTEM_PROMPT
            self._schema = HCX_SEMANTIC_PLAN_SCHEMA
        else:
            self._system_prompt = QUERY_PLANNER_SYSTEM_PROMPT
            self._schema = HCX_QUERY_PLAN_SCHEMA
        self._base_token_reservation = (
            len(self._system_prompt.encode("utf-8"))
            + len(
                json.dumps(
                    self._schema,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            + 2_048  # JSON/message framing and fixed request controls
            + 1_024  # configured maximum completion tokens
        )
        self._deadline_seconds = settings.hcx_total_deadline_seconds

    async def _acquire_rate_slot(self) -> None:
        """Bound request starts for a single-process deployment without busy waiting."""

        while True:
            async with self._rate_lock:
                now = time.monotonic()
                while self._request_starts and self._request_starts[0] <= now - 60:
                    self._request_starts.popleft()
                if len(self._request_starts) < self._qpm_limit:
                    self._request_starts.append(now)
                    return
                delay = max(self._request_starts[0] + 60 - now, 0.01)
            await asyncio.sleep(delay)

    async def _acquire_token_slot(self, question: str) -> None:
        """Reserve a conservative rolling TPM upper bound for one HTTP attempt.

        UTF-8 byte length is an intentionally conservative tokenizer-independent
        ceiling for the prompt material, and completion tokens are added
        explicitly.  Reservations are kept for the full rolling minute even
        after a small response so retries and bursts cannot silently exceed the
        operator's configured provider budget.
        """

        reservation = self._base_token_reservation + len(question.encode("utf-8"))
        if reservation > self._tpm_budget:
            raise HCXTransportError("estimated HCX request exceeds configured TPM budget")
        while True:
            async with self._token_lock:
                now = time.monotonic()
                while (
                    self._token_reservations
                    and self._token_reservations[0][0] <= now - 60
                ):
                    self._token_reservations.popleft()
                used = sum(tokens for _, tokens in self._token_reservations)
                if used + reservation <= self._tpm_budget:
                    self._token_reservations.append((now, reservation))
                    return
                delay = max(self._token_reservations[0][0] + 60 - now, 0.01)
            await asyncio.sleep(delay)

    async def plan(self, question: str) -> QueryPlan:
        async def before_attempt(attempt: int) -> None:
            # The first slot is acquired before the concurrency semaphore so
            # a QPM wait does not occupy scarce in-flight capacity.  Every
            # retry is a real provider request and therefore consumes another
            # QPM slot as well.
            if attempt > 0:
                await self._acquire_rate_slot()
                await self._acquire_token_slot(question)

        try:
            # Queueing, rate-limit waiting, provider retries, and validation
            # all share one wall-clock deadline.  A burst can therefore not
            # remain queued indefinitely behind the semaphore.
            async with asyncio.timeout(self._deadline_seconds):
                await self._acquire_rate_slot()
                await self._acquire_token_slot(question)
                async with self._semaphore:
                    if self._stage == "two":
                        return await self._client.create_plan(
                            question=question,
                            schema=self._schema,
                            validator=self._ground_semantic_payload(question),
                            system_prompt=self._system_prompt,
                            max_completion_tokens=1_024,
                            before_attempt=before_attempt,
                        )
                    return await self._client.create_plan(
                        question=question,
                        schema=self._schema,
                        validator=QueryPlan,
                        system_prompt=self._system_prompt,
                        max_completion_tokens=1_024,
                        before_attempt=before_attempt,
                    )
        except TimeoutError as exc:
            raise HCXTransportError("HCX total request deadline exceeded") from exc

    def _ground_semantic_payload(self, question: str):
        """Validator callback: Stage-1 concept JSON -> physical QueryPlan.

        The HCX adapter wraps any exception raised inside a validator
        callback as ``HCXValidationError``, so a ``GroundingError`` here
        (unknown concept, absent binding, missing entity) fails exactly like
        a schema-invalid response: no plan reaches execution.
        """

        from app.semantics.grounder import ground_semantic

        return lambda payload: ground_semantic(payload, question=question)

    async def aclose(self) -> None:
        await self._client.aclose()


def build_planner(settings: Settings) -> DeterministicPlanner | HcxQueryPlanner:
    if settings.planner_mode == "hcx":
        return HcxQueryPlanner(settings)
    return DeterministicPlanner()
