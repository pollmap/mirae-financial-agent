from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.domain.models import QueryPlan
from app.execution.engine import DuckDBEngine
from app.planner.deterministic import DeterministicPlanner
from app.rendering import render_answer
from app.service import AgentService

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"


def test_numeric_sum_avg_min_max_use_each_products_source_snapshot() -> None:
    with duckdb.connect(str(DATABASE), read_only=True) as connection:
        expected = connection.execute(
            "SELECT SUM(m.value_num), AVG(m.value_num), MIN(m.value_num), MAX(m.value_num), "
            "COUNT(DISTINCT c.product_uid) "
            "FROM product_catalog c JOIN product_metrics m USING(product_uid) "
            "WHERE c.scope='overseas_etp' AND c.internal_type='ETF' "
            "AND c.trading_currency='USD' AND m.metric_id='overseas_etp.aum_last' "
            "AND m.value_num IS NOT NULL "
            "AND m.quality_status IN "
            "('VALID','ZERO_VALID','SUSPECT_OUTLIER','PARTIAL')"
        ).fetchone()
    expected_values = {
        "sum": Decimal(str(expected[0])),
        "avg": (Decimal(str(expected[0])) / Decimal(expected[4])).quantize(
            Decimal("0.0000000001")
        ),
        "min": Decimal(str(expected[2])),
        "max": Decimal(str(expected[3])),
    }
    for function, expected_value in expected_values.items():
        plan = QueryPlan(
            intent="aggregate",
            scopes=["overseas_etp"],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.internal_type", "op": "eq", "value": "ETF"},
                        {"field": "product.trading_currency", "op": "eq", "value": "USD"},
                    ]
                }
            ],
            metrics=["overseas_etp.aum_last"],
            aggregations=[
                {
                    "function": function,
                    "field": "overseas_etp.aum_last",
                    "alias": f"{function}_aum",
                }
            ],
        )
        aggregate = DuckDBEngine(DATABASE).execute(plan).aggregates[0]
        assert Decimal(str(aggregate.value)) == expected_value
        assert aggregate.source_row_count == expected[4]
        assert aggregate.as_of_date == "2026-06-14"
        assert aggregate.unit == "[지표 단위 미확정; 필터 통화 USD]"


def test_secondary_rank_metric_missing_is_retained_and_sorted_last() -> None:
    plan = QueryPlan(
        intent="rank",
        scopes=["fund"],
        entities=[
            {"code": "KR5172430026", "scope": "fund"},
            {"code": "KR5142450022", "scope": "fund"},
            {"code": "KR5013101453", "scope": "fund"},
            {"code": "KR5016101406", "scope": "fund"},
        ],
        metrics=["fund.return_1y", "fund.return_6m"],
        sort=[
            {"field": "fund.return_1y", "direction": "desc", "nulls": "last"},
            {"field": "fund.return_6m", "direction": "desc", "nulls": "last"},
        ],
        limit=4,
    )

    bundle = DuckDBEngine(DATABASE).execute(plan)

    assert [item.product_uid for item in bundle.items] == [
        # Same primary value (73.80): the present secondary value precedes NULL.
        "FUND:PRFD01N001:KR5172430026",
        "FUND:PRFD01N001:KR5142450022",
        # Same primary and secondary values: product_uid is the stable tie-breaker.
        "FUND:PRFD01N001:KR5013101453",
        "FUND:PRFD01N001:KR5016101406",
    ]
    evidence_by_product = {
        item.product_uid: {field.metric_id: field for field in item.fields}
        for item in bundle.items
    }
    assert set(evidence_by_product["FUND:PRFD01N001:KR5172430026"]) >= {
        "fund.return_1y",
        "fund.return_6m",
    }
    missing_secondary = evidence_by_product["FUND:PRFD01N001:KR5142450022"][
        "fund.return_6m"
    ]
    assert missing_secondary.normalized_value is None
    assert missing_secondary.quality_flags == ["MISSING_NULL"]
    assert bundle.calculation is not None
    assert bundle.calculation.tie_breakers == ["product_uid ASC"]
    assert bundle.answerability == "PARTIAL_WITH_COVERAGE"
    assert any(
        "fund.return_6m 값을 확인할 수 없는 상품은 1개" in limitation
        for limitation in bundle.limitations
    )
    rendered = render_answer(plan, bundle)
    assert "1년 수익률:" in rendered
    assert "6개월 수익률:" in rendered
    assert "fund.return_1y" not in rendered
    assert "fund.return_6m" not in rendered
    assert "확인 불가" in rendered


