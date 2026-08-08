"""Unit tests for the Stage-2 semantic grounder (no DB, no network)."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.models import QueryPlan
from app.semantics.grounder import GroundingError, ground_semantic, load_value_aliases


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "search",
        "scope_concepts": ["domestic_etp"],
        "metric_concepts": [],
        "filters": [],
        "sort_direction": "none",
        "top_n": 10,
        "entities": [],
        "needs_clarification": False,
        "clarification_question": "",
    }
    base.update(overrides)
    return base


def _ground(**overrides: Any) -> QueryPlan:
    return ground_semantic(_payload(**overrides), question="질문")


# ---------------------------------------------------------------------------
# Metric grounding
# ---------------------------------------------------------------------------


def test_single_scope_rank_binds_physical_metric_and_mirrors_sort() -> None:
    plan = _ground(
        intent="rank",
        scope_concepts=["domestic_etp"],
        metric_concepts=["return_1y"],
        sort_direction="desc",
        top_n=3,
    )
    assert isinstance(plan, QueryPlan)
    assert plan.intent == "rank"
    assert plan.scopes == ["domestic_etp"]
    assert plan.metrics == ["domestic_etp.return_1y"]
    assert [(item.field, item.direction, item.nulls) for item in plan.sort] == [
        ("domestic_etp.return_1y", "desc", "last")
    ]
    assert plan.limit == 3
    assert plan.needs_clarification is False


def test_search_with_metric_and_direction_promotes_to_rank() -> None:
    plan = _ground(
        intent="search",
        scope_concepts=["domestic_etp"],
        metric_concepts=["expense_ratio"],
        sort_direction="asc",
    )
    assert plan.intent == "rank"
    assert plan.metrics == ["domestic_etp.expense_ratio"]
    assert [(item.field, item.direction) for item in plan.sort] == [
        ("domestic_etp.expense_ratio", "asc")
    ]


def test_rank_without_direction_defaults_to_desc() -> None:
    plan = _ground(intent="rank", metric_concepts=["return_1y"], sort_direction="none")
    assert [item.direction for item in plan.sort] == ["desc"]
    assert [item.field for item in plan.sort] == plan.metrics


def test_multi_scope_keeps_concept_id_for_cross_scope_executor() -> None:
    plan = _ground(
        intent="rank",
        scope_concepts=["domestic_etp", "fund"],
        metric_concepts=["return_1y"],
        sort_direction="desc",
    )
    assert plan.scopes == ["domestic_etp", "fund"]
    assert plan.metrics == ["return_1y"]
    assert [item.field for item in plan.sort] == ["return_1y"]


def test_duplicate_metric_concepts_are_deduplicated() -> None:
    plan = _ground(
        intent="rank",
        metric_concepts=["return_1y", "return_1y"],
        sort_direction="desc",
    )
    assert plan.metrics == ["domestic_etp.return_1y"]
    assert len(plan.sort) == 1


def test_absent_binding_for_single_scope_fails_closed() -> None:
    with pytest.raises(GroundingError) as excinfo:
        _ground(
            intent="rank",
            scope_concepts=["overseas_etp"],
            metric_concepts=["return_1y"],
            sort_direction="desc",
        )
    assert excinfo.value.reason_code == "BINDING_ABSENT_FOR_SCOPE"


def test_unknown_metric_concept_fails_closed() -> None:
    with pytest.raises(GroundingError) as excinfo:
        _ground(metric_concepts=["sharpe_ratio"])
    assert excinfo.value.reason_code == "CONCEPT_UNKNOWN"
    assert isinstance(excinfo.value, ValueError)


def test_dimension_concept_is_not_a_metric() -> None:
    with pytest.raises(GroundingError) as excinfo:
        _ground(metric_concepts=["asset_type"])
    assert excinfo.value.reason_code == "CONCEPT_NOT_METRIC"


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


def test_all_scope_expands_to_four_scopes() -> None:
    plan = _ground(scope_concepts=["all"], metric_concepts=["aum"])
    assert plan.scopes == ["bond", "domestic_etp", "overseas_etp", "fund"]
    assert plan.metrics == ["aum"]


def test_unspecified_scope_inferred_from_metric_bindings() -> None:
    plan = _ground(
        intent="rank",
        scope_concepts=["unspecified"],
        metric_concepts=["return_1y"],
        sort_direction="desc",
    )
    assert plan.scopes == ["domestic_etp", "fund"]
    assert plan.metrics == ["return_1y"]
    assert any(note.startswith("scope:unspecified→") for note in plan.assumptions)


def test_unspecified_scope_without_metrics_stays_empty_for_clarification() -> None:
    plan = _ground(scope_concepts=["unspecified"])
    assert plan.scopes == []
    assert plan.intent == "search"


def test_unknown_scope_token_fails_closed() -> None:
    with pytest.raises(GroundingError) as excinfo:
        _ground(scope_concepts=["crypto"])
    assert excinfo.value.reason_code == "SCOPE_UNKNOWN"


# ---------------------------------------------------------------------------
# Filter grounding and value aliases
# ---------------------------------------------------------------------------


def test_inferred_alias_translation_records_assumption() -> None:
    plan = _ground(
        scope_concepts=["overseas_etp"],
        filters=[{"concept": "asset_type", "op": "eq", "value_text": "주식형"}],
    )
    condition = plan.filter_groups[0].conditions[0]
    assert condition.field == "product.asset_type"
    assert condition.op == "eq"
    assert condition.value == "Equity"
    assert "alias:주식형→Equity(overseas_etp, inferred)" in plan.assumptions


def test_official_alias_translation_adds_no_assumption() -> None:
    plan = _ground(
        scope_concepts=["domestic_etp"],
        filters=[{"concept": "asset_type", "op": "eq", "value_text": "주식형"}],
    )
    condition = plan.filter_groups[0].conditions[0]
    assert condition.value == "주식"
    assert not any(note.startswith("alias:") for note in plan.assumptions)


def test_bond_asset_type_binds_product_asset_class() -> None:
    plan = _ground(
        scope_concepts=["bond"],
        filters=[{"concept": "asset_type", "op": "eq", "value_text": "국채"}],
    )
    condition = plan.filter_groups[0].conditions[0]
    assert condition.field == "product.asset_class"
    assert condition.value == "국채"  # no alias row -> raw value, engine validates downstream


def test_multi_scope_filter_keeps_raw_value_and_default_field() -> None:
    plan = _ground(
        scope_concepts=["domestic_etp", "overseas_etp"],
        filters=[{"concept": "asset_type", "op": "eq", "value_text": "주식형"}],
    )
    condition = plan.filter_groups[0].conditions[0]
    assert condition.field == "product.asset_type"
    assert condition.value == "주식형"
    assert not any(note.startswith("alias:") for note in plan.assumptions)


def test_pension_eligible_concept_maps_to_pension_trade_eligible_field() -> None:
    plan = _ground(
        scope_concepts=["domestic_etp"],
        filters=[{"concept": "pension_eligible", "op": "eq", "value_text": "Y"}],
    )
    assert plan.filter_groups[0].conditions[0].field == "product.pension_trade_eligible"


def test_in_filter_translates_each_member() -> None:
    plan = _ground(
        scope_concepts=["overseas_etp"],
        filters=[{"concept": "asset_type", "op": "in", "value_text": "주식형, 채권형"}],
    )
    condition = plan.filter_groups[0].conditions[0]
    assert condition.op == "in"
    assert condition.value == ["Equity", "Bond"]


def test_filter_dimension_absent_for_scope_fails_closed() -> None:
    with pytest.raises(GroundingError) as excinfo:
        _ground(
            scope_concepts=["overseas_etp"],
            filters=[{"concept": "risk_grade", "op": "eq", "value_text": "3등급"}],
        )
    assert excinfo.value.reason_code == "BINDING_ABSENT_FOR_SCOPE"


def test_unknown_filter_concept_fails_closed() -> None:
    with pytest.raises(GroundingError) as excinfo:
        _ground(filters=[{"concept": "moon_phase", "op": "eq", "value_text": "보름"}])
    assert excinfo.value.reason_code == "CONCEPT_UNKNOWN"


def test_load_value_aliases_key_and_inferred_flag() -> None:
    aliases = load_value_aliases()
    assert aliases[("주식형", "product.asset_type", "overseas_etp")] == ("Equity", True)
    assert aliases[("주식형", "product.asset_type", "domestic_etp")] == ("주식", False)


# ---------------------------------------------------------------------------
# Clarification passthrough
# ---------------------------------------------------------------------------


def test_clarify_passthrough_builds_valid_clarification_plan() -> None:
    plan = ground_semantic(
        _payload(
            intent="clarify",
            needs_clarification=True,
            clarification_question="어느 기간 수익률을 기준으로 할까요?",
        ),
        question="수익률 좋은 상품 알려줘",
    )
    assert plan.intent == "clarify"
    assert plan.needs_clarification is True
    assert plan.clarification_question == "어느 기간 수익률을 기준으로 할까요?"
    assert plan.missing_slots == ["semantic"]
    assert 2 <= len(plan.clarification_options) <= 4
    assert all(option.value and option.label for option in plan.clarification_options)
    request = plan.clarification()
    assert request is not None
    assert request.preserved_plan["original_question"] == "수익률 좋은 상품 알려줘"


def test_clarify_with_empty_question_still_produces_concrete_question() -> None:
    plan = ground_semantic(
        _payload(needs_clarification=True, clarification_question=""),
        question="애매한 질문",
    )
    assert plan.intent == "clarify"
    assert plan.clarification_question


# ---------------------------------------------------------------------------
# Entities, lookup, limit
# ---------------------------------------------------------------------------


def test_entity_code_and_name_are_distinguished() -> None:
    plan = _ground(
        intent="lookup",
        scope_concepts=["domestic_etp"],
        entities=["Q500001", "TIGER 미국S&P500"],
    )
    code_entity, name_entity = plan.entities
    assert code_entity.code == "Q500001"
    assert code_entity.scope == "domestic_etp"
    assert name_entity.name == "TIGER 미국S&P500"
    assert name_entity.code is None


def test_isin_entity_is_treated_as_code() -> None:
    plan = _ground(intent="lookup", scope_concepts=["bond"], entities=["KR6079161D97"])
    assert plan.entities[0].code == "KR6079161D97"
    assert plan.entities[0].scope == "bond"


def test_lookup_without_entity_fails_closed() -> None:
    with pytest.raises(GroundingError) as excinfo:
        _ground(intent="lookup", entities=[])
    assert excinfo.value.reason_code == "ENTITY_REQUIRED"


def test_top_n_is_clamped_to_plan_bounds() -> None:
    assert _ground(top_n=500).limit == 50
    assert _ground(top_n=0).limit == 1
    assert _ground(top_n=None).limit == 10
