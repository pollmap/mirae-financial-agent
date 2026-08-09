from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.domain.models import QueryPlan
from app.execution.engine import DuckDBEngine
from app.planner.deterministic import DeterministicPlanner
from app.rendering import render_answer
from app.service import AgentService

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"


class CountingPlanner:
    name = "counting"

    def __init__(self, plan: QueryPlan) -> None:
        self.result = plan
        self.calls = 0

    async def plan(self, question: str) -> QueryPlan:
        self.calls += 1
        return self.result


class SequencePlanner:
    name = "sequence"

    def __init__(self, *plans: QueryPlan) -> None:
        self._plans = list(plans)
        self.calls = 0

    async def plan(self, question: str) -> QueryPlan:
        plan = self._plans[self.calls]
        self.calls += 1
        return plan


def _service(plan: QueryPlan) -> tuple[AgentService, CountingPlanner]:
    planner = CountingPlanner(plan)
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        planner,
        DuckDBEngine(DATABASE),
    )
    return service, planner


def _rank_plan(*metrics: str, scope: str = "domestic_etp") -> QueryPlan:
    return QueryPlan(
        intent="rank",
        scopes=[scope],
        metrics=list(metrics),
        sort=[
            {"field": metric, "direction": "desc", "nulls": "last"}
            for metric in metrics
        ],
        limit=3,
    )


def _deterministic_service() -> AgentService:
    return AgentService(
        Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="deterministic",
            enable_clarification_state=True,
            clarification_signing_key="a-long-enough-development-signing-key",
        ),
        DeterministicPlanner(),
        DuckDBEngine(DATABASE),
    )


def _stateful_sequence_service(*plans: QueryPlan) -> AgentService:
    return AgentService(
        Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="deterministic",
            enable_clarification_state=True,
            clarification_signing_key="a-long-enough-development-signing-key",
        ),
        SequencePlanner(*plans),
        DuckDBEngine(DATABASE),
    )


def test_material_party_region_asset_and_currency_conditions_are_never_dropped() -> None:
    service = _deterministic_service()
    try:
        response = asyncio.run(
            service.answer(
                question_id="condition-ledger-party",
                question="미래에셋이 운용하는 미국 주식형 국내 ETF 중 AUM 상위 3개",
            )
        )
        context = json.loads(response.retrieved_context)
        assert context["answerability"] == "PARTIAL_WITH_COVERAGE"
        assert context["result_count"] == 3
        assert {entry["kind"] for entry in context["condition_ledger"]} >= {
            "scope",
            "intent",
            "party",
            "region",
            "asset_type",
            "product_type",
            "metric",
            "currency",
        }
        assert all(
            entry["status"] == "grounded" for entry in context["condition_ledger"]
        )
        graph_reasons = {
            trace["reason"]
            for trace in context["retrieval_trace"]
            if trace["channel"] == "graph"
        }
        assert any("product.manager" in reason for reason in graph_reasons)
        assert any("product.region" in reason for reason in graph_reasons)
        assert "CURR_CD_KRW" in response.answer
    finally:
        asyncio.run(service.aclose())


def test_strategy_similarity_uses_bm25_instead_of_returning_first_catalog_rows() -> None:
    service = _deterministic_service()
    try:
        response = asyncio.run(
            service.answer(
                question_id="condition-ledger-strategy",
                question="배당 인컴 전략과 비슷한 해외 ETF 5개",
            )
        )
        context = json.loads(response.retrieved_context)
        assert context["answerability"] in {"FULL", "PARTIAL_WITH_COVERAGE"}
        assert context["result_count"] == 5
        assert any(
            entry["kind"] == "strategy"
            and entry["status"] == "grounded"
            and entry["grounded_fields"] == ["product.strategy"]
            for entry in context["condition_ledger"]
        )
        assert any(
            trace["channel"] == "lexical"
            and trace["status"] == "used"
            and trace["candidate_count"] > 0
            for trace in context["retrieval_trace"]
        )
        assert "Alternative Access First Priority" not in response.answer
    finally:
        asyncio.run(service.aclose())


