"""Stage-2 grounder: HCX semantic plan -> physical ``QueryPlan``.

Stage-1 (``HCX_SEMANTIC_PLAN_SCHEMA``) keeps the model scope-neutral: it emits
financial *concepts* (``return_1y``, ``expense_ratio``, ``asset_type`` …) and
never physical column names.  This module performs the server-side second
stage: every concept is resolved against ``registry/semantic`` (concept
catalog + value aliases) into the exact physical fields the executor already
validates.

Fail-closed contract
--------------------
Grounding never guesses.  Anything the registries cannot vouch for raises
``GroundingError`` with a machine-readable ``reason_code`` instead of
producing a plausible-but-unverified plan:

* unknown metric/filter concepts (``CONCEPT_UNKNOWN`` / ``CONCEPT_NOT_METRIC``),
* a metric concept whose binding is ``ABSENT`` for the single requested scope
  (``BINDING_ABSENT_FOR_SCOPE``),
* ``lookup`` without any entity (``ENTITY_REQUIRED``),
* malformed payload fragments (``PAYLOAD_MALFORMED`` and friends).

Deliberate non-defaults:

* ``scope_concepts=['unspecified']`` with **no** metric concepts leaves
  ``plan.scopes`` empty on purpose.  We do *not* default to the ETP pair or
  any other guess -- the downstream clarification machinery owns the "which
  product universe?" question and an invented default would silently change
  results.  With metric concepts present, the scopes are inferred as exactly
  the scopes in which *every* requested concept has a non-``ABSENT`` binding,
  and the inference is recorded in ``plan.assumptions``.
* No default filters, periods, or units are ever invented; multi-scope filter
  values stay untranslated (the cross-scope executor translates per scope) and
  unknown single-scope values stay raw so the engine's catalog validation can
  fail closed downstream.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.models import (
    ClarificationOption,
    Condition,
    Entity,
    FilterGroup,
    QueryPlan,
    SortItem,
)
from app.planner.deterministic import _CODES
from app.semantics.concepts import SEMANTIC_ROOT, ConceptCatalog, load_concept_catalog

_ALL_SCOPES: tuple[str, ...] = ("bond", "domestic_etp", "overseas_etp", "fund")
_SEMANTIC_SCOPES = {*_ALL_SCOPES, "all", "unspecified"}
_INTENTS = {"lookup", "search", "rank", "compare", "aggregate", "explain", "clarify", "unsupported"}
_FILTER_OPS = {"eq", "in", "gte", "lte", "contains"}
_IN_SPLIT = re.compile(r"[,|]")

# Default physical field per filter concept.  Used verbatim for multi-scope
# plans (the cross-scope executor re-resolves per scope); single-scope plans
# resolve the field from the concept catalog instead, which is what maps the
# bond ``asset_type`` concept onto ``product.asset_class``.
_FILTER_CONCEPT_FIELDS: dict[str, str] = {
    "asset_type": "product.asset_type",
    "region": "product.region",
    "risk_grade": "product.risk_grade",
    "internal_type": "product.internal_type",
    "manager": "product.manager",
    "issuer": "product.issuer",
    "sale_status": "product.sale_status",
    "public_private": "product.public_private",
    "pension_eligible": "product.pension_trade_eligible",
}


class GroundingError(ValueError):
    """Fail-closed grounding failure with a reason_code attribute."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


@lru_cache(maxsize=1)
def load_value_aliases(path: Path | None = None) -> dict[tuple[str, str, str], tuple[str, bool]]:
    """Load ``value_aliases_v1.csv`` as ``{(alias, field, scope): (canonical, is_inferred)}``."""

    source = path or SEMANTIC_ROOT / "value_aliases_v1.csv"
    aliases: dict[tuple[str, str, str], tuple[str, bool]] = {}
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["alias"].strip(), row["field"].strip(), row["scope"].strip())
            aliases[key] = (
                row["canonical_value"].strip(),
                row["is_inferred"].strip().lower() in {"true", "1", "yes"},
            )
    return aliases


def _as_list(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key) or []
    if not isinstance(value, list):
        raise GroundingError("PAYLOAD_MALFORMED", f"'{key}' 항목은 목록이어야 합니다.")
    return value


def _clarify_plan(payload: Mapping[str, Any], question: str) -> QueryPlan:
    question_text = (
        str(payload.get("clarification_question") or "").strip()
        or "질문의 조건을 조금 더 구체적으로 알려주세요."
    )
    return QueryPlan(
        intent="clarify",
        needs_clarification=True,
        clarification_question=question_text,
        missing_slots=["semantic"],
        clarification_options=[
            ClarificationOption(value="조건 구체화", label="조건을 더 구체적으로 알려주세요"),
            ClarificationOption(
                value="상품군 지정",
                label="상품군(국내채권/국내 ETF·ETN/해외 ETF·ETN/공모펀드)을 지정해 주세요",
            ),
        ],
        preserved_plan={"original_question": question, "semantic_payload": dict(payload)},
    )


