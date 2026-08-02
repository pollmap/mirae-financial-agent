"""Bounded Korean-to-source-label resolver for catalog-only filters.

The resolver never invents a normalized taxonomy.  It maps only phrases whose
meaning and exact serving value are known for one scope; otherwise it returns a
stable fail-closed reason for the orchestration layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.models import Condition

_EQUITY_VALUE = {
    "domestic_etp": "주식",
    "overseas_etp": "Equity",
    "fund": "주식형",
}
_US_REGION_VALUE = {
    "domestic_etp": "미국",
    "overseas_etp": "United States of America",
}
_DOMESTIC_RISK_GRADE = {
    "1": "매우높은위험(1등급)",
    "2": "높은위험(2등급)",
    "3": "다소높은위험(3등급)",
    "4": "보통위험(4등급)",
    "5": "낮은위험(5등급)",
    "6": "매우낮은위험(6등급)",
}

_EQUITY_PATTERN = re.compile(r"(?:주식형|주식\s*(?:에|으로)?\s*투자)")
_BOND_FUND_PATTERN = re.compile(r"(?:채권형\s*펀드|채권\s*펀드)")
_US_INVESTMENT_PATTERN = re.compile(
    r"(?:미국(?:\s*(?:주식|채권|시장))?\s*(?:에\s*)?투자|"
    r"투자\s*지역(?:이|은|는|:)?\s*미국)"
)
_PENSION_PATTERN = re.compile(r"연금\s*(?:투자|거래|편입)?\s*(?:가능|대상|적격)")
_RISK_GRADE_PATTERN = re.compile(r"위험\s*등급(?:이|은|는|:)?\s*([1-6])\s*등급")


@dataclass(frozen=True, slots=True)
class CatalogFilterResolution:
    conditions: tuple[Condition, ...] = ()
    reason_code: str | None = None


def resolve_catalog_filters(question: str, scopes: list[str]) -> CatalogFilterResolution:
    """Resolve a small, audited phrase set to literal serving catalog values."""

    asks_equity = bool(_EQUITY_PATTERN.search(question))
    asks_bond_fund = bool(_BOND_FUND_PATTERN.search(question))
    asks_us_region = bool(_US_INVESTMENT_PATTERN.search(question))
    asks_pension = bool(_PENSION_PATTERN.search(question))
    risk_match = _RISK_GRADE_PATTERN.search(question)
    if not any((asks_equity, asks_bond_fund, asks_us_region, asks_pension, risk_match)):
        return CatalogFilterResolution()
    if len(scopes) != 1:
        return CatalogFilterResolution(reason_code="CATALOG_VALUE_SCOPE_AMBIGUOUS")

    scope = scopes[0]
    conditions: list[Condition] = []
    if asks_equity:
        value = _EQUITY_VALUE.get(scope)
        if value is None:
            return CatalogFilterResolution(reason_code="CATALOG_VALUE_UNAVAILABLE")
        conditions.append(Condition(field="product.asset_type", op="eq", value=value))
    if asks_bond_fund:
        if scope != "fund":
            return CatalogFilterResolution(reason_code="CATALOG_VALUE_UNAVAILABLE")
        conditions.append(Condition(field="product.asset_type", op="eq", value="채권형"))
    if asks_us_region:
        value = _US_REGION_VALUE.get(scope)
        if value is None:
            return CatalogFilterResolution(reason_code="CATALOG_VALUE_UNAVAILABLE")
        conditions.append(Condition(field="product.region", op="eq", value=value))
    if asks_pension:
        if scope != "domestic_etp":
            return CatalogFilterResolution(reason_code="CATALOG_VALUE_UNAVAILABLE")
        conditions.append(Condition(field="product.pension_eligible", op="eq", value="Y"))
    if risk_match:
        if scope != "domestic_etp":
            return CatalogFilterResolution(reason_code="CATALOG_VALUE_UNAVAILABLE")
        grade = risk_match.group(1)
        conditions.append(
            Condition(
                field="product.risk_grade",
                op="eq",
                value=_DOMESTIC_RISK_GRADE[grade],
            )
        )
    return CatalogFilterResolution(conditions=tuple(conditions))
