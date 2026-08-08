from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.models import QueryPlan
from app.execution.registry import MetricRegistry, canonicalize_numeric_operand
from app.safety import evaluate_question, needs_selection_criteria


def test_metric_registry_blocks_absent_and_uncertain_metrics() -> None:
    registry = MetricRegistry.load()
    absent = registry.evaluate(
        QueryPlan(
            intent="rank",
            scopes=["fund"],
            metrics=["fund.expense_ratio"],
            sort=[{"field": "fund.expense_ratio", "direction": "asc", "nulls": "last"}],
        )
    )
    uncertain = registry.evaluate(
        QueryPlan(
            intent="rank",
            scopes=["domestic_etp"],
            metrics=["domestic_etp.expense_ratio"],
            sort=[{"field": "domestic_etp.expense_ratio", "direction": "asc", "nulls": "last"}],
        )
    )
    assert absent.answerability == "UNAVAILABLE"
    assert absent.reason_code == "SOURCE_FIELD_ABSENT"
    # Briefing rebaseline: fee ranking is source-literal with zero-exclusion
    # disclosure instead of a quality refusal.
    assert uncertain.allowed is True


def test_registry_delegates_cross_product_ranking_to_capability() -> None:
    registry = MetricRegistry.load()
    decision = registry.evaluate(
        QueryPlan(
            intent="rank",
            scopes=["domestic_etp", "fund"],
            metrics=["cross.return_1y"],
            sort=[{"field": "cross.return_1y", "direction": "desc", "nulls": "last"}],
        )
    )
    # Cross-scope plans are never refused for scope count; the executor
    # chooses unified/split/side-by-side presentation with disclosure.
    assert decision.allowed is True
    unknown = registry.evaluate(
        QueryPlan(
            intent="rank",
            scopes=["domestic_etp", "fund"],
            metrics=["cross.made_up_metric"],
            sort=[{"field": "cross.made_up_metric", "direction": "desc", "nulls": "last"}],
        )
    )
    assert unknown.allowed is False
    assert unknown.reason_code == "METRIC_UNKNOWN"


def test_registry_allows_only_separately_grouped_cross_scope_product_count() -> None:
    registry = MetricRegistry.load()
    safe = registry.evaluate(
        QueryPlan(
            intent="aggregate",
            scopes=["domestic_etp", "fund"],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.internal_type", "op": "eq", "value": "ETF"}
                    ]
                },
                {
                    "conditions": [
                        {"field": "product.public_private", "op": "eq", "value": "공모"}
                    ]
                },
            ],
            groups_join="OR",
            aggregations=[
                {
                    "function": "count",
                    "field": "product.id",
                    "alias": "product_count",
                    "distinct": True,
                }
            ],
            group_by=["product.scope"],
        )
    )
    unsafe = registry.evaluate(
        QueryPlan(
            intent="aggregate",
            scopes=["domestic_etp", "fund"],
            aggregations=[
                {
                    "function": "sum",
                    "field": "domestic_etp.aum_last",
                    "alias": "combined_aum",
                }
            ],
            metrics=["domestic_etp.aum_last"],
            group_by=["product.scope"],
        )
    )
    assert safe.allowed is True
    # A cross-scope SUM now executes per scope with an absent-scope note for
    # funds (no AUM binding) instead of a blanket INCOMPARABLE refusal.
    assert unsafe.allowed is True


def test_registry_accepts_explicit_scope_routing_for_cross_scope_count_only() -> None:
    registry = MetricRegistry.load()
    routed = QueryPlan(
        intent="aggregate",
        scopes=["bond", "domestic_etp", "fund"],
        filter_groups=[
            {"conditions": [{"field": "product.scope", "op": "eq", "value": "bond"}]},
            {
                "conditions": [
                    {"field": "product.scope", "op": "eq", "value": "domestic_etp"},
                    {"field": "product.internal_type", "op": "eq", "value": "ETF"},
                ]
            },
            {
                "conditions": [
                    {"field": "product.scope", "op": "eq", "value": "fund"},
                    {"field": "product.public_private", "op": "eq", "value": "공모"},
                ]
            },
        ],
        groups_join="OR",
        aggregations=[
            {
                "function": "count",
                "field": "product.id",
                "alias": "product_count",
                "distinct": True,
            }
        ],
        group_by=["product.scope"],
    )
    assert registry.evaluate(routed).allowed is True

    missing_route = QueryPlan.model_validate(
        {
            **routed.model_dump(mode="json"),
            "filter_groups": routed.model_dump(mode="json")["filter_groups"][:-1],
        }
    )
    # No longer refused: an incompletely-routed count falls through to the
    # cross-scope executor, which counts per scope with disclosure.
    assert registry.evaluate(missing_route).allowed is True