def test_rank_does_not_render_stale_secondary_value_excluded_from_tie_break() -> None:
    plan = QueryPlan(
        intent="rank",
        scopes=["bond"],
        metrics=["bond.after_tax_yield", "bond.applied_yield"],
        sort=[
            {"field": "bond.after_tax_yield", "direction": "desc"},
            {"field": "bond.applied_yield", "direction": "desc"},
        ],
        limit=50,
    )
    bundle = DuckDBEngine(DATABASE).execute(plan)
    stale_item = next(
        item
        for item in bundle.items
        if item.product_uid == "BOND:PRBD01N001:KR356101DC83"
    )
    assert not any(
        field.metric_id == "bond.applied_yield" for field in stale_item.fields
    )
    assert any(
        "bond.applied_yield 값을 확인할 수 없는" in limitation
        for limitation in bundle.limitations
    )


def test_unknown_filter_is_controlled_and_never_compiled_as_sql() -> None:
    bundle = DuckDBEngine(DATABASE).execute(
        QueryPlan(
            intent="search",
            scopes=["bond"],
            filter_groups=[
                {"conditions": [{"field": "attacker.raw_sql", "op": "eq", "value": "x"}]}
            ],
        )
    )
    assert bundle.answerability == "UNAVAILABLE"
    assert bundle.reason_code == "FILTER_FIELD_NOT_ALLOWLISTED"


def test_filter_operands_and_evidence_summary_are_contract_bounded() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(
            intent="search",
            scopes=["bond"],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.name", "op": "contains", "value": "x" * 1950}
                    ]
                }
            ],
        )

    plan = QueryPlan(
        intent="search",
        scopes=["bond"],
        filter_groups=[
            {
                "conditions": [
                    {
                        "field": "product.name",
                        "op": "contains",
                        "value": f"{group_index}-{condition_index}-" + "x" * 280,
                    }
                    for condition_index in range(12)
                ]
            }
            for group_index in range(5)
        ],
        limit=1,
    )
    bundle = DuckDBEngine(DATABASE).execute(plan)
    assert bundle.universe is not None
    assert len(bundle.universe.filter_summary) <= 2000
    summary = json.loads(bundle.universe.filter_summary)
    assert summary["omitted_conditions"] > 0


def test_cross_scope_count_returns_separate_distinct_product_evidence() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        DeterministicPlanner(),
        DuckDBEngine(DATABASE),
    )
    response = asyncio.run(
        service.answer(
            question_id="CROSS-COUNT",
            question="국내 ETF와 공모펀드 상품은 각각 몇 개인가?",
        )
    )
    context = json.loads(response.retrieved_context)
    aggregates = {item["group_key"]: item for item in context["aggregates"]}
    assert context["answerability"] == "FULL"
    assert context["universe"]["scope"] == "multi"
    assert aggregates["domestic_etp"]["value"] == 1201
    assert aggregates["fund"]["value"] == 11115
    assert aggregates["domestic_etp"]["source_table_ids"] == ["PREF01N001"]
    assert aggregates["fund"]["source_table_ids"] == ["PRFD01N001"]
    assert "pd_itm_no" in aggregates["domestic_etp"]["source_fields"]
    assert "itm_no" in aggregates["fund"]["source_fields"]
    assert "국내 ETF: 개수 1,201개" in response.answer
    assert "공모펀드: 개수 11,115개" in response.answer