def _preservation_fixture() -> tuple[str, QueryPlan, QueryPlan]:
    question = "KR101501DA16의 확인된 조건으로 조회해줘"
    unresolved = QueryPlan(
        intent="search",
        scopes=["bond"],
        entities=[{"scope": "bond", "code": "KR101501DA16"}],
        filter_groups=[
            {
                "conditions": [
                    {"field": "product.currency", "op": "eq", "value": "KRW"}
                ]
            }
        ],
        metrics=["bond.coupon_rate"],
        limit=3,
    )
    clarification = QueryPlan(
        intent="clarify",
        scopes=["bond"],
        needs_clarification=True,
        clarification_question="결과를 어떤 형태로 확인할까요?",
        missing_slots=["result_format"],
        clarification_options=[
            {"value": "간단히", "label": "간단히"},
            {"value": "자세히", "label": "자세히"},
        ],
        preserved_plan={
            "original_question": question,
            "unresolved_plan": unresolved.model_dump(mode="json"),
        },
    )
    return question, unresolved, clarification


def test_explicit_return_period_corrects_wrong_generated_suffix() -> None:
    service, _ = _service(_rank_plan("domestic_etp.return_1y"))
    guarded = service._enforce_required_clarification(
        "국내 ETF 3개월 수익률 높은 3개", _rank_plan("domestic_etp.return_1y")
    )
    assert guarded.intent == "rank"
    assert guarded.metrics == ["domestic_etp.return_3m"]
    assert [item.field for item in guarded.sort] == ["domestic_etp.return_3m"]


def test_missing_or_multiple_return_periods_are_not_guessed() -> None:
    service, _ = _service(_rank_plan("domestic_etp.return_1y"))
    missing = service._enforce_required_clarification(
        "성과 좋은 국내 ETF 3개", _rank_plan("domestic_etp.return_1y")
    )
    multiple = service._enforce_required_clarification(
        "국내 ETF 1개월과 1년 수익률 순위", _rank_plan("domestic_etp.return_1y")
    )
    assert missing.missing_slots == ["return_period"]
    assert multiple.missing_slots == ["return_period_priority"]
    assert {item.value for item in multiple.clarification_options} == {"1m", "1y"}


def test_explicit_priority_reorders_metrics_and_standalone_connector_does_not_bypass() -> None:
    plan = _rank_plan("domestic_etp.return_1y", "domestic_etp.expense_ratio")
    service, _ = _service(plan)
    reordered = service._enforce_required_clarification(
        "국내 ETF는 보수 우선, 1년 수익률은 그 다음으로 정렬", plan
    )
    ambiguous = service._enforce_required_clarification(
        "국내 ETF 1년 수익률과 보수, 그 다음 결과를 알려줘", plan
    )
    assert reordered.metrics == ["domestic_etp.expense_ratio", "domestic_etp.return_1y"]
    assert [item.field for item in reordered.sort] == reordered.metrics
    assert ambiguous.missing_slots == ["ranking_priority"]


def test_explicit_listing_market_overrides_wrong_etp_scope_but_investment_region_does_not() -> None:
    overseas_question = "해외상장 ETF 중 1개월 수익률 높은 3개"
    service, _ = _service(_rank_plan("domestic_etp.return_1m"))
    corrected = service._enforce_required_clarification(
        overseas_question, _rank_plan("domestic_etp.return_1m")
    )
    assert corrected.scopes == ["overseas_etp"]
    assert corrected.metrics == ["overseas_etp.return_1m"]

    preflight = service._preflight_clarification("미국 주식에 투자하는 ETF 3개")
    assert preflight is not None
    assert preflight.missing_slots == ["market"]


def test_etf_subtype_is_injected_into_every_or_branch() -> None:
    plan = QueryPlan(
        intent="search",
        scopes=["overseas_etp"],
        filter_groups=[
            {"conditions": [{"field": "product.manager", "op": "eq", "value": "A"}]},
            {
                "conditions": [
                    {"field": "product.investment_region", "op": "eq", "value": "B"}
                ]
            },
        ],
        groups_join="OR",
    )
    service, _ = _service(plan)
    guarded = service._enforce_required_clarification("해외 ETF를 찾아줘", plan)
    for group in guarded.filter_groups:
        assert any(
            condition.field == "product.internal_type" and condition.value == "ETF"
            for condition in group.conditions
        )