def _metric_concepts(payload: Mapping[str, Any], catalog: ConceptCatalog) -> list[str]:
    concept_ids: list[str] = []
    for raw in _as_list(payload, "metric_concepts"):
        concept_id = str(raw).strip()
        if not concept_id or concept_id in concept_ids:
            continue
        concept = catalog.concepts.get(concept_id)
        if concept is None:
            raise GroundingError(
                "CONCEPT_UNKNOWN", f"'{concept_id}'은(는) 알 수 없는 지표 개념입니다."
            )
        if concept.concept_kind != "METRIC":
            raise GroundingError(
                "CONCEPT_NOT_METRIC", f"'{concept_id}'은(는) 정렬·조회 가능한 지표 개념이 아닙니다."
            )
        concept_ids.append(concept_id)
    return concept_ids


def _resolve_scopes(
    payload: Mapping[str, Any],
    metric_concepts: list[str],
    catalog: ConceptCatalog,
    assumptions: list[str],
) -> list[str]:
    tokens: list[str] = []
    for raw in _as_list(payload, "scope_concepts"):
        token = str(raw).strip()
        if not token or token in tokens:
            continue
        if token not in _SEMANTIC_SCOPES:
            raise GroundingError("SCOPE_UNKNOWN", f"'{token}'은(는) 알 수 없는 상품군입니다.")
        tokens.append(token)

    if "all" in tokens:
        return list(_ALL_SCOPES)
    concrete = [token for token in tokens if token in _ALL_SCOPES]
    if concrete:
        return concrete

    # 'unspecified' (or an empty list): without metric concepts there is no
    # evidence to pick any universe, so the scopes stay empty and the
    # downstream clarification machinery asks the user.  Never default here.
    if not metric_concepts:
        return []
    inferred = [
        scope
        for scope in _ALL_SCOPES
        if all(catalog.binding(concept_id, scope) is not None for concept_id in metric_concepts)
    ]
    if inferred:
        assumptions.append(f"scope:unspecified→{','.join(inferred)}(metric bindings)")
    return inferred


def _ground_metrics(
    metric_concepts: list[str], scopes: list[str], catalog: ConceptCatalog
) -> list[str]:
    metrics: list[str] = []
    for concept_id in metric_concepts:
        if len(scopes) == 1:
            bound = catalog.binding(concept_id, scopes[0])
            if bound is None:
                label = catalog.concepts[concept_id].label_ko
                raise GroundingError(
                    "BINDING_ABSENT_FOR_SCOPE",
                    f"'{label}'({concept_id}) 지표는 {scopes[0]} 상품군에 존재하지 않습니다.",
                )
            metrics.append(bound)
        else:
            # Multi-scope (or still-unresolved) plans keep the concept id
            # itself: the cross-scope executor resolves concepts per scope.
            metrics.append(concept_id)
    return metrics


def _coerce_number(value: str) -> str | int | float:
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _translate_value(
    raw: str,
    field: str,
    scope: str | None,
    aliases: Mapping[tuple[str, str, str], tuple[str, bool]],
    assumptions: list[str],
) -> str:
    if scope is None:
        return raw
    hit = aliases.get((raw, field, scope))
    if hit is None:
        return raw
    canonical, is_inferred = hit
    if is_inferred:
        note = f"alias:{raw}→{canonical}({scope}, inferred)"
        if note not in assumptions:
            assumptions.append(note)
    return canonical