def test_cross_scope_count_routes_each_condition_to_its_own_scope() -> None:
    plan = QueryPlan(
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
    bundle = DuckDBEngine(DATABASE).execute(plan)
    actual = {str(item.group_key): int(item.value) for item in bundle.aggregates}
    with duckdb.connect(str(DATABASE), read_only=True) as connection:
        expected = {
            "bond": connection.execute(
                "SELECT COUNT(DISTINCT product_uid) FROM product_catalog WHERE scope='bond'"
            ).fetchone()[0],
            "domestic_etp": connection.execute(
                "SELECT COUNT(DISTINCT product_uid) FROM product_catalog "
                "WHERE scope='domestic_etp' AND internal_type='ETF'"
            ).fetchone()[0],
            "fund": connection.execute(
                "SELECT COUNT(DISTINCT product_uid) FROM product_catalog "
                "WHERE scope='fund' AND public_private='공모'"
            ).fetchone()[0],
        }
    assert actual == expected
    assert actual["bond"] > 0
    assert actual["domestic_etp"] > 0
    assert actual["fund"] > 0


def test_complex_catalog_filters_execute_with_literal_raw_label_evidence() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        DeterministicPlanner(),
        DuckDBEngine(DATABASE),
    )
    cases = [
        (
            "미국 주식에 투자하는 해외 ETF 5개 보여줘",
            {
                "product.asset_type": "Equity",
                "product.region": "United States of America",
                "product.internal_type": "ETF",
            },
        ),
        (
            "주식형+미국 투자+연금가능 국내 ETF 5개 보여줘",
            {
                "product.asset_type": "주식",
                "product.region": "미국",
                "product.pension_eligible": "Y",
                "product.internal_type": "ETF",
            },
        ),
        (
            "위험등급 3등급 국내 ETF 5개 보여줘",
            {
                "product.risk_grade": "다소높은위험(3등급)",
                "product.internal_type": "ETF",
            },
        ),
    ]
    for index, (question, expected) in enumerate(cases, start=1):
        response = asyncio.run(
            service.answer(question_id=f"CATALOG-{index}", question=question)
        )
        context = json.loads(response.retrieved_context)
        assert context["answerability"] == "FULL"
        assert context["result_count"] == 5
        for item in context["items"]:
            fields = {field["metric_id"]: field for field in item["fields"]}
            for metric_id, value in expected.items():
                assert fields[metric_id]["normalized_value"] == value
                assert fields[metric_id]["raw_value"] == value


def test_grounded_product_explanation_uses_raw_strategy_and_requires_target() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        DeterministicPlanner(),
        DuckDBEngine(DATABASE),
    )
    missing = asyncio.run(
        service.answer(
            question_id="EXPLAIN-MISSING",
            question="해외 ETF 상품 정보와 운용전략을 설명해줘",
        )
    )
    missing_context = json.loads(missing.retrieved_context)
    assert missing_context["answerability"] == "NEEDS_CLARIFICATION"
    assert missing_context["clarification"]["missing_slots"] == ["explanation_target"]

    exact = asyncio.run(
        service.answer(
            question_id="EXPLAIN-SPY",
            question="해외 ETF 티커 SPY의 상품 정보와 운용전략을 설명해줘",
        )
    )
    context = json.loads(exact.retrieved_context)
    assert context["answerability"] == "FULL"
    assert context["result_count"] == 1
    assert context["items"][0]["product_uid"] == "GLOBAL_ETP:PREF02N001:SPY"
    fields = {field["metric_id"]: field for field in context["items"][0]["fields"]}
    assert fields["product.strategy"]["source_field"] == "cu_strtegy"
    assert fields["product.strategy"]["raw_value"] == fields["product.strategy"][
        "normalized_value"
    ]
    assert fields["product.benchmark"]["source_field"] == "cu_base_index"
    assert "운용전략:" in exact.answer
    assert "cu_strtegy" not in exact.answer
    assert "S&P 500" in exact.answer

    absent = asyncio.run(
        service.answer(
            question_id="EXPLAIN-NO-STRATEGY",
            question="국내 ETN Q530102의 운용전략을 설명해줘",
        )
    )
    absent_context = json.loads(absent.retrieved_context)
    assert absent_context["answerability"] == "PARTIAL_WITH_COVERAGE"
    assert absent_context["reason_code"] == "SOURCE_FIELD_ABSENT"
    assert "운용전략" in absent.answer
    assert "product.strategy" not in absent.answer
    assert "확인할 수 없는 항목" in absent.answer


