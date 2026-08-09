from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.domain.models import QueryPlan
from app.execution.engine import DuckDBEngine
from app.main import create_app
from app.planner.hcx import HCXTransportError
from app.planner.service import HcxQueryPlanner
from app.rendering import _format_value
from app.service import AgentService

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"
RESPONSE_KEYS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}


class FixedPlanner:
    name = "fixed"

    def __init__(self, plan: QueryPlan) -> None:
        self._plan = plan

    async def plan(self, _: str) -> QueryPlan:
        return self._plan


def test_settings_bound_public_question_and_runtime_concurrency() -> None:
    with pytest.raises(ValueError, match="QUESTION_MAX_CHARS"):
        Settings(question_max_chars=2001).validate()
    with pytest.raises(ValueError, match="DB_MAX_CONCURRENCY"):
        Settings(db_max_concurrency=0).validate()
    with pytest.raises(ValueError, match="HCX_QPM_LIMIT"):
        Settings(hcx_qpm_limit=181).validate()
    with pytest.raises(ValueError, match="HCX_TPM_BUDGET"):
        Settings(hcx_tpm_budget=1023).validate()
    with pytest.raises(ValueError, match="non-placeholder CLOVA_STUDIO_API_KEY"):
        Settings(
            environment="production",
            planner_mode="hcx",
            clova_studio_api_key="mock-key",
            clarification_signing_key="a" * 24,
        ).validate()


def test_settings_require_exact_human_approved_model_lock_in_production() -> None:
    base = {
        "environment": "production",
        "planner_mode": "hcx",
        "clova_studio_api_key": "realistic-production-key-material",
        "clarification_signing_key": "a" * 24,
    }
    with pytest.raises(ValueError, match="APPROVED_HCX_MODEL_ID"):
        Settings(**base).validate()
    with pytest.raises(ValueError, match="must match APPROVED_HCX_MODEL_ID"):
        Settings(
            **base,
            hcx_model_id="HCX-RUNTIME",
            approved_hcx_model_id="HCX-APPROVED",
        ).validate()

    Settings(
        **base,
        hcx_model_id="HCX-APPROVED",
        approved_hcx_model_id="HCX-APPROVED",
    ).validate()
    with pytest.raises(ValueError, match="24-byte-or-longer"):
        Settings(
            environment="production",
            planner_mode="hcx",
            clova_studio_api_key="realistic-production-key-material",
            clarification_signing_key="too-short",
            approved_hcx_model_id="HCX-007",
        ).validate()


def test_unknown_hcx_policy_reason_is_canonicalized_before_evidence_validation() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        FixedPlanner(
            QueryPlan(
                intent="unsupported",
                scopes=["bond"],
                assumptions=["policy_reason=bad reason with spaces"],
            )
        ),
        DuckDBEngine(DATABASE),
    )
    response = asyncio.run(service.answer(question_id="BAD-REASON", question="채권 요청"))
    context = json.loads(response.retrieved_context)
    assert context["answerability"] == "SAFETY_LIMITED"
    assert context["reason_code"] == "UNSUPPORTED_REQUEST"


def test_cross_scope_count_plan_keeps_each_filter_inside_its_scope_branch() -> None:
    plan = AgentService._cross_scope_count_plan(
        "국내 ETF와 해외 ETN과 공모펀드 상품은 각각 몇 개인가?",
        ["domestic_etp", "overseas_etp", "fund"],
        10,
    )
    by_scope: dict[str, dict[str, object]] = {}
    for group in plan.filter_groups:
        values = {condition.field: condition.value for condition in group.conditions}
        by_scope[str(values["product.scope"])] = values
    assert by_scope["domestic_etp"]["product.internal_type"] == "ETF"
    assert by_scope["overseas_etp"]["product.internal_type"] == "ETN"
    assert by_scope["fund"]["product.public_private"] == "공모"


def test_server_guard_uses_scope_executable_comparison_options() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        FixedPlanner(QueryPlan(intent="unsupported")),
        DuckDBEngine(DATABASE),
    )
    guarded = service._enforce_required_clarification(
        "해외 티커 SPY와 IVV를 비교해줘",
        QueryPlan(
            intent="compare",
            scopes=["overseas_etp"],
            entities=[
                {"code": "SPY", "scope": "overseas_etp"},
                {"code": "IVV", "scope": "overseas_etp"},
            ],
        ),
    )
    assert guarded.missing_slots == ["comparison_metric"]
    assert {option.value for option in guarded.clarification_options} == {
        "overseas_etp.aum_last",
        "overseas_etp.close_price",
        "overseas_etp.volume_1d",
    }


def test_lookup_and_compare_keep_multiple_explicit_return_periods() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        FixedPlanner(QueryPlan(intent="unsupported")),
        DuckDBEngine(DATABASE),
    )
    guarded = service._enforce_required_clarification(
        "국내 ETF A000660과 A005930의 1개월과 1년 수익률을 비교",
        QueryPlan(
            intent="compare",
            scopes=["domestic_etp"],
            entities=[
                {"code": "A000660", "scope": "domestic_etp"},
                {"code": "A005930", "scope": "domestic_etp"},
            ],
            metrics=["domestic_etp.return_1y"],
        ),
    )
    assert guarded.intent == "compare"
    assert guarded.metrics == ["domestic_etp.return_1m", "domestic_etp.return_1y"]