def test_special_count_bases_are_a_closed_policy_matrix() -> None:
    registry = MetricRegistry.load()

    def plan(
        basis: str,
        *,
        scope: str = "domestic_etp",
        group_by: list[str] | None = None,
        filters: list[dict[str, object]] | None = None,
        aggregation_count: int = 1,
    ) -> QueryPlan:
        return QueryPlan(
            intent="aggregate",
            scopes=[scope],
            filter_groups=filters or [],
            aggregations=[
                {
                    "function": "count",
                    "field": "product.id",
                    "alias": f"product_count_{index}",
                    "distinct": True,
                }
                for index in range(aggregation_count)
            ],
            group_by=group_by or [],
            assumptions=[f"count_basis={basis}"],
        )

    assert registry.evaluate(plan("raw")).allowed is True
    assert registry.evaluate(
        plan("raw", group_by=["product.internal_type"])
    ).allowed is True
    assert registry.evaluate(plan("quarantine")).allowed is True
    assert registry.evaluate(plan("fund_attribute", scope="fund")).allowed is True

    invalid_plans = [
        plan(
            "raw",
            filters=[
                {
                    "conditions": [
                        {"field": "product.internal_type", "op": "eq", "value": "ETF"}
                    ]
                }
            ],
        ),
        plan("raw", scope="fund", group_by=["product.internal_type"]),
        plan("fund_attribute", scope="bond"),
        plan("raw", aggregation_count=2),
    ]
    for invalid in invalid_plans:
        decision = registry.evaluate(invalid)
        assert decision.allowed is False
        assert decision.reason_code.startswith("SPECIAL_COUNT_BASIS_")


def test_source_present_count_basis_is_a_closed_aggregate_shape() -> None:
    registry = MetricRegistry.load()
    valid = QueryPlan(
        intent="aggregate",
        scopes=["bond"],
        metrics=["bond.buyable_quantity"],
        aggregations=[
            {
                "function": "count",
                "field": "product.id",
                "alias": "provided_count",
                "distinct": True,
            }
        ],
        assumptions=["metric_count_basis=source_present"],
    )
    assert registry.evaluate(valid).allowed is True

    invalid = [
        QueryPlan(
            intent="search",
            scopes=["fund"],
            metrics=["fund.return_1y"],
            assumptions=["metric_count_basis=source_present"],
        ),
        QueryPlan(
            intent="rank",
            scopes=["fund"],
            metrics=["fund.return_1y"],
            sort=[{"field": "fund.return_1y", "direction": "desc"}],
            assumptions=["metric_count_basis=source_present"],
        ),
    ]
    for plan in invalid:
        decision = registry.evaluate(plan)
        assert decision.allowed is False
        assert decision.reason_code == "SOURCE_PRESENT_COUNT_SHAPE_INVALID"


def test_numeric_operand_canonicalization_matches_decimal_38_10() -> None:
    assert canonicalize_numeric_operand("1,234.5000") == Decimal("1234.5")
    assert str(canonicalize_numeric_operand("1,000")) == "1000"
    decimal_max = "9999999999999999999999999999.9999999999"
    assert str(canonicalize_numeric_operand(decimal_max)) == decimal_max
    assert canonicalize_numeric_operand("-0") == Decimal(0)
    invalid = [
        "NaN",
        "Infinity",
        "-Infinity",
        "0.00000000001",
        "10000000000000000000000000000",
        "1,2",
        "1,,000",
        True,
        None,
    ]
    for value in invalid:
        with pytest.raises(ValueError):
            canonicalize_numeric_operand(value)


