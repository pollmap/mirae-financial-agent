from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.planner.deterministic import DeterministicPlanner

ROOT = Path(__file__).resolve().parents[2]


def _contains_subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_subset(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return False
        return all(_contains_subset(actual[index], value) for index, value in enumerate(expected))
    return actual == expected


def test_all_40_gold_questions_match_declared_plan_subset() -> None:
    planner = DeterministicPlanner()
    failures: list[str] = []
    for line in (
        (ROOT / "tests" / "gold_queries_v0.jsonl").read_text(encoding="utf-8-sig").splitlines()
    ):
        fixture = json.loads(line)
        actual = asyncio.run(planner.plan(fixture["question"])).model_dump(mode="json")
        if not _contains_subset(actual, fixture["expected_plan_subset"]):
            failures.append(
                f"{fixture['id']}: expected={fixture['expected_plan_subset']} actual={actual}"
            )
    assert not failures, "\n".join(failures)


def test_ambiguous_etf_question_asks_market_not_guess() -> None:
    plan = asyncio.run(DeterministicPlanner().plan("수익률 높은 ETF 3개 알려줘"))
    assert plan.intent == "clarify"
    assert plan.needs_clarification is True
    assert plan.missing_slots == ["market"]
    assert {option.value for option in plan.clarification_options} == {
        "domestic_etp",
        "overseas_etp",
    }


def test_missing_return_period_asks_specific_period() -> None:
    plan = asyncio.run(DeterministicPlanner().plan("국내 ETF 수익률 높은 상품 알려줘"))
    assert plan.intent == "clarify"
    assert plan.missing_slots == ["return_period"]


def test_return_period_options_come_from_usable_scope_registry() -> None:
    domestic = asyncio.run(
        DeterministicPlanner().plan("국내 ETF 수익률 높은 상품 알려줘")
    )
    assert {option.value for option in domestic.clarification_options} <= {
        "1d",
        "1m",
        "3m",
        "6m",
        "1y",
        "ytd",
    }

    overseas = asyncio.run(
        DeterministicPlanner().plan("해외 ETF 수익률 높은 상품 알려줘")
    )
    assert overseas.intent == "unsupported"
    assert overseas.clarification_options == []
    assert "policy_reason=RETURN_METRICS_UNAVAILABLE" in overseas.assumptions


def test_simple_cross_scope_count_is_grouped_by_scope() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan("국내 ETF와 공모펀드 상품은 각각 몇 개인가?")
    )
    assert plan.intent == "aggregate"
    assert plan.scopes == ["domestic_etp", "fund"]
    assert plan.group_by == ["product.scope"]
    assert plan.groups_join == "OR"
    assert plan.metrics == []
    assert plan.aggregations[0].field == "product.id"
    assert plan.aggregations[0].distinct is True
    assert {
        (condition.field, condition.value)
        for group in plan.filter_groups
        for condition in group.conditions
    } == {
        ("product.internal_type", "ETF"),
        ("product.public_private", "공모"),
    }


def test_bounded_catalog_value_resolver_uses_scope_specific_source_labels() -> None:
    planner = DeterministicPlanner()
    overseas = asyncio.run(
        planner.plan("미국 주식에 투자하는 해외 ETF 5개 보여줘")
    )
    domestic = asyncio.run(
        planner.plan("주식형+미국 투자+연금가능 국내 ETF 5개 보여줘")
    )
    risk = asyncio.run(planner.plan("위험등급 3등급 국내 ETF 5개 보여줘"))

    def conditions(plan: object) -> set[tuple[str, object]]:
        return {
            (condition.field, condition.value)
            for group in plan.filter_groups
            for condition in group.conditions
        }

    assert {
        ("product.internal_type", "ETF"),
        ("product.asset_type", "Equity"),
        ("product.region", "United States of America"),
    } <= conditions(overseas)
    assert {
        ("product.internal_type", "ETF"),
        ("product.asset_type", "주식"),
        ("product.region", "미국"),
        ("product.pension_eligible", "Y"),
    } <= conditions(domestic)
    assert (
        "product.risk_grade",
        "다소높은위험(3등급)",
    ) in conditions(risk)


def test_catalog_value_resolver_fails_closed_without_exact_scope_label() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan("미국 주식에 투자하는 공모펀드 5개 보여줘")
    )
    assert plan.intent == "unsupported"
    assert "policy_reason=CATALOG_VALUE_UNAVAILABLE" in plan.assumptions