def test_obvious_market_ambiguity_does_not_spend_a_planner_call() -> None:
    service, planner = _service(_rank_plan("domestic_etp.return_1y"))
    response = asyncio.run(
        service.answer(question_id="PREFLIGHT", question="수익률 높은 ETF 3개")
    )
    context = json.loads(response.retrieved_context)
    assert context["answerability"] == "NEEDS_CLARIFICATION"
    assert context["clarification"]["missing_slots"] == ["market"]
    assert planner.calls == 0


def test_overseas_missing_return_period_is_unavailable_without_fake_options_or_hcx_call() -> None:
    service, planner = _service(_rank_plan("overseas_etp.return_1y", scope="overseas_etp"))
    response = asyncio.run(
        service.answer(
            question_id="OVERSEAS-NO-RETURN",
            question="해외 ETF 수익률 높은 상품 3개 알려줘",
        )
    )
    context = json.loads(response.retrieved_context)
    assert context["answerability"] == "UNAVAILABLE"
    assert context["reason_code"] == "RETURN_METRICS_UNAVAILABLE"
    assert context["clarification"] is None
    assert "AUM" in response.answer
    assert planner.calls == 0


def test_hcx_cross_count_plan_is_canonicalized_before_subtype_injection() -> None:
    generated = QueryPlan(
        intent="clarify",
        scopes=[],
        needs_clarification=True,
        clarification_question="무엇을 셀까요?",
        missing_slots=["scope"],
        clarification_options=[
            {"value": "domestic_etp", "label": "국내 ETF"},
            {"value": "fund", "label": "공모펀드"},
        ],
    )
    service, _ = _service(generated)
    guarded = service._enforce_required_clarification(
        "국내 ETF와 공모펀드 상품은 각각 몇 개인가?", generated
    )
    assert guarded.intent == "aggregate"
    assert guarded.group_by == ["product.scope"]
    assert guarded.groups_join == "OR"
    fund_branch = next(
        group
        for group in guarded.filter_groups
        if any(condition.field == "product.public_private" for condition in group.conditions)
    )
    assert all(
        condition.field != "product.internal_type" for condition in fund_branch.conditions
    )


def test_scope_clarification_preserves_product_family_count_intent() -> None:
    async def scenario() -> None:
        service = _deterministic_service()
        question = "상품군별 상품 수를 알려줘."

        first = await service.answer(question_id="SCOPE-COUNT-1", question=question)
        first_context = json.loads(first.retrieved_context)
        clarification = first_context["clarification"]

        assert first_context["answerability"] == "NEEDS_CLARIFICATION"
        assert clarification["missing_slots"] == ["scope"]

        final = await service.answer(
            question_id="SCOPE-COUNT-2",
            question=question,
            clarification_token=clarification["clarification_token"],
            clarification_response="fund",
        )
        final_context = json.loads(final.retrieved_context)

        assert final_context["answerability"] in {"FULL", "PARTIAL_WITH_COVERAGE"}
        assert final_context["result_count"] == 1
        assert len(final_context["aggregates"]) == 1
        assert int(final_context["aggregates"][0]["value"]) == 11115
        assert "intent=aggregate" in final.think_trace
        assert "None 집계" not in final.answer
        assert "개별 원천 기준일" not in final.answer
        assert any(
            entry["kind"] == "intent"
            and entry["requested_text"] == "aggregate"
            and entry["status"] == "grounded"
            for entry in final_context["condition_ledger"]
        )

    asyncio.run(scenario())


def test_public_fund_numeric_risk_grade_is_mapped_to_official_source_label() -> None:
    async def scenario() -> None:
        service = _deterministic_service()
        response = await service.answer(
            question_id="FUND-RISK-GRADE-1",
            question="위험등급이 1등급인 공모펀드는 몇 개야?",
        )
        context = json.loads(response.retrieved_context)

        assert context["answerability"] in {"FULL", "PARTIAL_WITH_COVERAGE"}
        assert context["reason_code"] != "CATALOG_VALUE_UNAVAILABLE"
        assert len(context["aggregates"]) == 1
        assert int(context["aggregates"][0]["value"]) == 670
        risk_entry = next(
            entry
            for entry in context["condition_ledger"]
            if entry["kind"] == "risk_grade"
        )
        assert risk_entry["status"] == "grounded"
        assert risk_entry["requested_text"] == "매우 높은 위험"

    asyncio.run(scenario())