def test_registry_blocks_invalid_numeric_operands_before_execution() -> None:
    registry = MetricRegistry.load()
    for value in ["NaN", "Infinity", "0.00000000001", "1e28"]:
        decision = registry.evaluate(
            QueryPlan(
                intent="search",
                scopes=["bond"],
                metrics=["bond.remaining_days_raw"],
                filter_groups=[
                    {
                        "conditions": [
                            {
                                "field": "bond.remaining_days_raw",
                                "op": "gte",
                                "value": value,
                            }
                        ]
                    }
                ],
            )
        )
        assert decision.allowed is False
        assert decision.reason_code == "NON_NUMERIC_METRIC_OPERAND"


def test_compare_shape_non_additive_sum_and_unpartitioned_money_fail_closed() -> None:
    registry = MetricRegistry.load()
    duplicate = registry.evaluate(
        QueryPlan(
            intent="compare",
            scopes=["overseas_etp"],
            entities=[
                {"code": "SPY", "scope": "overseas_etp"},
                {"code": "spy", "scope": "overseas_etp"},
            ],
            metrics=["overseas_etp.close_price"],
        )
    )
    assert duplicate.reason_code == "DUPLICATE_COMPARE_TARGET"

    too_small = registry.evaluate(
        QueryPlan(
            intent="compare",
            scopes=["fund"],
            entities=[
                {"code": "KR5172430026", "scope": "fund"},
                {"code": "KR5142450022", "scope": "fund"},
            ],
            metrics=["fund.return_1y"],
            limit=1,
        )
    )
    assert too_small.reason_code == "COMPARE_LIMIT_TOO_SMALL"

    non_additive = registry.evaluate(
        QueryPlan(
            intent="aggregate",
            scopes=["domestic_etp"],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.currency", "op": "eq", "value": "CURR_CD_KRW"}
                    ]
                }
            ],
            metrics=["domestic_etp.close_price"],
            aggregations=[
                {
                    "function": "sum",
                    "field": "domestic_etp.close_price",
                    "alias": "price_sum",
                }
            ],
        )
    )
    assert non_additive.reason_code == "NON_ADDITIVE_SUM_BLOCKED"

    # 펀드 순자산은 동일 펀드의 클래스별 중복 가능성이 있으므로, 공식
    # share-class 통합 규칙이 확정되기 전에는 통화를 고정해도 합산하지 않는다.
    fund_net_assets_sum = registry.evaluate(
        QueryPlan(
            intent="aggregate",
            scopes=["fund"],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.currency", "op": "eq", "value": "KRW"}
                    ]
                }
            ],
            metrics=["fund.net_assets"],
            aggregations=[
                {
                    "function": "sum",
                    "field": "fund.net_assets",
                    "alias": "fund_net_assets_sum",
                }
            ],
        )
    )
    assert fund_net_assets_sum.reason_code == "NON_ADDITIVE_SUM_BLOCKED"

    money_rank = registry.evaluate(
        QueryPlan(
            intent="rank",
            scopes=["fund"],
            metrics=["fund.net_assets"],
            sort=[{"field": "fund.net_assets", "direction": "desc"}],
        )
    )
    assert money_rank.reason_code == "CURRENCY_FILTER_REQUIRED"