def test_realtime_and_definitive_recommendation_are_unsupported() -> None:
    planner = DeterministicPlanner()
    realtime = asyncio.run(planner.plan("지금 실시간으로 가장 많이 오른 국내 ETF를 알려줘"))
    recommend = asyncio.run(planner.plan("무조건 수익이 날 국내채권 하나를 당장 사라고 해줘"))
    assert realtime.intent == "unsupported"
    assert "policy_reason=SNAPSHOT_NOT_REALTIME" in realtime.assumptions
    assert recommend.intent == "unsupported"
    assert "policy_reason=FORECAST_OR_DEFINITIVE_RECOMMENDATION" in recommend.assumptions


def test_multiple_rank_metrics_require_explicit_priority() -> None:
    planner = DeterministicPlanner()
    ambiguous = asyncio.run(planner.plan("국내 ETF 중 보수는 낮고 AUM은 높은 상품 5개를 알려줘"))
    assert ambiguous.intent == "clarify"
    assert ambiguous.missing_slots == ["ranking_priority"]
    assert [option.label for option in ambiguous.clarification_options] == [
        "보수 우선",
        "AUM 우선",
    ]

    explicit = asyncio.run(planner.plan("국내 ETF 중 보수 우선, 그다음 AUM 큰 순으로 5개를 알려줘"))
    assert explicit.intent == "rank"
    assert explicit.metrics == [
        "domestic_etp.expense_ratio",
        "domestic_etp.aum_last",
    ]
    assert [(item.field, item.direction) for item in explicit.sort] == [
        ("domestic_etp.expense_ratio", "asc"),
        ("domestic_etp.aum_last", "desc"),
    ]


def test_lookup_name_removes_scope_wrapper_without_changing_product_name() -> None:
    plan = asyncio.run(DeterministicPlanner().plan("국내채권 엠에프엠코리아8CB를 찾아줘."))
    assert plan.intent == "lookup"
    assert plan.scopes == ["bond"]
    assert len(plan.entities) == 1
    assert plan.entities[0].name == "엠에프엠코리아8CB"


def test_explanation_requires_target_and_uses_exact_product_when_present() -> None:
    planner = DeterministicPlanner()
    missing = asyncio.run(
        planner.plan("해외 ETF 상품 정보와 운용전략을 설명해줘")
    )
    exact = asyncio.run(
        planner.plan("해외 ETF 티커 SPY의 상품 정보와 운용전략을 설명해줘")
    )

    assert missing.intent == "clarify"
    assert missing.missing_slots == ["explanation_target"]
    assert {option.label for option in missing.clarification_options} == {
        "상품명으로 지정",
        "상품코드로 지정",
    }
    assert exact.intent == "explain"
    assert exact.scopes == ["overseas_etp"]
    assert exact.entities[0].code == "SPY"


def test_lookup_and_compare_keep_all_requested_metrics_without_priority_question() -> None:
    planner = DeterministicPlanner()
    lookup = asyncio.run(
        planner.plan("해외 ETF 티커 SPY의 AUM과 종가와 거래량 조회해줘")
    )
    comparison = asyncio.run(
        planner.plan("해외 ETF 티커 SPY와 IVV의 종가와 거래량 비교해줘")
    )

    assert lookup.intent == "lookup"
    assert lookup.needs_clarification is False
    assert lookup.metrics == [
        "overseas_etp.aum_last",
        "overseas_etp.close_price",
        "overseas_etp.volume_1d",
    ]
    assert comparison.intent == "compare"
    assert comparison.needs_clarification is False
    assert {entity.code for entity in comparison.entities} == {"SPY", "IVV"}
    assert comparison.metrics == [
        "overseas_etp.close_price",
        "overseas_etp.volume_1d",
    ]


def test_comparison_metric_clarification_is_scope_specific_and_executable() -> None:
    planner = DeterministicPlanner()
    bond = asyncio.run(
        planner.plan("국내채권 KR101501DA16과 KR101501DA24 비교해줘")
    )
    overseas = asyncio.run(planner.plan("해외 ETF SPY와 IVV 비교해줘"))

    assert bond.missing_slots == ["comparison_metric"]
    assert [option.value for option in bond.clarification_options] == [
        "bond.coupon_rate",
        "bond.buy_yield",
        "bond.credit_rating",
    ]
    assert overseas.missing_slots == ["comparison_metric"]
    assert [option.value for option in overseas.clarification_options] == [
        "overseas_etp.aum_last",
        "overseas_etp.close_price",
        "overseas_etp.volume_1d",
    ]