def test_hcx_catalog_filter_values_are_replaced_with_scope_source_labels() -> None:
    generated = QueryPlan(
        intent="search",
        scopes=["overseas_etp"],
        filter_groups=[
            {
                "conditions": [
                    {"field": "product.asset_type", "op": "eq", "value": "주식"},
                    {"field": "product.region", "op": "eq", "value": "미국"},
                ]
            }
        ],
    )
    service, _ = _service(generated)
    guarded = service._enforce_required_clarification(
        "미국 주식에 투자하는 해외 ETF 5개 보여줘", generated
    )
    values = {
        (condition.field, condition.value)
        for group in guarded.filter_groups
        for condition in group.conditions
    }
    assert ("product.asset_type", "Equity") in values
    assert ("product.region", "United States of America") in values
    assert ("product.asset_type", "주식") not in values
    assert ("product.region", "미국") not in values

    risk_generated = QueryPlan(
        intent="search",
        scopes=["domestic_etp"],
        metrics=["domestic_etp.risk_grade"],
    )
    risk_guarded = service._enforce_required_clarification(
        "위험등급 3등급 국내 ETF 5개 보여줘", risk_generated
    )
    assert risk_guarded.metrics == []
    assert any(
        condition.field == "product.risk_grade"
        and condition.value == "다소높은위험(3등급)"
        for group in risk_guarded.filter_groups
        for condition in group.conditions
    )


def test_hcx_explain_without_product_target_is_canonicalized_to_clarification() -> None:
    generated = QueryPlan(intent="explain", scopes=["overseas_etp"])
    service, _ = _service(generated)
    guarded = service._enforce_required_clarification(
        "해외 ETF 상품 정보와 운용전략을 설명해줘", generated
    )
    assert guarded.intent == "clarify"
    assert guarded.missing_slots == ["explanation_target"]
    assert len(guarded.clarification_options) == 2


def test_hcx_exact_catalog_filter_does_not_pollute_return_rank_metrics() -> None:
    generated = QueryPlan(
        intent="rank",
        scopes=["domestic_etp"],
        metrics=["domestic_etp.return_1y", "domestic_etp.risk_grade"],
        sort=[
            {"field": "domestic_etp.return_1y", "direction": "desc"},
            {"field": "domestic_etp.risk_grade", "direction": "desc"},
        ],
    )
    service, _ = _service(generated)
    guarded = service._enforce_required_clarification(
        "위험등급 3등급 국내 ETF 중 1년 수익률 높은 3개", generated
    )
    assert guarded.intent == "rank"
    assert guarded.metrics == ["domestic_etp.return_1y"]
    assert [item.field for item in guarded.sort] == ["domestic_etp.return_1y"]
    assert any(
        condition.field == "product.risk_grade"
        and condition.value == "다소높은위험(3등급)"
        for group in guarded.filter_groups
        for condition in group.conditions
    )


def test_disabled_clarification_state_does_not_require_a_signing_key() -> None:
    planner = CountingPlanner(_rank_plan("domestic_etp.return_1y"))
    service = AgentService(
        Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="deterministic",
            enable_clarification_state=False,
            clarification_signing_key="",
        ),
        planner,
        DuckDBEngine(DATABASE),
    )
    response = asyncio.run(
        service.answer(question_id="NO-STATE", question="수익률 높은 ETF 3개")
    )
    context = json.loads(response.retrieved_context)
    assert context["clarification"]["clarification_token"] is None


def test_returned_market_and_period_option_values_reach_final_result_verbatim() -> None:
    async def scenario() -> None:
        service = _deterministic_service()
        question = "수익률 높은 ETF 3개 골라줘"

        first = await service.answer(question_id="ROUNDTRIP-1", question=question)
        first_context = json.loads(first.retrieved_context)
        market = first_context["clarification"]
        assert market["missing_slots"] == ["market"]
        assert "domestic_etp" in {option["value"] for option in market["options"]}

        second = await service.answer(
            question_id="ROUNDTRIP-2",
            question=question,
            clarification_token=market["clarification_token"],
            clarification_response="domestic_etp",
        )
        second_context = json.loads(second.retrieved_context)
        period = second_context["clarification"]
        assert period["missing_slots"] == ["return_period"]
        assert "1y" in {option["value"] for option in period["options"]}

        final = await service.answer(
            question_id="ROUNDTRIP-3",
            question=question,
            clarification_token=period["clarification_token"],
            clarification_response="1y",
        )
        final_context = json.loads(final.retrieved_context)
        assert final_context["answerability"] == "PARTIAL_WITH_COVERAGE"
        assert final_context["result_count"] == 3
        assert final_context["clarification"] is None

    asyncio.run(scenario())


