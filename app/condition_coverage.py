"""Server-side question-to-plan condition coverage audit.

The HCX planner is allowed to interpret language, but it is not allowed to
silently delete a material qualifier.  This module extracts a deliberately
bounded set of high-impact conditions that the official data can ground and
checks that the physical plan still carries each one.  Deterministic additions
use only literal catalog values or KG-resolvable party aliases; unknown meaning
is returned to the service as a clarification/unavailable state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.models import Condition, ConditionLedgerEntry, FilterGroup, QueryPlan
from app.planner.catalog_filters import resolve_catalog_filters

_SCOPES = {
    "bond": ("국내채권",),
    "domestic_etp": ("국내 ETF", "국내 ETN", "국내 ETP"),
    "overseas_etp": ("해외 ETF", "해외 ETN", "해외 ETP", "미국 상장 ETF"),
    "fund": ("공모펀드", "사모펀드", "펀드"),
}
_MANAGER = re.compile(
    r"(?P<value>[A-Za-z0-9가-힣&.,'·/㈜()_ -]{2,60}?)(?:이|가)?\s*"
    r"(?:운용하는|운용한|운용 중인|운용사인)"
)
_ISSUER = re.compile(
    r"(?P<value>[A-Za-z0-9가-힣&.,'·/㈜()_ -]{2,60}?)(?:이|가)?\s*"
    r"(?:발행하는|발행한|발행사인)"
)
_STRATEGY_TERMS = (
    "배당",
    "인컴",
    "커버드콜",
    "퀄리티",
    "모멘텀",
    "저변동",
    "가치",
    "성장",
    "반도체",
    "인공지능",
    "AI",
    "income",
    "dividend",
    "covered call",
    "quality",
    "momentum",
    "low volatility",
)
_BENCHMARK = re.compile(
    r"(?P<value>(?:S&P\s*500|NASDAQ\s*100|KOSPI\s*200|코스피\s*200|"
    r"다우(?:존스)?|MSCI(?:\s+[A-Za-z]+){0,3}))\s*(?:지수|벤치마크|기초지수)?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CoverageAudit:
    plan: QueryPlan
    ledger: list[ConditionLedgerEntry]
    missing_semantic_basis: bool = False
    unavailable_reason: str | None = None


def _condition_key(condition: Condition) -> tuple[str, str, str]:
    return condition.field, condition.op, str(condition.value).casefold()


def _conditions(plan: QueryPlan) -> list[Condition]:
    return [condition for group in plan.filter_groups for condition in group.conditions]


def _add_conditions(plan: QueryPlan, additions: list[Condition]) -> QueryPlan:
    existing = {_condition_key(condition) for condition in _conditions(plan)}
    pending = [condition for condition in additions if _condition_key(condition) not in existing]
    if not pending:
        return plan
    payload = plan.model_dump(mode="json")
    if payload["filter_groups"]:
        if plan.groups_join == "AND":
            payload["filter_groups"][0]["conditions"].extend(
                condition.model_dump(mode="json") for condition in pending
            )
        else:
            # A material qualifier applies to every OR branch; adding it as a
            # separate OR branch would broaden the query and silently lose it.
            for group in payload["filter_groups"]:
                group["conditions"].extend(
                    condition.model_dump(mode="json") for condition in pending
                )
    else:
        payload["filter_groups"] = [
            FilterGroup(conditions=pending).model_dump(mode="json")
        ]
    payload["preserved_plan"] = plan.preserved_plan
    return QueryPlan.model_validate(payload)


def _entry(
    condition_id: str,
    kind: str,
    requested_text: str,
    status: str,
    fields: list[str] | None = None,
    note: str | None = None,
) -> ConditionLedgerEntry:
    return ConditionLedgerEntry(
        condition_id=condition_id,
        kind=kind,  # type: ignore[arg-type]
        requested_text=requested_text,
        status=status,  # type: ignore[arg-type]
        grounded_fields=fields or [],
        note=note,
    )


def _strategy_text(question: str) -> str | None:
    terms = [term for term in _STRATEGY_TERMS if term.casefold() in question.casefold()]
    if not terms:
        return None
    # Preserve the compact user phrase. Query-time lexical expansion handles
    # Korean/English equivalents without changing the source text recorded in
    # the condition ledger.
    return " ".join(dict.fromkeys(terms))[:160]


def audit_question_conditions(question: str, plan: QueryPlan) -> CoverageAudit:
    """Return an augmented plan plus a bounded, non-sensitive condition ledger."""

    ledger: list[ConditionLedgerEntry] = []
    additions: list[Condition] = []

    requested_scopes = [
        scope
        for scope, phrases in _SCOPES.items()
        if any(phrase in question for phrase in phrases)
    ]
    for scope in dict.fromkeys(requested_scopes):
        ledger.append(
            _entry(
                f"scope_{scope}",
                "scope",
                scope,
                "grounded" if scope in plan.scopes else "clarification_required",
                ["product.scope"] if scope in plan.scopes else [],
            )
        )

    intent_tokens = {
        "aggregate": ("몇 개", "몇 건", "개수", "상품 수", "합계", "평균"),
        "rank": (
            "상위",
            "하위",
            "최대",
            "최소",
            "높은 순",
            "낮은 순",
            "큰 순",
            "작은 순",
            "정렬",
        ),
        "compare": ("비교",),
    }
    requested_intent = next(
        (
            intent
            for intent, tokens in intent_tokens.items()
            if any(token in question for token in tokens)
        ),
        None,
    )
    if requested_intent:
        compatible = plan.intent == requested_intent or (
            requested_intent == "rank" and plan.intent == "compare"
        )
        ledger.append(
            _entry(
                "intent_primary",
                "intent",
                requested_intent,
                "grounded" if compatible else "clarification_required",
                ["intent"] if compatible else [],
            )
        )

    if len(plan.scopes) == 1:
        catalog = resolve_catalog_filters(question, plan.scopes)
        if catalog.reason_code is None:
            additions.extend(catalog.conditions)

    manager = _MANAGER.search(question)
    if manager:
        value = manager.group("value")
        additions.append(Condition(field="product.manager", op="eq", value=value))
        ledger.append(_entry("party_manager", "party", value, "grounded", ["product.manager"]))

    issuer = _ISSUER.search(question)
    if issuer:
        value = issuer.group("value")
        additions.append(Condition(field="product.issuer", op="eq", value=value))
        ledger.append(_entry("party_issuer", "party", value, "grounded", ["product.issuer"]))

    benchmark = _BENCHMARK.search(question)
    if benchmark and any(token in question for token in ("벤치마크", "기초지수", "추종", "지수")):
        value = benchmark.group("value")
        additions.append(Condition(field="product.benchmark", op="contains", value=value))
        ledger.append(
            _entry("benchmark_text", "benchmark", value, "grounded", ["product.benchmark"])
        )

    asks_strategy = plan.intent in {"search", "rank"} and any(
        token in question for token in ("전략", "테마", "비슷한", "유사한")
    )
    strategy = _strategy_text(question) if asks_strategy else None
    if strategy:
        if plan.scopes and any(scope not in {"overseas_etp"} for scope in plan.scopes):
            ledger.append(
                _entry(
                    "strategy_theme",
                    "strategy",
                    strategy,
                    "unavailable",
                    note="선택 상품군에는 검색 가능한 자연어 전략 원문이 없습니다.",
                )
            )
            return CoverageAudit(
                plan=plan,
                ledger=ledger,
                unavailable_reason="SEMANTIC_STRATEGY_UNAVAILABLE",
            )
        additions.append(Condition(field="product.strategy", op="contains", value=strategy))
        ledger.append(
            _entry("strategy_theme", "strategy", strategy, "grounded", ["product.strategy"])
        )
    elif asks_strategy and not benchmark:
        ledger.append(
            _entry(
                "semantic_similarity_basis",
                "comparison_basis",
                "비슷함/유사함",
                "clarification_required",
                note="유사성 기준이 운용전략인지 벤치마크인지 확인해야 합니다.",
            )
        )
        return CoverageAudit(plan=plan, ledger=ledger, missing_semantic_basis=True)

    augmented = _add_conditions(plan, additions)
    grounded = {condition.field for condition in _conditions(augmented)}
    catalog_kinds = {
        "product.region": ("region", "region_filter"),
        "product.investment_region": ("region", "region_filter"),
        "product.asset_type": ("asset_type", "asset_type_filter"),
        "product.asset_class": ("asset_type", "asset_type_filter"),
        "product.internal_type": ("product_type", "product_type_filter"),
        "product.public_private": ("product_type", "public_private_filter"),
        "product.currency": ("currency", "currency_filter"),
        "product.trading_currency": ("currency", "currency_filter"),
        "product.risk_grade": ("risk_grade", "risk_grade_filter"),
    }
    for condition in _conditions(augmented):
        spec = catalog_kinds.get(condition.field)
        if spec is None or any(item.condition_id == spec[1] for item in ledger):
            continue
        ledger.append(
            _entry(
                spec[1],
                spec[0],
                str(condition.value),
                "grounded" if condition.field in grounded else "clarification_required",
                [condition.field] if condition.field in grounded else [],
            )
        )
    for index, metric in enumerate(augmented.metrics):
        ledger.append(
            _entry(f"metric_{index}", "metric", metric, "grounded", [metric])
        )
    return CoverageAudit(plan=augmented, ledger=ledger)
