"""Application service: one provider call at most for each accepted turn."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from typing import Any

from qa_chat.config import QASettings
from qa_chat.conversation import ConversationAdapter, StaleClarification
from qa_chat.engine_client import EngineClient, EngineError
from qa_chat.models import AssistantView, FeedbackRequest, MessageRequest, TurnResponse
from qa_chat.privacy import contains_sensitive_input
from qa_chat.repository import Conflict, QARepository, RequestPending

LOGGER = logging.getLogger("mirae.qa.turn")


class PilotDisabled(RuntimeError):
    pass


class ReleaseGatePending(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, *, threshold: int, window_seconds: int, open_seconds: int) -> None:
        self.threshold = threshold
        self.window_seconds = window_seconds
        self.open_seconds = open_seconds
        self.failures: deque[float] = deque()
        self.open_until = 0.0

    def available(self, now: float) -> bool:
        return now >= self.open_until

    def success(self) -> None:
        self.failures.clear()
        self.open_until = 0.0

    def failure(self, now: float) -> None:
        while self.failures and self.failures[0] <= now - self.window_seconds:
            self.failures.popleft()
        self.failures.append(now)
        if len(self.failures) >= self.threshold:
            self.open_until = now + self.open_seconds


class QAGatewayService:
    def __init__(
        self,
        settings: QASettings,
        repository: QARepository,
        engine: EngineClient,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.engine = engine
        self.environment = {
            "engine_git_sha": settings.engine_git_sha,
            "image_digest": settings.engine_image_digest,
            "data_hash": settings.data_hash,
            "model_id": settings.model_id,
            "planner_stage": settings.planner_stage,
            "vector_status": settings.vector_status,
        }
        self.adapter = ConversationAdapter(
            clarification_ttl_seconds=settings.clarification_ttl_seconds,
            environment=self.environment,
        )
        self._tester_locks: dict[str, asyncio.Lock] = {}
        self._global_slots = asyncio.Semaphore(settings.engine_concurrency)
        self._circuit = CircuitBreaker(
            threshold=settings.circuit_failure_threshold,
            window_seconds=settings.circuit_window_seconds,
            open_seconds=settings.circuit_open_seconds,
        )
        self._runtime_readiness_lock = asyncio.Lock()
        self._runtime_readiness_cached_at = 0.0
        self._runtime_readiness_cache: dict[str, Any] | None = None

    async def runtime_readiness(self) -> dict[str, Any]:
        """Bind the configured release identity to the engine actually called."""

        now = time.monotonic()
        if (
            self._runtime_readiness_cache is not None
            and now - self._runtime_readiness_cached_at < 5.0
        ):
            return dict(self._runtime_readiness_cache)
        async with self._runtime_readiness_lock:
            now = time.monotonic()
            if (
                self._runtime_readiness_cache is not None
                and now - self._runtime_readiness_cached_at < 5.0
            ):
                return dict(self._runtime_readiness_cache)
            state = await self.engine.ready()
            if state.get("status") == "ready":
                expected = {
                    "engine_git_sha": self.settings.engine_git_sha,
                    "engine_image_digest": self.settings.engine_image_digest,
                    "data_hash": self.settings.data_hash,
                    "model_id": self.settings.model_id,
                    "hcx_base_url": self.settings.hcx_base_url,
                    "planner_stage": "two",
                }
                mismatches = [
                    field for field, value in expected.items() if state.get(field) != value
                ]
                if mismatches:
                    state = {
                        "status": "identity_mismatch",
                        "reason": "ENGINE_RELEASE_IDENTITY_MISMATCH",
                        "mismatch_fields": mismatches,
                        "data_snapshot_date": state.get("data_snapshot_date"),
                    }
                else:
                    state = {**state, "identity_verified": True}
            self._runtime_readiness_cache = dict(state)
            self._runtime_readiness_cached_at = now
            return dict(state)

    async def submit_message(
        self, tester_id: str, session_id: str, message: MessageRequest
    ) -> dict[str, Any]:
        if not self.settings.pilot_chat_enabled:
            raise PilotDisabled("new chat turns are disabled")
        if not self.settings.release_metadata_ready:
            raise ReleaseGatePending("live gate and release metadata are required")
        runtime_state = await self.runtime_readiness()
        if runtime_state.get("status") != "ready" or not runtime_state.get(
            "identity_verified"
        ):
            raise ReleaseGatePending("engine runtime identity does not match release evidence")
        if len(message.text) > self.settings.question_max_chars:
            raise ValueError("question exceeds configured length")

        canonical = json.dumps(message.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        request_hash = self.repository.hasher.digest("message-request", canonical)
        existing = await self._existing_result_or_wait(
            session_id, tester_id, message.client_message_id, request_hash
        )
        if existing is not None:
            return TurnResponse.model_validate(existing).model_dump(mode="json")
        self.repository.ensure_turn_capacity(session_id, tester_id)

        lock = self._tester_locks.setdefault(tester_id, asyncio.Lock())
        if lock.locked():
            raise Conflict("a tester may have only one in-flight turn")
        async with lock:
            existing = await self._existing_result_or_wait(
                session_id, tester_id, message.client_message_id, request_hash
            )
            if existing is not None:
                return TurnResponse.model_validate(existing).model_dump(mode="json")
            self.repository.ensure_turn_capacity(session_id, tester_id)
            self.repository.reserve_request(
                session_id,
                tester_id,
                message.client_message_id,
                request_hash,
                message.expected_session_version,
                max(60.0, self.settings.engine_timeout_seconds + 30.0),
            )
            try:
                return await self._execute_reserved(tester_id, session_id, message)
            except Exception:
                self.repository.abandon_request(session_id, message.client_message_id)
                raise

    async def _existing_result_or_wait(
        self,
        session_id: str,
        tester_id: str,
        client_message_id: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + min(
            self.settings.engine_timeout_seconds + 1.0, 30.0
        )
        while True:
            try:
                return self.repository.request_status(
                    session_id, tester_id, client_message_id, request_hash
                )
            except RequestPending as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Conflict("the matching request is still processing") from exc
                await asyncio.sleep(min(0.05, remaining))

    async def _execute_reserved(
        self, tester_id: str, session_id: str, message: MessageRequest
    ) -> dict[str, Any]:
        state = self.repository.load_state(session_id, tester_id)
        assistant_id = uuid.uuid4().hex
        user_message_id = uuid.uuid4().hex
        now = time.time()
        engine_request: dict[str, Any] | None = None
        engine_response: dict[str, Any] | None = None
        engine_outcome = "not_called"
        latency_ms = 0.0

        if contains_sensitive_input(message.text):
            view = self.adapter.blocked_view(
                assistant_id=assistant_id,
                reason_code="PERSONAL_DATA_DETECTED",
                content="개인정보는 저장하거나 분석하지 않습니다. 연락처·계좌번호 등 개인정보를 제거한 뒤 다시 질문해 주세요.",
            )
            stored_text = "[개인정보 감지로 원문을 보존하지 않은 메시지]"
            return self._commit(
                tester_id,
                session_id,
                message,
                user_message_id,
                stored_text,
                view,
                state,
                engine_request,
                engine_response,
                engine_outcome,
                latency_ms,
            )

        try:
            prepared = self.adapter.prepare(
                text=message.text,
                reply_to_message_id=message.reply_to_message_id,
                clarification_option_value=message.clarification_option_value,
                state=state,
                now_epoch=now,
                assistant_id=assistant_id,
            )
        except StaleClarification as exc:
            raise Conflict("clarification is stale, spent, or belongs to another turn") from exc

        if prepared.local_view is not None:
            clarification = prepared.local_view.get("clarification")
            if isinstance(clarification, dict):
                products = (state.get("last_completed") or {}).get("products") or []
                state["pending_clarification"] = {
                    "kind": "local_reference",
                    "message_id": assistant_id,
                    "expires_at_epoch": now + self.settings.clarification_ttl_seconds,
                    "consumed": False,
                    "option_values": [
                        option["value"] for option in clarification.get("options") or []
                    ],
                    "products": products[:50],
                    "original_follow_up_text": prepared.local_follow_up_text or message.text,
                }
            state["turn_count"] = int(state.get("turn_count", 0)) + 1
            return self._commit(
                tester_id,
                session_id,
                message,
                user_message_id,
                message.text,
                prepared.local_view,
                state,
                engine_request,
                engine_response,
                engine_outcome,
                latency_ms,
            )

        if not prepared.question:
            raise RuntimeError("conversation adapter returned no action")
        if not self._circuit.available(now):
            state["pending_clarification"] = None
            view = self.adapter.error_view(
                assistant_id=assistant_id,
                content="HCX 연결이 연속으로 실패하여 잠시 새 호출을 중단했습니다. 잠시 뒤 다시 시도해 주세요.",
                reason_code="HCX_CIRCUIT_OPEN",
                retryable=True,
            )
            return self._commit(
                tester_id,
                session_id,
                message,
                user_message_id,
                message.text,
                view,
                state,
                None,
                None,
                "circuit_open",
                0.0,
            )

        self.repository.admit_usage(
            tester_id,
            per_minute=self.settings.per_tester_per_minute,
            per_day=self.settings.per_tester_per_day,
            global_per_minute=self.settings.global_per_minute,
            global_per_day=self.settings.global_per_day,
            total=self.settings.pilot_total_limit,
        )
        question_id = f"QA-{session_id[:8]}-{message.expected_session_version + 1}"
        engine_request = {
            "question_id": question_id,
            "question": prepared.question,
            "clarification_token": prepared.clarification_token,
            "clarification_response": prepared.clarification_response,
        }
        started = time.perf_counter()
        try:
            try:
                async with asyncio.timeout(self.settings.engine_timeout_seconds):
                    async with self._global_slots:
                        raw_response, context = await self.engine.answer(**engine_request)
            except TimeoutError as exc:
                raise EngineError("gateway deadline expired") from exc
            latency_ms = round((time.perf_counter() - started) * 1_000, 2)
            engine_response = raw_response.model_dump(mode="json")
            view, state = self.adapter.from_engine(
                response=raw_response,
                context=context,
                assistant_id=assistant_id,
                state=state,
                now_epoch=time.time(),
                redaction_tokens=tuple(
                    token
                    for token in (prepared.clarification_token,)
                    if token is not None
                ),
            )
            engine_outcome = "succeeded"
            self.repository.record_usage_outcome(tester_id, engine_outcome)
            self._circuit.success()
        except EngineError as exc:
            latency_ms = round((time.perf_counter() - started) * 1_000, 2)
            engine_outcome = exc.reason_code
            self.repository.record_usage_outcome(tester_id, engine_outcome)
            self._circuit.failure(time.time())
            state["pending_clarification"] = None
            view = self.adapter.error_view(
                assistant_id=assistant_id,
                content="금융상품 엔진 응답을 안전하게 확인하지 못했습니다. 다른 모델로 대체하지 않았습니다. 잠시 뒤 다시 시도해 주세요.",
                reason_code=exc.reason_code,
                retryable=exc.retryable,
            )
        return self._commit(
            tester_id,
            session_id,
            message,
            user_message_id,
            message.text,
            view,
            state,
            engine_request,
            engine_response,
            engine_outcome,
            latency_ms,
        )

    def _commit(
        self,
        tester_id: str,
        session_id: str,
        message: MessageRequest,
        user_message_id: str,
        stored_user_text: str,
        assistant_view: dict[str, Any],
        state: dict[str, Any],
        engine_request: dict[str, Any] | None,
        engine_response: dict[str, Any] | None,
        engine_outcome: str,
        latency_ms: float,
    ) -> dict[str, Any]:
        assistant_view = AssistantView.model_validate(assistant_view).model_dump(mode="json")
        result = self.repository.commit_turn(
            session_id=session_id,
            tester_id=tester_id,
            client_message_id=message.client_message_id,
            expected_version=message.expected_session_version,
            user_message_id=user_message_id,
            user_text=stored_user_text,
            assistant_view=assistant_view,
            state=state,
            engine_request=engine_request,
            engine_response=engine_response,
            engine_outcome=engine_outcome,
            latency_ms=latency_ms,
        )
        validated = TurnResponse.model_validate(result).model_dump(mode="json")
        LOGGER.info(
            json.dumps(
                {
                    "event": "qa_turn",
                    "tester": self.repository.hasher.digest("tester-log", tester_id)[:16],
                    "question_chars": len(stored_user_text),
                    "answer_status": assistant_view["status"],
                    "provider_attempted": engine_request is not None,
                    "latency_ms": latency_ms,
                },
                separators=(",", ":"),
            )
        )
        return validated

    def save_feedback(
        self, tester_id: str, message_id: str, feedback: FeedbackRequest
    ) -> dict[str, Any]:
        invalid = set(feedback.tags) - self.settings.feedback_tags
        if invalid:
            raise ValueError("unsupported feedback tag")
        if feedback.verdict == "accurate" and feedback.tags:
            raise ValueError("an accurate verdict cannot include problem tags")
        if feedback.verdict in {"partly_accurate", "incorrect"} and not feedback.tags:
            raise ValueError("problem feedback requires at least one tag")
        return self.repository.upsert_feedback(
            message_id=message_id,
            tester_id=tester_id,
            verdict=feedback.verdict,
            tags=feedback.tags,
            note=feedback.note,
        )