def test_explicit_absent_overseas_return_answer_contains_available_alternatives() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        DeterministicPlanner(),
        DuckDBEngine(DATABASE),
    )
    response = asyncio.run(
        service.answer(
            question_id="OVERSEAS-RETURN-ALTERNATIVES",
            question="해외 ETF 중 1년 수익률이 가장 높은 상품을 알려줘.",
        )
    )
    context = json.loads(response.retrieved_context)
    assert context["answerability"] == "UNAVAILABLE"
    assert context["reason_code"] == "SOURCE_FIELD_ABSENT"
    assert "AUM" in response.answer
    assert "종가" in response.answer
    assert "거래량" in response.answer
    assert "overseas_etp." not in response.answer


def test_catalog_benchmark_lookup_returns_matching_source_evidence() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        DeterministicPlanner(),
        DuckDBEngine(DATABASE),
    )
    response = asyncio.run(
        service.answer(
            question_id="BENCHMARK",
            question="해외 ETF 티커 SPY의 벤치마크를 조회해줘",
        )
    )
    context = json.loads(response.retrieved_context)
    benchmark = next(
        field
        for field in context["items"][0]["fields"]
        if field["metric_id"] == "product.benchmark"
    )
    assert benchmark["source_field"] == "cu_base_index"
    assert benchmark["raw_value"] == benchmark["normalized_value"]


def test_compare_requires_two_resolved_targets_and_returns_both_when_valid() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        DeterministicPlanner(),
        DuckDBEngine(DATABASE),
    )
    valid = asyncio.run(
        service.answer(
            question_id="COMPARE-OK",
            question="국내 ETP A305080과 Q760014의 1년 수익률을 비교해줘",
        )
    )
    valid_context = json.loads(valid.retrieved_context)
    assert valid_context["result_count"] == 2
    assert {item["product_uid"] for item in valid_context["items"]} == {
        "KR_ETP:PREF01N001:KR7305080004",
        "KR_ETP:PREF01N001:KRG760000148",
    }

    subtype_mismatch = asyncio.run(
        service.answer(
            question_id="COMPARE-BLOCKED",
            question="국내 ETF A305080과 Q760014의 1년 수익률을 비교해줘",
        )
    )
    blocked_context = json.loads(subtype_mismatch.retrieved_context)
    assert blocked_context["answerability"] == "NEEDS_CLARIFICATION"
    assert blocked_context["reason_code"] == "COMPARE_TARGET_NOT_UNIQUE"


def test_monetary_compare_blocks_mixed_currency_and_renders_actual_same_currency() -> None:
    engine = DuckDBEngine(DATABASE)
    mixed = engine.execute(
        QueryPlan(
            intent="compare",
            scopes=["fund"],
            entities=[
                {"code": "KR5010101702", "scope": "fund"},
                {"code": "KR5013101461", "scope": "fund"},
            ],
            metrics=["fund.net_assets"],
        )
    )
    assert mixed.answerability == "INCOMPARABLE"
    assert mixed.reason_code == "CURRENCY_MISMATCH"
    assert mixed.items == []
    assert "KRW" in mixed.limitations[0] and "USD" in mixed.limitations[0]

    same_currency_plan = QueryPlan(
        intent="compare",
        scopes=["fund"],
        entities=[
            {"code": "KR5010101702", "scope": "fund"},
            {"code": "KR5010101703", "scope": "fund"},
        ],
        metrics=["fund.net_assets"],
    )
    same_currency = engine.execute(same_currency_plan)
    assert same_currency.answerability in {"FULL", "PARTIAL_WITH_COVERAGE"}
    rendered = render_answer(same_currency_plan, same_currency)
    assert "[지표 단위 미확정; 상품통화 KRW]" in rendered
    assert "KRW_PENDING" not in rendered

    unknown_zero = engine.execute(
        QueryPlan(
            intent="compare",
            scopes=["fund"],
            entities=[
                {"code": "KR5114490235", "scope": "fund"},
                {"code": "KR5010101702", "scope": "fund"},
            ],
            metrics=["fund.net_assets"],
        )
    )
    assert unknown_zero.answerability == "INCOMPARABLE"
    assert unknown_zero.reason_code == "COMPARISON_VALUE_UNUSABLE"
    assert unknown_zero.items == []