def test_return_period_parser_does_not_shorten_unsupported_longer_periods() -> None:
    assert AgentService._explicit_return_periods("11년 수익률 높은 펀드") == []
    assert AgentService._explicit_return_periods("13개월 수익률") == []
    assert AgentService._explicit_return_periods("15년 수익률") == []
    assert AgentService._explicit_return_periods("1년과 3개월 수익률") == ["3m", "1y"]


def test_catalog_code_formatting_preserves_leading_zeroes() -> None:
    assert _format_value("00040010") == "00040010"
    assert _format_value("00040010", numeric=True) == "40,010"


def test_unexpected_error_uses_redacted_five_field_response() -> None:
    app = create_app(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic")
    )

    async def explode(**_: Any) -> None:
        raise RuntimeError("secret-internal-detail")

    app.state.service.answer = explode

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            return await client.get(
                "/answer", params={"question_id": "ERR-1", "question": "국내채권 조회"}
            )

    response = asyncio.run(request())
    assert response.status_code == 500
    assert set(response.json()) == RESPONSE_KEYS
    assert "secret-internal-detail" not in response.text
    context = json.loads(response.json()["retrieved_context"])
    assert context["reason_code"] == "INTERNAL_EXECUTION_ERROR"


def test_request_metrics_log_only_lengths_without_question_or_linkable_hash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic")
    )
    question = "채권 코드 KR101501DA16 비공개질문문자열"
    caplog.set_level(logging.INFO, logger="mirae.request")

    async def request() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/answer", params={"question_id": "PRIVATE-ID", "question": question}
            )
            assert response.status_code == 200

    asyncio.run(request())
    assert question not in caplog.text
    assert "PRIVATE-ID" not in caplog.text
    event = next(
        json.loads(record.message)
        for record in caplog.records
        if record.name == "mirae.request"
    )
    assert event["question_chars"] == len(question)
    assert event["question_id_chars"] == len("PRIVATE-ID")
    assert "question_hash" not in event
    assert "question_id_hash" not in event


def test_hcx_planner_bounds_simultaneous_calls_before_real_credentials() -> None:
    planner = HcxQueryPlanner(
        Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="hcx",
            clova_studio_api_key="mock-key",
            hcx_timeout_seconds=1,
            hcx_total_deadline_seconds=2,
            hcx_max_retries=1,
            hcx_max_concurrency=2,
            hcx_qpm_limit=180,
            hcx_tpm_budget=1_000_000,
        )
    )
    active = 0
    maximum_active = 0

    async def fake_create_plan(**_: Any) -> QueryPlan:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return QueryPlan(intent="unsupported", scopes=[])

    planner._client.create_plan = fake_create_plan  # type: ignore[method-assign]

    async def scenario() -> None:
        await asyncio.gather(*(planner.plan(f"질문 {index}") for index in range(8)))
        await planner.aclose()

    asyncio.run(scenario())
    assert maximum_active == 2


def test_hcx_total_deadline_includes_concurrency_queue_wait() -> None:
    planner = HcxQueryPlanner(
        Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="hcx",
            clova_studio_api_key="mock-key",
            hcx_timeout_seconds=0.01,
            hcx_total_deadline_seconds=0.02,
            hcx_max_retries=1,
            hcx_max_concurrency=1,
            hcx_qpm_limit=180,
        )
    )
    called = False

    async def fake_create_plan(**_: Any) -> QueryPlan:
        nonlocal called
        called = True
        return QueryPlan(intent="unsupported", scopes=[])

    planner._client.create_plan = fake_create_plan  # type: ignore[method-assign]

    async def scenario() -> None:
        await planner._semaphore.acquire()
        try:
            with pytest.raises(HCXTransportError, match="total request deadline"):
                await planner.plan("대기열 제한 확인")
        finally:
            planner._semaphore.release()
            await planner.aclose()

    asyncio.run(scenario())
    assert called is False


def test_hcx_tpm_budget_fails_closed_before_provider_call() -> None:
    planner = HcxQueryPlanner(
        Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="hcx",
            clova_studio_api_key="mock-key",
            hcx_timeout_seconds=1,
            hcx_total_deadline_seconds=2,
            hcx_max_retries=1,
            hcx_max_concurrency=1,
            hcx_qpm_limit=180,
            hcx_tpm_budget=1_024,
        )
    )
    called = False

    async def fake_create_plan(**_: Any) -> QueryPlan:
        nonlocal called
        called = True
        return QueryPlan(intent="unsupported", scopes=[])

    planner._client.create_plan = fake_create_plan  # type: ignore[method-assign]

    async def scenario() -> None:
        with pytest.raises(HCXTransportError, match="TPM budget"):
            await planner.plan("TPM 제한 확인")
        await planner.aclose()

    asyncio.run(scenario())
    assert called is False
