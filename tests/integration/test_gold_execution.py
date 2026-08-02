from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import Settings
from app.execution.engine import DuckDBEngine
from app.planner.deterministic import DeterministicPlanner
from app.service import AgentService

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"
HANDLED_ASSERTIONS = {
    "coverage",
    "exact_count",
    "exact_group_counts",
    "exact_internal_type",
    "exact_name",
    "exact_product_uid",
    "excluded_quality_count",
    "excluded_zero_count",
    "missing_count",
    "must_cite",
    "must_count_distinct",
    "must_not_double_count_products",
    "must_not_estimate",
    "must_not_forecast",
    "must_not_impute_missing_as_zero",
    "must_not_present_snapshot_as_realtime",
    "must_not_rank",
    "must_not_recommend_definitively",
    "must_offer_available_alternatives",
    "must_report_outlier_limitation",
    "must_report_share_class_concentration",
    "must_report_universe",
    "must_state_aum_unit_unconfirmed",
    "must_state_snapshot_date",
    "must_state_trading_currency",
    "must_use_isin_as_alias_not_pk",
    "ordered_product_uids",
    "ordered_values",
    "partial_rows_kept_with_flag",
    "present_values",
    "policy_reason",
    "quarantine_excel_row",
    "serving_note",
    "universe",
    "valid_etn_values",
    "zero_values",
}


def _fixtures() -> list[dict[str, object]]:
    fixtures: list[dict[str, object]] = []
    for name in ("gold_queries_v0.jsonl", "policy_queries_v0.jsonl"):
        for line in (ROOT / "tests" / name).read_text(encoding="utf-8-sig").splitlines():
            fixtures.append(json.loads(line))
    return fixtures


@pytest.fixture(scope="module")
def service() -> AgentService:
    if not DATABASE.is_file():
        pytest.fail(
            "Run `.venv/bin/python scripts/build_data.py --no-parquet` before integration tests"
        )
    settings = Settings(environment="test", database_path=DATABASE, planner_mode="deterministic")
    return AgentService(settings, DeterministicPlanner(), DuckDBEngine(DATABASE))


def _decimal(value: object) -> Decimal:
    return Decimal(str(value).replace(",", ""))


def _metric_values(context: dict[str, object], metric_id: str) -> list[object]:
    values: list[object] = []
    for item in context["items"]:
        for field in item["fields"]:
            if field["metric_id"] == metric_id:
                values.append(field["normalized_value"])
    return values


def _all_text(response: object, context: dict[str, object]) -> str:
    return (
        response.answer + " " + " ".join(context["limitations"]) + " " + response.retrieved_context
    )


def _assert_declared_subset(actual: object, expected: object, path: str = "plan") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected object, got {type(actual).__name__}"
        for key, value in expected.items():
            assert key in actual, f"{path}: missing key {key}"
            _assert_declared_subset(actual[key], value, f"{path}.{key}")
        return
    if isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected list, got {type(actual).__name__}"
        assert len(actual) == len(expected), (
            f"{path}: expected {len(expected)} items, got {len(actual)}"
        )
        for index, value in enumerate(expected):
            _assert_declared_subset(actual[index], value, f"{path}[{index}]")
        return
    assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"


