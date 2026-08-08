"""Precision tests for the deterministic exact-identifier pre-router.

The pre-router may only fire on unmistakable single-identifier detail
questions; every uncertain shape must fall through (``None``) to the normal
planner.  No database or network access is involved.
"""

from __future__ import annotations

from app.planner.pre_router import pre_route


def test_bond_isin_detail_question_routes_to_bond_lookup() -> None:
    plan = pre_route("KR6033024C99 채권 상세 정보 알려줘")
    assert plan is not None
    assert plan.intent == "lookup"
    assert plan.scopes == ["bond"]
    assert len(plan.entities) == 1
    entity = plan.entities[0]
    assert entity.code == "KR6033024C99"
    assert entity.scope == "bond"
    assert not entity.name
    assert plan.filter_groups == []
    assert plan.metrics == []
    assert plan.aggregations == []
    assert plan.sort == []
    assert plan.limit <= 10


def test_overseas_ticker_with_ticker_context_routes_to_overseas_lookup() -> None:
    plan = pre_route("해외 티커 QQQ 상세 정보 알려줘")
    assert plan is not None
    assert plan.intent == "lookup"
    assert plan.scopes == ["overseas_etp"]
    assert plan.entities[0].code == "QQQ"
    assert plan.entities[0].scope == "overseas_etp"


def test_krx_ticker_with_code_context_routes_to_domestic_lookup() -> None:
    plan = pre_route("종목 코드 069500 상세 정보 알려줘")
    assert plan is not None
    assert plan.intent == "lookup"
    assert plan.scopes == ["domestic_etp"]
    assert plan.entities[0].code == "069500"
    assert plan.entities[0].scope == "domestic_etp"


def test_ranking_question_containing_code_returns_none() -> None:
    assert pre_route("KR6033024C99 보다 표면금리 높은 채권 알려줘") is None


def test_aggregate_question_containing_code_returns_none() -> None:
    assert pre_route("KR6033024C99 포함 채권 평균 수익률 정보 알려줘") is None


def test_two_code_comparison_returns_none() -> None:
    assert pre_route("KR6033024C99와 XS2010609684 상세 비교해줘") is None


def test_bare_six_digit_amount_is_not_treated_as_ticker() -> None:
    # "100000" is six digits but the question has no 종목/코드/티커 context.
    assert pre_route("AUM 100000 이상 국내 ETF 정보 알려줘") is None


def test_question_without_identifier_returns_none() -> None:
    assert pre_route("국내 ETF 상세 정보 알려줘") is None


def test_identifier_without_detail_request_returns_none() -> None:
    assert pre_route("KR6033024C99 채권 매수") is None


def test_identifier_with_unresolvable_scope_returns_none() -> None:
    # IE-prefixed ISIN with no product-type words: scope stays unknown.
    assert pre_route("IE00B4L5Y983 상세 정보 알려줘") is None


def test_empty_question_returns_none() -> None:
    assert pre_route("") is None
    assert pre_route("   ") is None