def test_compare_blocks_mixed_source_dates_without_time_alignment_data() -> None:
    bundle = DuckDBEngine(DATABASE).execute(
        QueryPlan(
            intent="compare",
            scopes=["overseas_etp"],
            entities=[
                {"code": "DWCR.K", "scope": "overseas_etp"},
                {"code": "AAA", "scope": "overseas_etp"},
            ],
            metrics=["overseas_etp.close_price"],
        )
    )
    assert bundle.answerability == "INCOMPARABLE"
    assert bundle.reason_code == "AS_OF_DATE_MISMATCH"
    assert bundle.items == []
    assert "2025-07-28" in bundle.limitations[0]
    assert "2026-06-16" in bundle.limitations[0]


def test_zero_unknown_is_present_but_not_rankable_or_filter_valid() -> None:
    plan = QueryPlan(
        intent="rank",
        scopes=["fund"],
        filter_groups=[
            {"conditions": [{"field": "product.currency", "op": "eq", "value": "KRW"}]}
        ],
        metrics=["fund.net_assets"],
        sort=[{"field": "fund.net_assets", "direction": "asc"}],
        limit=5,
    )
    bundle = DuckDBEngine(DATABASE).execute(plan)
    values = [
        Decimal(str(field.normalized_value))
        for item in bundle.items
        for field in item.fields
        if field.metric_id == "fund.net_assets"
    ]
    assert len(values) == 5
    assert all(value > 0 for value in values)
    assert all(
        "ZERO_UNKNOWN" not in field.quality_flags
        for item in bundle.items
        for field in item.fields
    )


def test_fund_rank_warns_on_source_backed_share_class_family_candidates() -> None:
    bundle = DuckDBEngine(DATABASE).execute(
        QueryPlan(
            intent="rank",
            scopes=["fund"],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.public_private", "op": "eq", "value": "공모"}
                    ]
                }
            ],
            metrics=["fund.return_1y"],
            sort=[{"field": "fund.return_1y", "direction": "desc"}],
            limit=50,
        )
    )
    assert any("rptt_ksd_itm_no" in limitation for limitation in bundle.limitations)


def test_fifty_result_context_stays_inside_public_contract_budget() -> None:
    service = AgentService(
        Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
        DeterministicPlanner(),
        DuckDBEngine(DATABASE),
    )
    response = asyncio.run(
        service.answer(question_id="LIMIT-50", question="국내 ETF 50개 보여줘")
    )
    context = json.loads(response.retrieved_context)
    assert len(context["items"]) == 50
    assert len(response.retrieved_context) < 500_000


def test_comma_numeric_operands_bind_as_decimal_without_duckdb_conversion_error() -> None:
    plan = QueryPlan(
        intent="search",
        scopes=["bond"],
        metrics=["bond.remaining_days_raw"],
        filter_groups=[
            {
                "conditions": [
                    {
                        "field": "bond.remaining_days_raw",
                        "op": "between",
                        "value": "1,000",
                        "value2": "2,000",
                    }
                ]
            }
        ],
        limit=5,
    )
    bundle = DuckDBEngine(DATABASE).execute(plan)
    assert bundle.answerability in {"FULL", "PARTIAL_WITH_COVERAGE"}
    assert bundle.result_count == 5
    for item in bundle.items:
        value = next(
            field.normalized_value
            for field in item.fields
            if field.metric_id == "bond.remaining_days_raw"
        )
        assert Decimal("1000") <= Decimal(str(value)) <= Decimal("2000")