def _assert_one(
    fixture: dict[str, object],
    assertion: dict[str, object],
    response: object,
    context: dict[str, object],
    plan: object,
) -> None:
    kind = assertion["type"]
    assert kind in HANDLED_ASSERTIONS, f"unhandled assertion: {kind}"
    items = context["items"]
    aggregates = context["aggregates"]
    text = _all_text(response, context)

    if kind == "exact_product_uid":
        assert items[0]["product_uid"] == assertion["value"]
    elif kind == "exact_name":
        assert items[0]["name"] == assertion["value"]
    elif kind == "ordered_product_uids":
        assert [item["product_uid"] for item in items] == assertion["value"]
    elif kind == "ordered_values":
        actual = _metric_values(context, plan.metrics[0])
        assert [_decimal(value) for value in actual] == [
            _decimal(value) for value in assertion["value"]
        ]
    elif kind == "exact_count":
        assert any(int(_decimal(item["value"])) == assertion["value"] for item in aggregates)
    elif kind == "exact_group_counts":
        actual = {str(item["group_key"]): int(_decimal(item["value"])) for item in aggregates}
        assert actual == assertion["value"]
    elif kind == "exact_internal_type":
        values = [
            field["normalized_value"]
            for field in items[0]["fields"]
            if field["source_field"] in {"pd_grp_no", "cu_etn_yn"}
        ]
        assert assertion["value"] in values
    elif kind == "must_cite":
        fields = {field["source_field"] for item in items for field in item["fields"]} | {
            field for item in aggregates for field in item["source_fields"]
        }
        assert set(assertion["fields"]) <= fields
    elif kind == "policy_reason":
        assert context["reason_code"] == assertion["value"]
    elif kind == "coverage":
        assert context["coverage"]["numerator"] == assertion["numerator"]
        assert context["coverage"]["denominator"] == assertion["denominator"]
    elif kind == "must_report_universe":
        assert assertion["value"] in {
            context["universe"]["raw_count"],
            context["universe"]["serving_count"],
            context["universe"]["eligible_count"],
        }
    elif kind == "must_not_rank":
        assert not items and context["answerability"] == "DATA_QUALITY_BLOCKED"
    elif kind in {"must_not_forecast", "must_not_recommend_definitively"}:
        assert context["answerability"] == "SAFETY_LIMITED"
        assert not any(
            token in response.answer for token in ("사세요", "보장합니다", "무조건 오릅니다")
        )
    elif kind == "must_not_impute_missing_as_zero":
        assert context["reason_code"] == "MISSING_IS_NOT_ZERO"
    elif kind == "must_not_estimate":
        assert context["answerability"] == "UNAVAILABLE"
    elif kind == "must_not_present_snapshot_as_realtime":
        assert context["reason_code"] == "SNAPSHOT_NOT_REALTIME"
    elif kind in {"must_report_outlier_limitation", "must_report_share_class_concentration"}:
        token = "이상치" if kind == "must_report_outlier_limitation" else "클래스"
        assert token in text
    elif kind == "exact_internal_type":
        raise AssertionError("handled above")
    elif kind == "serving_note":
        assert "serving" in text and "1,201" in text
    elif kind == "quarantine_excel_row":
        assert str(assertion["value"]) in text
    elif kind in {"excluded_zero_count", "zero_values"}:
        assert f"{assertion['value']:,}" in text
    elif kind == "valid_etn_values":
        assert context["coverage"]["numerator"] == assertion["value"]
    elif kind == "present_values":
        assert context["coverage"]["present_count"] == assertion["value"]
    elif kind == "excluded_quality_count":
        assert (
            context["universe"]["raw_count"] - context["universe"]["serving_count"]
            == assertion["value"]
        )
    elif kind == "must_state_snapshot_date":
        assert context["data_snapshot_date"] == assertion["value"]
    elif kind == "must_state_trading_currency":
        values = [
            field["normalized_value"]
            for item in items
            for field in item["fields"]
            if field["source_field"] == "pd_trd_ccy"
        ]
        assert values and set(values) == {assertion["value"]}
    elif kind == "must_state_aum_unit_unconfirmed":
        assert "미확정" in text or "CONTEXT_REQUIRED" in text
    elif kind == "must_offer_available_alternatives":
        assert all(value in text for value in assertion["value"])
    elif kind == "must_use_isin_as_alias_not_pk":
        assert not items[0]["product_uid"].endswith("US97717W5629")
        assert any(field["source_field"] == "pd_isin_cd" for field in items[0]["fields"])
    elif kind == "partial_rows_kept_with_flag":
        assert "TBF" in text and "EMOP.K" in text and str(assertion["value"]) in text
    elif kind == "missing_count":
        assert (
            context["coverage"]["denominator"] - context["coverage"]["numerator"]
            == assertion["value"]
        )
    elif kind == "universe":
        filters = context["universe"]["filter_summary"]
        assert "internal_type" in filters and "ETF" in filters
    elif kind == "must_count_distinct" or kind == "must_not_double_count_products":
        assert context["universe"]["serving_count"] == 11_138


def test_all_declared_gold_and_policy_assertions(service: AgentService) -> None:
    failures: list[str] = []
    for fixture in _fixtures():
        try:
            plan = asyncio.run(service.planner.plan(fixture["question"]))
            if "expected_plan_subset" in fixture:
                _assert_declared_subset(
                    plan.model_dump(mode="json"),
                    fixture["expected_plan_subset"],
                )
            response = asyncio.run(
                service.answer(question_id=fixture["id"], question=fixture["question"])
            )
            context = json.loads(response.retrieved_context)
            assert context["answerability"] == fixture["expected_answerability"]
            for assertion in fixture.get("expected_assertions", []):
                _assert_one(fixture, assertion, response, context, plan)
        except Exception as exc:  # collect every fixture gap in one run
            failures.append(f"{fixture['id']}: {type(exc).__name__}: {exc}")
    assert not failures, "\n".join(failures)


def test_fixture_assertion_vocabulary_is_fully_implemented() -> None:
    declared = {
        assertion["type"]
        for fixture in _fixtures()
        for assertion in fixture.get("expected_assertions", [])
    }
    assert declared == HANDLED_ASSERTIONS