def test_returned_metric_option_values_reach_rank_and_compare_results_verbatim() -> None:
    async def scenario() -> None:
        service = _deterministic_service()
        rank_question = (
            "거래통화 USD인 해외 ETF 중 종가는 높고 거래량은 많은 3개 알려줘"
        )
        rank_first = await service.answer(
            question_id="RANK-ROUNDTRIP-1", question=rank_question
        )
        rank_context = json.loads(rank_first.retrieved_context)
        rank_clarification = rank_context["clarification"]
        assert "overseas_etp.volume_1d" in {
            option["value"] for option in rank_clarification["options"]
        }
        rank_final = await service.answer(
            question_id="RANK-ROUNDTRIP-2",
            question=rank_question,
            clarification_token=rank_clarification["clarification_token"],
            clarification_response="overseas_etp.volume_1d",
        )
        rank_final_context = json.loads(rank_final.retrieved_context)
        assert rank_final_context["answerability"] == "PARTIAL_WITH_COVERAGE"
        assert rank_final_context["result_count"] == 3
        assert rank_final_context["clarification"] is None

        compare_question = "국내채권 KR101501DA16과 KR101501DA24 비교해줘"
        compare_first = await service.answer(
            question_id="COMPARE-ROUNDTRIP-1", question=compare_question
        )
        compare_context = json.loads(compare_first.retrieved_context)
        compare_clarification = compare_context["clarification"]
        assert "bond.coupon_rate" in {
            option["value"] for option in compare_clarification["options"]
        }
        compare_final = await service.answer(
            question_id="COMPARE-ROUNDTRIP-2",
            question=compare_question,
            clarification_token=compare_clarification["clarification_token"],
            clarification_response="bond.coupon_rate",
        )
        compare_final_context = json.loads(compare_final.retrieved_context)
        assert compare_final_context["answerability"] == "PARTIAL_WITH_COVERAGE"
        assert compare_final_context["result_count"] == 2
        assert compare_final_context["clarification"] is None

    asyncio.run(scenario())


def test_source_strategy_forecast_words_do_not_trigger_generated_advice_guard() -> None:
    async def scenario() -> None:
        service = _deterministic_service()
        response = await service.answer(
            question_id="SOURCE-TEXT-POLICY-1",
            question="해외 ETF 티커 ABEQ.K의 공식 원본 상세 정보를 확인해줘.",
        )
        context = json.loads(response.retrieved_context)

        assert context["answerability"] == "FULL"
        assert context["result_count"] == 1
        assert "will yield positive absolute returns" in response.answer

    asyncio.run(scenario())