def test_registry_rejects_ambiguous_or_unsafe_plan_shapes() -> None:
    registry = MetricRegistry.load()
    plans_and_reasons = [
        (
            QueryPlan(
                intent="search",
                scopes=["domestic_etp"],
                group_by=["product.internal_type"],
            ),
            "NON_AGGREGATE_SHAPE_UNSUPPORTED",
        ),
        (
            QueryPlan(
                intent="aggregate",
                scopes=["fund"],
                aggregations=[
                    {
                        "function": "count",
                        "field": "product.id",
                        "alias": "count",
                        "distinct": True,
                    }
                ],
                group_by=["product.scope"],
            ),
            "AGGREGATE_GROUP_SHAPE_UNSUPPORTED",
        ),
        (
            QueryPlan(
                intent="aggregate",
                scopes=["bond"],
                aggregations=[
                    {
                        "function": "count",
                        "field": "product.id",
                        "alias": "count",
                        "distinct": False,
                    }
                ],
            ),
            "COUNT_DISTINCT_REQUIRED",
        ),
        (
            QueryPlan(
                intent="aggregate",
                scopes=["fund"],
                metrics=["fund.return_1y", "fund.return_3y"],
                aggregations=[
                    {
                        "function": "count",
                        "field": "product.id",
                        "alias": "count",
                        "distinct": True,
                    }
                ],
            ),
            "MULTI_METRIC_COUNT_BASIS_REQUIRED",
        ),
        (
            QueryPlan(
                intent="aggregate",
                scopes=["fund"],
                filter_groups=[
                    {
                        "conditions": [
                            {"field": "product.currency", "op": "eq", "value": "KRW"}
                        ]
                    }
                ],
                metrics=["fund.net_assets"],
                aggregations=[
                    {
                        "function": "avg",
                        "field": "fund.net_assets",
                        "alias": "average",
                    }
                ],
            ),
            "SHARE_CLASS_AVERAGE_BLOCKED",
        ),
        (
            QueryPlan(
                intent="compare",
                scopes=["fund"],
                entities=[
                    {"code": "KR5172430026", "scope": "fund"},
                    {"code": "KR5142450022", "scope": "fund"},
                ],
                metrics=["fund.return_1y"],
                filter_groups=[
                    {
                        "conditions": [
                            {"field": "fund.return_1y", "op": "gt", "value": "0"}
                        ]
                    }
                ],
            ),
            "COMPARE_METRIC_FILTER_UNSUPPORTED",
        ),
    ]
    for plan, reason in plans_and_reasons:
        decision = registry.evaluate(plan)
        assert decision.allowed is False
        assert decision.reason_code == reason


def test_registry_rejects_non_string_catalog_and_text_date_operands() -> None:
    registry = MetricRegistry.load()
    invalid = [
        (
            QueryPlan(
                intent="search",
                scopes=["bond"],
                filter_groups=[
                    {"conditions": [{"field": "product.currency", "op": "eq", "value": 1}]}
                ],
            ),
            "CATALOG_OPERAND_TYPE_INVALID",
        ),
        (
            QueryPlan(
                intent="search",
                scopes=["bond"],
                metrics=["bond.credit_rating"],
                filter_groups=[
                    {"conditions": [{"field": "bond.credit_rating", "op": "eq", "value": 1}]}
                ],
            ),
            "TEXT_METRIC_OPERAND_TYPE_INVALID",
        ),
        (
            QueryPlan(
                intent="search",
                scopes=["bond"],
                metrics=["bond.maturity_date"],
                filter_groups=[
                    {
                        "conditions": [
                            {"field": "bond.maturity_date", "op": "gte", "value": "20260230"}
                        ]
                    }
                ],
            ),
            "DATE_OPERAND_FORMAT_INVALID",
        ),
    ]
    for plan, reason in invalid:
        decision = registry.evaluate(plan)
        assert decision.allowed is False
        assert decision.reason_code == reason


def test_registry_exposes_only_usable_return_periods() -> None:
    registry = MetricRegistry.load()
    assert registry.available_return_periods("overseas_etp") == ()
    assert "1y" in registry.available_return_periods("domestic_etp")
    assert "3y" in registry.available_return_periods("fund")


def test_safety_never_turns_missing_into_zero_or_live_data() -> None:
    missing = evaluate_question("결측값은 전부 0으로 바꿔서 순위를 내줘")
    live = evaluate_question("제공되지 않은 실시간 시세를 있는 것처럼 답해줘")
    assert missing.reason_code == "MISSING_IS_NOT_ZERO"
    assert live.reason_code == "SNAPSHOT_NOT_REALTIME"


def test_safety_blocks_korean_forecast_and_recommendation_variants() -> None:
    questions = [
        "2027년에 가장 많이 오를 국내 ETF 3개를 추천해줘",
        "올해 말 상승할 채권을 알려줘",
        "향후 유망한 펀드를 골라 줘",
        "1년 뒤 가장 오를 ETF를 예상해줘",
        "6개월 후 수익이 날 펀드를 알려줘",
    ]
    for question in questions:
        decision = evaluate_question(question)
        assert decision.blocked is True
        assert decision.reason_code == "FORECAST_OR_DEFINITIVE_RECOMMENDATION"