def _ground_filters(
    payload: Mapping[str, Any],
    scopes: list[str],
    catalog: ConceptCatalog,
    assumptions: list[str],
) -> list[Condition]:
    single_scope = scopes[0] if len(scopes) == 1 else None
    aliases = load_value_aliases()
    conditions: list[Condition] = []
    for item in _as_list(payload, "filters"):
        if not isinstance(item, Mapping):
            raise GroundingError("PAYLOAD_MALFORMED", "filters 항목은 객체여야 합니다.")
        concept_id = str(item.get("concept") or "").strip()
        op = str(item.get("op") or "").strip()
        value_text = str(item.get("value_text") or "").strip()
        if concept_id not in _FILTER_CONCEPT_FIELDS:
            raise GroundingError(
                "CONCEPT_UNKNOWN", f"'{concept_id}'은(는) 알 수 없는 필터 개념입니다."
            )
        if op not in _FILTER_OPS:
            raise GroundingError("FILTER_OP_UNKNOWN", f"'{op}'은(는) 지원하지 않는 조건입니다.")
        if not value_text:
            raise GroundingError(
                "FILTER_VALUE_EMPTY", f"'{concept_id}' 조건에 비교할 값이 없습니다."
            )

        if single_scope is not None:
            field = catalog.binding(concept_id, single_scope)
            if field is None:
                label = catalog.concepts[concept_id].label_ko
                raise GroundingError(
                    "BINDING_ABSENT_FOR_SCOPE",
                    f"'{label}'({concept_id}) 조건은 {single_scope} 상품군에 존재하지 않습니다.",
                )
        else:
            field = _FILTER_CONCEPT_FIELDS[concept_id]

        value: str | int | float | list[str | int | float | bool]
        if op == "in":
            members = [part.strip() for part in _IN_SPLIT.split(value_text) if part.strip()]
            if not members:
                raise GroundingError(
                    "FILTER_VALUE_EMPTY", f"'{concept_id}' 조건에 비교할 값이 없습니다."
                )
            value = [
                _translate_value(member, field, single_scope, aliases, assumptions)
                for member in members
            ]
        else:
            translated = _translate_value(value_text, field, single_scope, aliases, assumptions)
            value = _coerce_number(translated) if op in {"gte", "lte"} else translated
        conditions.append(Condition(field=field, op=op, value=value))
    return conditions


def _ground_entities(payload: Mapping[str, Any], scopes: list[str]) -> list[Entity]:
    entity_scope = scopes[0] if len(scopes) == 1 else None
    entities: list[Entity] = []
    for raw in _as_list(payload, "entities")[:10]:
        text = str(raw).strip()
        if not text:
            continue
        if _CODES.fullmatch(text):
            entities.append(Entity(code=text, name="", scope=entity_scope))
        else:
            entities.append(Entity(name=text))
    return entities


def _limit(payload: Mapping[str, Any]) -> int:
    raw = payload.get("top_n")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 10
    return max(1, min(50, int(raw)))


def ground_semantic(payload: Mapping[str, Any], *, question: str) -> QueryPlan:
    """Ground a Stage-1 semantic plan into a physical ``QueryPlan`` (fail-closed)."""

    if bool(payload.get("needs_clarification")) or payload.get("intent") == "clarify":
        return _clarify_plan(payload, question)

    intent = str(payload.get("intent") or "").strip()
    if intent not in _INTENTS:
        raise GroundingError("INTENT_UNKNOWN", f"'{intent}'은(는) 지원하지 않는 의도입니다.")
    if intent == "unsupported":
        return QueryPlan(intent="unsupported")
    if intent == "aggregate":
        # The semantic schema carries no aggregation spec; inventing count/avg
        # here would violate the fail-closed contract.
        raise GroundingError(
            "AGGREGATE_UNSUPPORTED", "집계 질의는 2단계 의미 계획에서 아직 지원되지 않습니다."
        )

    direction = str(payload.get("sort_direction") or "none").strip()
    if direction not in {"desc", "asc", "none"}:
        raise GroundingError(
            "SORT_DIRECTION_UNKNOWN", f"'{direction}'은(는) 지원하지 않는 정렬 방향입니다."
        )

    catalog = load_concept_catalog()
    assumptions: list[str] = []
    metric_concepts = _metric_concepts(payload, catalog)
    scopes = _resolve_scopes(payload, metric_concepts, catalog, assumptions)
    metrics = _ground_metrics(metric_concepts, scopes, catalog)
    conditions = _ground_filters(payload, scopes, catalog, assumptions)
    entities = _ground_entities(payload, scopes)

    if intent == "search" and metrics and direction != "none":
        intent = "rank"
    if intent == "rank" and not metrics:
        raise GroundingError("METRIC_REQUIRED", "순위를 정할 지표가 지정되지 않았습니다.")
    if intent == "lookup" and not entities:
        raise GroundingError(
            "ENTITY_REQUIRED", "조회할 상품명이나 상품코드가 지정되지 않았습니다."
        )

    sort: list[SortItem] = []
    if metrics and (intent == "rank" or direction != "none"):
        sort_direction = direction if direction != "none" else "desc"
        sort = [SortItem(field=metric, direction=sort_direction) for metric in metrics]

    return QueryPlan(
        intent=intent,
        scopes=scopes,
        entities=entities,
        filter_groups=[FilterGroup(conditions=conditions)] if conditions else [],
        metrics=metrics,
        sort=sort,
        limit=_limit(payload),
        assumptions=assumptions[:10],
    )