def test_metric_filters_exclude_sentinel_values_and_null_means_no_usable_value() -> None:
    engine = DuckDBEngine(DATABASE)
    impossible_future = QueryPlan(
        intent="search",
        scopes=["bond"],
        metrics=["bond.maturity_date"],
        filter_groups=[
            {
                "conditions": [
                    {"field": "bond.maturity_date", "op": "gt", "value": "21000101"}
                ]
            }
        ],
        limit=10,
    )
    blocked_sentinels = engine.execute(impossible_future)
    assert blocked_sentinels.answerability == "NO_RESULT"
    assert blocked_sentinels.items == []

    sentinel_lookup = QueryPlan(
        intent="search",
        scopes=["bond"],
        entities=[{"code": "KR6009311E61", "scope": "bond"}],
        metrics=["bond.maturity_date"],
        filter_groups=[
            {"conditions": [{"field": "bond.maturity_date", "op": "is_null"}]}
        ],
        limit=10,
    )
    unusable = engine.execute(sentinel_lookup)
    assert unusable.result_count == 1
    rendered = render_answer(sentinel_lookup, unusable)
    assert "99991231" not in rendered
    assert "확인 불가" in rendered

    no_latest_duration = engine.execute(
        QueryPlan(
            intent="search",
            scopes=["bond"],
            metrics=["bond.duration"],
            filter_groups=[
                {"conditions": [{"field": "bond.duration", "op": "is_null"}]}
            ],
            limit=50,
        )
    )
    assert no_latest_duration.items
    assert all(
        not any(
            field.metric_id == "bond.duration"
            and field.normalized_value is not None
            for field in item.fields
        )
        for item in no_latest_duration.items
    )


def test_source_provided_metric_count_is_distinct_from_valid_value_count() -> None:
    planner = DeterministicPlanner()
    plan = asyncio.run(planner.plan("매수가능수량이 제공된 국내채권은 몇 개인가?"))
    assert plan.assumptions == ["metric_count_basis=source_present"]
    bundle = DuckDBEngine(DATABASE).execute(plan)
    assert int(bundle.aggregates[0].value) == 881
    assert bundle.coverage is not None
    assert bundle.coverage.present_count == 881
    assert bundle.coverage.valid_count == 325
    assert any("쓸 수 없는 값은 556개" in item for item in bundle.limitations)


def test_mixed_date_aggregate_uses_common_latest_filtered_universe() -> None:
    plan = QueryPlan(
        intent="aggregate",
        scopes=["bond"],
        metrics=["bond.duration"],
        aggregations=[
            {
                "function": "avg",
                "field": "bond.duration",
                "alias": "average_duration",
            }
        ],
    )
    with duckdb.connect(str(DATABASE), read_only=True) as connection:
        latest_date = connection.execute(
            "SELECT MAX(as_of_date) FROM product_metrics "
            "WHERE metric_id='bond.duration' AND value_num IS NOT NULL "
            "AND quality_status IN "
            "('VALID','ZERO_VALID','SUSPECT_OUTLIER','PARTIAL')"
        ).fetchone()[0]
        total, count = connection.execute(
            "SELECT SUM(value_num), COUNT(DISTINCT product_uid) "
            "FROM product_metrics WHERE metric_id='bond.duration' "
            "AND value_num IS NOT NULL AND quality_status IN "
            "('VALID','ZERO_VALID','SUSPECT_OUTLIER','PARTIAL') "
            "AND as_of_date=?",
            [latest_date],
        ).fetchone()
    expected = (Decimal(str(total)) / Decimal(count)).quantize(Decimal("0.0000000001"))
    bundle = DuckDBEngine(DATABASE).execute(plan)
    aggregate = bundle.aggregates[0]
    assert Decimal(str(aggregate.value)) == expected
    assert aggregate.source_row_count == count
    assert aggregate.as_of_date == str(latest_date)
    assert "함께 포함" not in " ".join(bundle.limitations)