def test_overseas_close_volume_and_fund_net_assets_are_rankable_requests() -> None:
    planner = DeterministicPlanner()
    close = asyncio.run(
        planner.plan("거래통화 USD인 해외 ETF 중 종가 높은 3개 알려줘")
    )
    volume = asyncio.run(planner.plan("거래량이 가장 많은 해외 ETF 3개 알려줘"))
    fund = asyncio.run(planner.plan("순자산이 큰 공모펀드 3개 알려줘"))

    assert (close.intent, close.metrics) == ("rank", ["overseas_etp.close_price"])
    assert (volume.intent, volume.metrics) == ("rank", ["overseas_etp.volume_1d"])
    assert (fund.intent, fund.metrics) == ("rank", ["fund.net_assets"])


def test_us_equity_search_and_us_listed_scope_do_not_become_name_lookup() -> None:
    planner = DeterministicPlanner()
    search = asyncio.run(planner.plan("미국 주식형 해외 ETF 3개 찾아줘"))
    listed = asyncio.run(planner.plan("미국 상장 ETF 중 거래량 많은 3개 알려줘"))

    assert search.intent == "search"
    assert search.scopes == ["overseas_etp"]
    assert search.entities == []
    assert {
        (condition.field, condition.value)
        for group in search.filter_groups
        for condition in group.conditions
    } >= {
        ("product.internal_type", "ETF"),
        ("product.asset_type", "Equity"),
        ("product.region", "United States of America"),
    }
    assert listed.intent == "rank"
    assert listed.scopes == ["overseas_etp"]
    assert listed.metrics == ["overseas_etp.volume_1d"]


def test_explicit_provided_dataset_basis_allows_current_snapshot_ranking() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan(
            "현재 주최 측 제공 데이터 기준으로 거래량 높은 해외 ETF 3개 알려줘"
        )
    )
    assert plan.intent == "rank"
    assert plan.metrics == ["overseas_etp.volume_1d"]


def test_top_and_bottom_rank_phrasing_sets_direction() -> None:
    planner = DeterministicPlanner()
    top = asyncio.run(planner.plan("해외 ETF 거래량 상위 3개"))
    bottom = asyncio.run(planner.plan("해외 ETF 거래량 하위 3개"))

    assert top.intent == "rank"
    assert top.sort[0].direction == "desc"
    assert bottom.intent == "rank"
    assert bottom.sort[0].direction == "asc"


def test_cross_scope_metric_ids_preserve_specific_policy_reason() -> None:
    planner = DeterministicPlanner()
    expense = asyncio.run(
        planner.plan("국내와 해외 ETF를 합쳐 총보수가 낮은 순으로 알려줘")
    )
    aum = asyncio.run(
        planner.plan("국내 ETF와 해외 ETF를 AUM 큰 순으로 섞어 정렬해줘")
    )

    assert (expense.intent, expense.metrics) == ("rank", ["cross.expense_ratio"])
    assert (aum.intent, aum.metrics) == ("rank", ["cross.aum_last"])


def test_party_name_fund_word_does_not_add_fund_scope() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan(
            "BlackRock Fund Advisors가 운용하는 해외 ETF를 공식 관계 기준으로 1개 보여줘."
        )
    )

    assert plan.scopes == ["overseas_etp"]
    assert plan.intent == "search"
    assert plan.entities == []


def test_strategy_similarity_is_search_not_product_name_lookup() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan("배당 인컴 전략과 비슷한 해외 ETF를 3개 찾아줘.")
    )

    assert plan.intent == "search"
    assert plan.scopes == ["overseas_etp"]
    assert plan.entities == []


def test_bond_theme_inside_overseas_etf_strategy_does_not_add_bond_scope() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan("채권 인컴 전략과 비슷한 해외 ETF를 3개 찾아줘.")
    )

    assert plan.scopes == ["overseas_etp"]


def test_punctuated_manager_name_does_not_become_ticker_entity() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan(
            "Grantham, Mayo, Van Otterloo & Co. LLC가 운용하는 해외 ETF를 1개 보여줘."
        )
    )

    assert plan.intent == "search"
    assert plan.entities == []


def test_source_word_does_not_add_special_count_basis_to_search() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan(
            "OBP Capital LLC가 운용하는 해외 ETF를 공식 관계와 원본 목록으로 교차검증해 1개 보여줘."
        )
    )

    assert plan.intent == "search"
    assert not any(item.startswith("count_basis=") for item in plan.assumptions)


def test_ticker_containing_aum_does_not_select_aum_metric() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan("해외 ETF AAUM.K와 AAVM.O를 비교해줘.")
    )

    assert plan.needs_clarification is True
    assert plan.missing_slots == ["comparison_metric"]


def test_bond_type_public_fund_phrase_is_grounded() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan("채권형 공모펀드 중 공식 조건에 맞는 상품 3개 보여줘.")
    )
    conditions = {
        (condition.field, condition.value)
        for group in plan.filter_groups
        for condition in group.conditions
    }

    assert ("product.asset_type", "채권형") in conditions
