from __future__ import annotations

import asyncio
import json
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel

from app.domain.models import QueryPlan
from app.planner.hcx import (
    HCXConfigurationError,
    HCXFinishReasonError,
    HCXRetryExhausted,
    HCXStructuredPlanner,
)
from app.planner.schema import HCX_QUERY_PLAN_SCHEMA

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["rank"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    },
    "required": ["intent", "limit"],
}


class TinyPlan(BaseModel):
    intent: Literal["rank"]
    limit: int


def _success_body(*, finish_reason: str = "stop") -> dict[str, object]:
    return {
        "status": {"code": "20000", "message": "OK"},
        "result": {
            "message": {
                "role": "assistant",
                "content": json.dumps({"intent": "rank", "limit": 5}, ensure_ascii=False),
            },
            "finishReason": finish_reason,
            "usage": {
                "promptTokens": 42,
                "completionTokens": 12,
                "totalTokens": 54,
            },
        },
    }


def test_native_hcx_success_uses_bearer_and_pydantic_validation() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        seen["request_id"] = request.headers.get("X-NCP-CLOVASTUDIO-REQUEST-ID")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_success_body())

    async def scenario() -> TinyPlan:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            planner = HCXStructuredPlanner(
                api_key="test-secret",
                client=client,
                request_id_factory=lambda: "request-123",
            )
            return await planner.create_plan(
                question="1년 수익률이 높은 국내 ETF 5개",
                schema=SCHEMA,
                validator=TinyPlan,
            )

    plan = asyncio.run(scenario())
    assert plan == TinyPlan(intent="rank", limit=5)
    assert seen["url"] == ("https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-007")
    assert seen["authorization"] == "Bearer test-secret"
    assert seen["request_id"] == "request-123"

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["thinking"] == {"effort": "none"}
    assert body["responseFormat"] == {"type": "json", "schema": SCHEMA}
    assert body["messages"][1]["role"] == "user"


def test_human_approved_hcx_model_lock_allows_only_exact_runtime_model() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_success_body())

    async def scenario() -> TinyPlan:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            planner = HCXStructuredPlanner(
                api_key="test-secret",
                model_id="HCX-TEST-APPROVED",
                approved_model_id="HCX-TEST-APPROVED",
                client=client,
            )
            return await planner.create_plan(
                question="승인 모델 잠금 검증",
                schema=SCHEMA,
                validator=TinyPlan,
            )

    assert asyncio.run(scenario()) == TinyPlan(intent="rank", limit=5)
    assert seen["url"] == (
        "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-TEST-APPROVED"
    )

    with pytest.raises(HCXConfigurationError, match="must match APPROVED_HCX_MODEL_ID"):
        HCXStructuredPlanner(
            api_key="test-secret",
            model_id="HCX-RUNTIME",
            approved_model_id="HCX-APPROVED",
        )


def test_length_finish_reason_is_rejected_before_validation() -> None:
    validator_called = False

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_body(finish_reason="length"))

    def validator(_: dict[str, object]) -> TinyPlan:
        nonlocal validator_called
        validator_called = True
        return TinyPlan(intent="rank", limit=5)

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            planner = HCXStructuredPlanner(api_key="test-secret", client=client)
            await planner.create_plan(
                question="국내 ETF를 정렬해 줘",
                schema=SCHEMA,
                validator=validator,
            )

    try:
        asyncio.run(scenario())
    except HCXFinishReasonError as exc:
        assert "length" in str(exc)
    else:
        raise AssertionError("length finishReason must be rejected")
    assert validator_called is False


def test_429_retries_are_bounded_and_error_is_redacted() -> None:
    calls = 0
    delays: list[float] = []
    gated_attempts: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={"x-ratelimit-reset-requests": "0.01s"},
            json={"status": {"code": "42901", "message": "Too many requests"}},
        )

    async def no_wait(delay: float) -> None:
        delays.append(delay)

    async def before_attempt(attempt: int) -> None:
        gated_attempts.append(attempt)

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            planner = HCXStructuredPlanner(
                api_key="test-secret",
                client=client,
                max_retries=2,
                sleep=no_wait,
            )
            await planner.create_plan(
                question="비밀 질문 문자열",
                schema=SCHEMA,
                validator=TinyPlan,
                before_attempt=before_attempt,
            )

    try:
        asyncio.run(scenario())
    except HCXRetryExhausted as exc:
        assert exc.status_code == 429
        assert exc.attempts == 3
        assert "test-secret" not in str(exc)
        assert "비밀 질문 문자열" not in str(exc)
    else:
        raise AssertionError("bounded retry budget must be enforced")

    assert calls == 3
    assert delays == [0.01, 0.01]
    assert gated_attempts == [0, 1, 2]