def test_rank_uses_common_latest_date_inside_filtered_universe() -> None:
    plan = QueryPlan(
        intent="rank",
        scopes=["bond"],
        metrics=["bond.duration"],
        sort=[{"field": "bond.duration", "direction": "desc"}],
        limit=5,
    )
    with duckdb.connect(str(DATABASE), read_only=True) as connection:
        latest_date = connection.execute(
            "SELECT MAX(m.as_of_date) FROM product_catalog c "
            "JOIN product_metrics m USING(product_uid) "
            "WHERE c.scope='bond' AND m.metric_id='bond.duration' "
            "AND m.value_num IS NOT NULL AND m.quality_status IN "
            "('VALID','ZERO_VALID','SUSPECT_OUTLIER','PARTIAL')"
        ).fetchone()[0]
        expected = [
            str(row[0])
            for row in connection.execute(
                "SELECT c.product_uid FROM product_catalog c JOIN product_metrics m "
                "USING(product_uid) WHERE c.scope='bond' AND m.metric_id='bond.duration' "
                "AND m.value_num IS NOT NULL AND m.quality_status IN "
                "('VALID','ZERO_VALID','SUSPECT_OUTLIER','PARTIAL') "
                "AND m.as_of_date=? "
                "ORDER BY m.value_num DESC NULLS LAST, c.product_uid ASC LIMIT 5",
                [latest_date],
            ).fetchall()
        ]
    bundle = DuckDBEngine(DATABASE).execute(plan)
    assert [item.product_uid for item in bundle.items] == expected
    assert bundle.coverage is not None
    assert bundle.coverage.numerator == bundle.coverage.valid_count
    assert bundle.coverage.rankable_count == bundle.coverage.valid_count
    assert str(latest_date) in " ".join(bundle.limitations)


def test_rank_latest_date_is_selected_after_entity_filter() -> None:
    plan = QueryPlan(
        intent="rank",
        scopes=["overseas_etp"],
        entities=[{"code": "DWCR.K", "scope": "overseas_etp"}],
        filter_groups=[
            {
                "conditions": [
                    {"field": "product.trading_currency", "op": "eq", "value": "USD"}
                ]
            }
        ],
        metrics=["overseas_etp.close_price"],
        sort=[{"field": "overseas_etp.close_price", "direction": "desc"}],
        limit=1,
    )
    bundle = DuckDBEngine(DATABASE).execute(plan)
    assert bundle.result_count == 1
    assert bundle.items[0].product_uid == "GLOBAL_ETP:PREF02N001:DWCR.K"
    assert "2025-07-28" in " ".join(bundle.limitations)


def test_overseas_close_price_rank_preserves_source_and_warns_scale_review() -> None:
    bundle = DuckDBEngine(DATABASE).execute(
        QueryPlan(
            intent="rank",
            scopes=["overseas_etp"],
            filter_groups=[
                {
                    "conditions": [
                        {"field": "product.trading_currency", "op": "eq", "value": "USD"}
                    ]
                }
            ],
            metrics=["overseas_etp.close_price"],
            sort=[{"field": "overseas_etp.close_price", "direction": "desc"}],
            limit=1,
        )
    )
    assert bundle.items[0].product_uid == "GLOBAL_ETP:PREF02N001:SPY"
    assert bundle.answerability == "PARTIAL_WITH_COVERAGE"
    assert any("이상치 후보" in limitation for limitation in bundle.limitations)


def test_compare_blocks_dated_and_undated_source_context_mix() -> None:
    bundle = DuckDBEngine(DATABASE).execute(
        QueryPlan(
            intent="compare",
            scopes=["domestic_etp"],
            entities=[
                {"code": "KRG530001020", "scope": "domestic_etp"},
                {"code": "KR7305080004", "scope": "domestic_etp"},
            ],
            metrics=["domestic_etp.close_price"],
        )
    )
    assert bundle.answerability == "INCOMPARABLE"
    assert bundle.reason_code == "AS_OF_CONTEXT_MISSING"
    assert bundle.items == []