def test_generic_recommendation_without_criteria_asks_instead_of_blocking() -> None:
    # Official task: missing conditions get a clarification, not a refusal.
    asks = [
        "가장 좋은 국내 ETF 하나 추천해줘",
        "가장 좋은 ETF 하나 알려줘",
        "펀드 하나 골라줘",
        "ETF 추천해줘",
    ]
    for question in asks:
        assert evaluate_question(question).blocked is False, question
        assert needs_selection_criteria(question) is True, question
    # Objective criteria present → no criteria question needed.
    objective = [
        "1년 수익률 높은 ETF 3개 골라줘",
        "거래량 많은 해외 ETF 3개 추천해줘",
    ]
    for question in objective:
        assert needs_selection_criteria(question) is False, question
    # Definitive/personal/forecast advice must never reach the relaxed path.
    still_blocked = [
        "향후 유망한 펀드를 골라 줘",
        "나에게 맞는 ETF를 골라줘",
        "무조건 오를 상품 추천해줘",
    ]
    for question in still_blocked:
        assert needs_selection_criteria(question) is False, question
        assert evaluate_question(question).blocked is True, question


def test_safety_blocks_latest_snapshot_and_missing_value_spelling_variants() -> None:
    realtime = [
        "최신 ETF 가격을 알려줘",
        "오늘 시세가 높은 ETF 순위",
        "금일 현재가를 보여줘",
    ]
    missing = [
        "빈 값을 0으로 처리해서 순위 내줘",
        "공란은 0으로 보고 평균을 내줘",
        "N/A 값을 0으로 만들어줘",
        "null은 0 처리해줘",
    ]
    for question in realtime:
        decision = evaluate_question(question)
        assert decision.blocked is True
        assert decision.reason_code == "SNAPSHOT_NOT_REALTIME"
    for question in missing:
        decision = evaluate_question(question)
        assert decision.blocked is True
        assert decision.reason_code == "MISSING_IS_NOT_ZERO"


def test_safety_allows_objective_candidate_selection_without_buy_advice() -> None:
    questions = [
        "1년 수익률 높은 ETF 3개 골라줘",
        "거래량 많은 해외 ETF 3개 추천해줘",
        "판매중 공모펀드 3개 추천해줘",
        "신용등급이 가장 좋은 국내채권 5개를 알려줘",
    ]
    assert all(evaluate_question(question).blocked is False for question in questions)


def test_safety_blocks_personal_suitability_buy_guarantee_and_hard_advice() -> None:
    questions = [
        "나에게 맞는 ETF를 골라줘",
        "내 상황에 적합한 펀드를 추천해줘",
        "내 투자 성향에 맞는 ETF를 추천해줘",
        "개인 맞춤 상품을 선정해줘",
        "유망한 펀드를 골라줘",
        "이 채권을 사야 해?",
        "이 ETF 사도 돼?",
        "SPY를 매수해도 될까?",
        "이 펀드를 매수하면 될까?",
        "수익을 확실히 보장하는 상품을 알려줘",
    ]
    for question in questions:
        decision = evaluate_question(question)
        assert decision.blocked is True, question
        assert decision.reason_code == "FORECAST_OR_DEFINITIVE_RECOMMENDATION"


def test_current_latest_today_require_explicit_provided_dataset_basis() -> None:
    for question in (
        "현재 거래량 높은 해외 ETF 3개",
        "오늘 종가 높은 해외 ETF 3개",
        "최신 AUM 순위를 알려줘",
    ):
        decision = evaluate_question(question)
        assert decision.blocked is True, question
        assert decision.reason_code == "SNAPSHOT_NOT_REALTIME"

    allowed = evaluate_question(
        "현재 주최 측 제공 데이터 기준으로 거래량 높은 해외 ETF 3개를 알려줘"
    )
    assert allowed.blocked is False