def test_transient_transport_error_retries_then_succeeds_without_leaking_question() -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("private transport detail", request=request)
        return httpx.Response(200, json=_success_body())

    async def no_wait(delay: float) -> None:
        delays.append(delay)

    async def scenario() -> TinyPlan:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            planner = HCXStructuredPlanner(
                api_key="test-secret",
                client=client,
                max_retries=2,
                sleep=no_wait,
            )
            return await planner.create_plan(
                question="비공개 평가 질문",
                schema=SCHEMA,
                validator=TinyPlan,
            )

    assert asyncio.run(scenario()) == TinyPlan(intent="rank", limit=5)
    assert calls == 2
    assert delays == [0.5]


def test_transient_transport_error_exhaustion_is_bounded_and_redacted() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("비공개 평가 질문", request=request)

    async def no_wait(_: float) -> None:
        return None

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            planner = HCXStructuredPlanner(
                api_key="test-secret",
                client=client,
                max_retries=2,
                sleep=no_wait,
            )
            await planner.create_plan(
                question="비공개 평가 질문",
                schema=SCHEMA,
                validator=TinyPlan,
            )

    try:
        asyncio.run(scenario())
    except HCXRetryExhausted as exc:
        assert exc.status_code is None
        assert exc.attempts == 3
        assert "비공개 평가 질문" not in str(exc)
        assert "test-secret" not in str(exc)
    else:
        raise AssertionError("transport retries must be bounded")
    assert calls == 3


def test_retry_after_numeric_seconds_is_respected() -> None:
    delays: list[float] = []
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "1.25"})
        return httpx.Response(200, json=_success_body())

    async def no_wait(delay: float) -> None:
        delays.append(delay)

    async def scenario() -> TinyPlan:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            planner = HCXStructuredPlanner(
                api_key="test-secret",
                client=client,
                max_retries=1,
                sleep=no_wait,
            )
            return await planner.create_plan(
                question="국내 ETF",
                schema=SCHEMA,
                validator=TinyPlan,
            )

    assert asyncio.run(scenario()) == TinyPlan(intent="rank", limit=5)
    assert delays == [1.25]


def test_production_query_schema_does_not_use_unsupported_null_type() -> None:
    def walk(value: object) -> None:
        if isinstance(value, dict):
            assert value.get("type") != "null"
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(HCX_QUERY_PLAN_SCHEMA)


def test_full_production_schema_round_trips_a_valid_aggregate_plan() -> None:
    payload = {
        "version": "1.1",
        "intent": "aggregate",
        "scopes": ["overseas_etp"],
        "entities": [],
        "filter_groups": [
            {
                "join": "AND",
                "conditions": [
                    {
                        "field": "product.trading_currency",
                        "op": "eq",
                        "value": "USD",
                        "value2": "",
                        "unit": "",
                    }
                ],
            }
        ],
        "groups_join": "AND",
        "metrics": ["overseas_etp.aum_last"],
        "aggregations": [
            {
                "function": "avg",
                "field": "overseas_etp.aum_last",
                "alias": "average_aum",
                "distinct": False,
            }
        ],
        "sort": [],
        "group_by": [],
        "limit": 10,
        "assumptions": [],
        "needs_clarification": False,
        "clarification_question": "",
        "missing_slots": [],
        "clarification_options": [],
    }

    def handler(_: httpx.Request) -> httpx.Response:
        body = _success_body()
        body["result"]["message"]["content"] = json.dumps(payload, ensure_ascii=False)
        return httpx.Response(200, json=body)

    async def scenario() -> QueryPlan:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            planner = HCXStructuredPlanner(api_key="test-secret", client=client)
            return await planner.create_plan(
                question="거래통화 USD 해외 ETF의 평균 AUM",
                schema=HCX_QUERY_PLAN_SCHEMA,
                validator=QueryPlan,
            )

    plan = asyncio.run(scenario())
    assert plan.aggregations[0].alias == "average_aum"
    assert plan.metrics == ["overseas_etp.aum_last"]


def test_hcx_clarification_options_are_provider_bounded_to_four() -> None:
    # Flat array (no anyOf) so provider-side constrained decoding stays
    # portable; the exact 0-or-2..4 rule is enforced locally by QueryPlan.
    option_schema = HCX_QUERY_PLAN_SCHEMA["properties"]["clarification_options"]
    assert option_schema["type"] == "array"
    assert option_schema["maxItems"] == 4
    assert option_schema["items"]["required"] == ["value", "label", "description"]