def test_entity_diagnostics_cover_code_isin_duplicate_alias_and_ambiguous_name() -> None:
    engine = DuckDBEngine(DATABASE)
    alias_plan = QueryPlan(
        intent="compare",
        scopes=["overseas_etp"],
        entities=[
            {"code": "SPY", "scope": "overseas_etp"},
            {"code": "US78462F1030", "scope": "overseas_etp"},
        ],
        metrics=["overseas_etp.close_price"],
    )
    alias_matches = engine.resolve_entity_matches(alias_plan)
    assert alias_matches[0][1] == ("GLOBAL_ETP:PREF02N001:SPY",)
    assert alias_matches[1][1] == alias_matches[0][1]

    name_plan = QueryPlan(
        intent="explain",
        scopes=["overseas_etp"],
        entities=[{"name": "Strive 500 ETF", "scope": "overseas_etp"}],
    )
    assert engine.resolve_entities(name_plan) == [("Strive 500 ETF", 2)]


def test_catalog_semantics_and_field_level_dates_are_source_grounded() -> None:
    engine = DuckDBEngine(DATABASE)
    bond = engine.execute(
        QueryPlan(
            intent="lookup",
            scopes=["bond"],
            entities=[{"code": "KR101501DA16", "scope": "bond"}],
            metrics=["product.issuer", "product.country_code", "bond.kind"],
        )
    )
    bond_fields = {field.metric_id: field for field in bond.items[0].fields}
    assert bond_fields["product.issuer"].source_field == "PD_PBCM"
    assert bond_fields["product.country_code"].source_field == "PD_CTRY_CD"
    assert bond_fields["bond.kind"].source_field == "BD_KND"
    assert bond_fields["product.issuer"].as_of_status == "DATASET_SNAPSHOT_ONLY"
    assert "product.manager" not in bond_fields
    assert "product.strategy" not in bond_fields
    assert "product.investment_region" not in bond_fields

    etp = engine.execute(
        QueryPlan(
            intent="lookup",
            scopes=["overseas_etp"],
            entities=[{"code": "SPY", "scope": "overseas_etp"}],
            metrics=["product.manager", "product.benchmark", "product.strategy"],
        )
    )
    etp_fields = {field.metric_id: field for field in etp.items[0].fields}
    for metric_id in ("product.manager", "product.benchmark", "product.strategy"):
        assert etp_fields[metric_id].as_of_date == "2026-06-14"
        assert etp_fields[metric_id].as_of_status == "AVAILABLE"

    fund = engine.execute(
        QueryPlan(
            intent="lookup",
            scopes=["fund"],
            entities=[{"code": "KR5114420158", "scope": "fund"}],
            metrics=["product.manager_code", "fund.domestic_overseas_class"],
        )
    )
    fund_fields = {field.metric_id: field for field in fund.items[0].fields}
    assert fund_fields["product.manager_code"].normalized_value == "00040010"
    assert fund_fields["product.manager_code"].raw_value == "00040010"
    assert fund_fields["fund.domestic_overseas_class"].source_field == "ovrs_fd_desc"
    assert "product.manager" not in fund_fields
    assert "product.strategy" not in fund_fields


def test_valid_special_count_basis_executes_without_unsupported_raw_columns() -> None:
    raw = DuckDBEngine(DATABASE).execute(
        QueryPlan(
            intent="aggregate",
            scopes=["domestic_etp"],
            aggregations=[
                {
                    "function": "count",
                    "field": "product.id",
                    "alias": "raw_count",
                    "distinct": True,
                }
            ],
            group_by=["product.internal_type"],
            assumptions=["count_basis=raw"],
        )
    )
    assert {item.group_key for item in raw.aggregates} == {"ETF", "ETN"}

    blocked = DuckDBEngine(DATABASE).execute(
        QueryPlan(
            intent="aggregate",
            scopes=["fund"],
            aggregations=[
                {
                    "function": "count",
                    "field": "product.id",
                    "alias": "raw_count",
                    "distinct": True,
                }
            ],
            group_by=["product.internal_type"],
            assumptions=["count_basis=raw"],
        )
    )
    assert blocked.answerability == "UNAVAILABLE"
    assert blocked.reason_code == "SPECIAL_COUNT_BASIS_GROUP_UNSUPPORTED"