def test_follow_up_safely_restores_dropped_scope_entity_filter_and_metric() -> None:
    async def scenario() -> None:
        question, _, clarification = _preservation_fixture()
        dropped = QueryPlan(intent="search", scopes=[])
        service = _stateful_sequence_service(clarification, dropped)

        first = await service.answer(question_id="PRESERVE-1", question=question)
        clarification_context = json.loads(first.retrieved_context)["clarification"]
        final = await service.answer(
            question_id="PRESERVE-2",
            question=question,
            clarification_token=clarification_context["clarification_token"],
            clarification_response="간단히",
        )
        context = json.loads(final.retrieved_context)

        assert context["reason_code"] != "CLARIFICATION_STATE_CONFLICT"
        assert context["result_count"] == 1
        assert "scopes=bond" in final.think_trace
        assert "metrics=bond.coupon_rate" in final.think_trace
        assert "filter_fields=product.currency" in final.think_trace
        assert context["items"][0]["product_uid"] == "BOND:PRBD01N001:KR101501DA16"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changed_plan",
    [
        QueryPlan(
            intent="search",
            scopes=["fund"],
            entities=[{"scope": "bond", "code": "KR101501DA16"}],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.currency", "op": "eq", "value": "KRW"}
                    ]
                }
            ],
            metrics=["bond.coupon_rate"],
        ),
        QueryPlan(
            intent="search",
            scopes=["bond"],
            entities=[{"scope": "bond", "code": "KR101501DA24"}],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.currency", "op": "eq", "value": "KRW"}
                    ]
                }
            ],
            metrics=["bond.coupon_rate"],
        ),
        QueryPlan(
            intent="search",
            scopes=["bond"],
            entities=[{"scope": "bond", "code": "KR101501DA16"}],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.currency", "op": "eq", "value": "USD"}
                    ]
                }
            ],
            metrics=["bond.coupon_rate"],
        ),
        QueryPlan(
            intent="search",
            scopes=["bond"],
            entities=[{"scope": "bond", "code": "KR101501DA16"}],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.currency", "op": "eq", "value": "KRW"}
                    ]
                }
            ],
            metrics=["bond.buy_yield"],
        ),
    ],
    ids=["scope", "entity", "filter", "metric"],
)
def test_follow_up_conflicting_preserved_constraints_fail_closed(
    changed_plan: QueryPlan,
) -> None:
    async def scenario() -> None:
        question, _, clarification = _preservation_fixture()
        service = _stateful_sequence_service(clarification, changed_plan)

        first = await service.answer(question_id="CONFLICT-1", question=question)
        clarification_context = json.loads(first.retrieved_context)["clarification"]
        blocked = await service.answer(
            question_id="CONFLICT-2",
            question=question,
            clarification_token=clarification_context["clarification_token"],
            clarification_response="간단히",
        )
        context = json.loads(blocked.retrieved_context)

        assert context["answerability"] == "SAFETY_LIMITED"
        assert context["reason_code"] == "CLARIFICATION_STATE_CONFLICT"
        assert context["result_count"] == 0
        assert "후속 계획이 충돌" in blocked.answer

    asyncio.run(scenario())


def test_large_evidence_and_rendered_answer_are_deterministically_bounded() -> None:
    metrics = [
        "bond.deposit_equivalent_yield_154",
        "bond.maturity_date",
        "bond.risk_grade",
        "bond.buy_yield",
        "bond.applied_yield",
        "bond.evaluation_price",
        "bond.corporate_after_tax_yield",
        "bond.coupon_rate",
        "bond.convexity",
        "bond.remaining_days_raw",
        "bond.preferential_tax_yield",
        "bond.after_tax_yield",
    ]
    plan = QueryPlan(
        intent="lookup",
        scopes=["bond"],
        entities=[{"name": "채권", "scope": "bond"}],
        metrics=metrics,
        limit=50,
    )
    engine = DuckDBEngine(DATABASE)
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        CountingPlanner(plan),
        engine,
    )

    raw = engine.execute(plan)
    raw_context = raw.model_dump_json(exclude_none=False)
    raw_answer = render_answer(plan, raw)
    bounded = service._bounded_response_evidence(raw)
    bounded_context = bounded.model_dump_json(exclude_none=False)

    assert len(raw_context) > 100_000
    assert len(raw_answer) <= 30_000
    assert "응답 길이 제한으로 이후 항목을 생략" in raw_answer
    assert len(bounded_context) <= 100_000
    assert 0 < len(bounded.items) < len(raw.items)
    assert bounded.result_count == len(bounded.items)
    assert bounded.answerability.value == "PARTIAL_WITH_COVERAGE"
    assert bounded.reason_code == "RESPONSE_TRUNCATED"
    assert any("응답 크기 제한" in item for item in bounded.limitations)


def test_metric_source_present_count_keeps_default_public_fund_universe() -> None:
    plan = QueryPlan(
        intent="aggregate",
        scopes=["fund"],
        metrics=["fund.return_1y"],
        aggregations=[
            {
                "function": "count",
                "field": "fund.return_1y",
                "alias": "product_count",
                "distinct": True,
            }
        ],
        assumptions=["metric_count_basis=source_present"],
    )
    service, _ = _service(plan)

    guarded = service._ensure_public_fund_universe(plan)

    assert any(
        condition.field == "product.public_private"
        and condition.op == "eq"
        and condition.value == "공모"
        for group in guarded.filter_groups
        for condition in group.conditions
    )
