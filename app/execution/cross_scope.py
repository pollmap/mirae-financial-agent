"""Cross-scope execution: decompose, run per scope, fuse with full disclosure.

A multi-scope metric plan is split into single-scope subplans that run through
the existing, unchanged single-scope machinery (per-scope policy checks,
per-scope common-latest as-of dates, coverage, evidence). Fusion then follows
the comparability mode:

- UNIFIED_RANK: merge and re-rank on the shared concept's raw values.
- SPLIT_PRESENTATION: per-scope sections, no merged ordering.
- EXPLAIN_ONLY: per-scope values side by side, never one ranking.

Scopes without a usable binding are reported with alternatives instead of
blocking the others. There is no refusal path here: the fail-closed property
is "never present without disclosure", enforced by the mandatory disclosure
lines this module emits.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.domain.models import (
    Answerability,
    EvidenceBundle,
    ProductEvidence,
    QueryPlan,
    UniverseEvidence,
)
from app.semantics.capability import (
    CrossScopeDecision,
    evaluate_cross_scope,
    warning_lines_ko,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from app.execution.engine import DuckDBEngine

SCOPE_LABELS_KO = {
    "bond": "국내채권",
    "domestic_etp": "국내 ETF·ETN",
    "overseas_etp": "해외 ETF·ETN",
    "fund": "공모펀드",
}
MODE_LABELS_KO = {
    "UNIFIED_RANK": "통합 순위",
    "SPLIT_PRESENTATION": "상품군별 분리 제시",
    "EXPLAIN_ONLY": "상품군별 병렬 설명(통합 순위 없음)",
}
_MAX_LIMITATIONS = 30


_CURRENCY_PARTITIONED_METRICS = {
    "domestic_etp.aum_last": ("product.currency", "CURR_CD_KRW"),
    "domestic_etp.net_assets": ("product.currency", "CURR_CD_KRW"),
    "domestic_etp.nav_last": ("product.currency", "CURR_CD_KRW"),
    "domestic_etp.close_price": ("product.currency", "CURR_CD_KRW"),
    "overseas_etp.aum_last": ("product.trading_currency", None),
    "overseas_etp.nav_last": ("product.trading_currency", None),
    "overseas_etp.close_price": ("product.trading_currency", None),
}


def _dominant_currency(
    engine: DuckDBEngine, scope: str, field_column: str
) -> tuple[str, float] | None:
    """The single currency value covering the largest share of the scope.

    Only a single non-sentinel currency is safe to infer.  A merely dominant
    value is not enough; mixed currencies must remain split or be clarified.
    """

    column = "trading_currency" if field_column == "product.trading_currency" else "currency"
    with engine._connect() as connection:  # noqa: SLF001 - same execution package
        rows = connection.execute(
            f"SELECT {column}, COUNT(*) AS n FROM product_catalog "
            f"WHERE scope = ? AND {column} IS NOT NULL "
            "GROUP BY 1 ORDER BY n DESC",
            [scope],
        ).fetchall()
    if not rows:
        return None
    rows = [row for row in rows if row[0] not in (None, "", "000", "CURR_CD_000")]
    total = sum(row[1] for row in rows)
    if len(rows) != 1:
        return None
    top_value, top_count = rows[0]
    return str(top_value), top_count / total if total else 0.0


def _subplan(
    engine: DuckDBEngine, plan: QueryPlan, scope: str, metrics: list[str]
) -> tuple[QueryPlan, list[str], list[str]]:
    """Rebind one scope's physical metrics onto a single-scope copy.

    Conditions that do not exist for the target scope (e.g. ETF/ETN type on
    funds) are dropped and reported. A currency-partitioned metric without an
    explicit currency filter gets the scope's dominant currency auto-applied
    (see ``_dominant_currency``); this is reported separately so the
    disclosure block can state it as an inferred assumption, not a silent
    default.
    """

    from app.execution.registry import CATALOG_SCOPE_FIELDS

    payload = plan.model_dump(mode="json")
    payload["scopes"] = [scope]
    payload["metrics"] = metrics

    scope_fields = CATALOG_SCOPE_FIELDS.get(scope, frozenset())
    dropped: list[str] = []
    kept_groups = []
    has_explicit_currency: dict[str, bool] = {}
    for group in payload["filter_groups"]:
        kept_conditions = []
        for condition in group["conditions"]:
            field_name = condition["field"]
            if field_name == "product.scope":
                # Only ever produced by the safe-count scope-routing path
                # (app/service.py::_cross_scope_count_plan), which the engine
                # intercepts before a plan ever reaches this executor -- so
                # this is unreachable today. If that ever changes, route it
                # through the same drop-and-disclose path as any other
                # scope-inapplicable condition (it no longer means anything
                # once the plan has been split into a single-scope subplan)
                # instead of silently vanishing.
                dropped.append(f"{field_name}={condition.get('value')}")
                continue
            applicable = (
                field_name in scope_fields
                or field_name.startswith(f"{scope}.")
                or field_name in ("product.id", "product.name")
            )
            if field_name in ("product.currency", "product.trading_currency"):
                has_explicit_currency[field_name] = True
            if applicable:
                kept_conditions.append(condition)
            else:
                dropped.append(f"{field_name}={condition.get('value')}")
        if kept_conditions:
            kept_groups.append({**group, "conditions": kept_conditions})

    inferred_currency: list[str] = []
    for metric in metrics:
        spec = _CURRENCY_PARTITIONED_METRICS.get(metric)
        if spec is None or has_explicit_currency.get(spec[0]):
            continue
        dominant = _dominant_currency(engine, scope, spec[0])
        if dominant is None:
            continue
        value, share = dominant
        kept_groups.append(
            {
                "join": "AND",
                "conditions": [{"field": spec[0], "op": "eq", "value": value}],
            }
        )
        has_explicit_currency[spec[0]] = True
        inferred_currency.append(
            f"{SCOPE_LABELS_KO.get(scope, scope)} {metric}: 실측 통화의 "
            f"{share * 100:.1f}%가 {value}이어서 이를 기준으로 자동 적용"
        )

    payload["filter_groups"] = kept_groups
    if plan.intent == "rank" and metrics:
        direction = plan.sort[0].direction if plan.sort else "desc"
        payload["sort"] = [
            {"field": metric, "direction": direction, "nulls": "last"}
            for metric in metrics
        ]
    elif plan.sort:
        payload["sort"] = [
            {**item.model_dump(mode="json"), "field": metrics[0]}
            if metrics and item.field not in metrics
            else item.model_dump(mode="json")
            for item in plan.sort
        ]
    payload["aggregations"] = [
        {
            **aggregation.model_dump(mode="json"),
            "field": metrics[0]
            if metrics and aggregation.field not in ("product.id", *metrics)
            else aggregation.field,
        }
        for aggregation in plan.aggregations
    ]
    payload["group_by"] = [key for key in plan.group_by if key != "product.scope"]
    payload["preserved_plan"] = None
    return QueryPlan.model_validate(payload), dropped, inferred_currency


def _item_value(item: ProductEvidence, metric_ids: list[str]) -> Decimal | None:
    for field in item.fields:
        if field.metric_id in metric_ids and field.normalized_value is not None:
            try:
                return Decimal(str(field.normalized_value))
            except (InvalidOperation, ValueError):
                return None
    return None


def _with_cross_field(
    item: ProductEvidence, scope_metrics: list[str], cross_metric_id: str
) -> ProductEvidence:
    """Mirror the scope-specific metric evidence under the plan's cross id.

    The synthetic entry points at the same source cell (same locator and row
    hash); it only re-labels the value so a fused item list renders uniformly.
    """

    extra = []
    for field in item.fields:
        if field.metric_id in scope_metrics:
            extra.append(
                field.model_copy(
                    update={
                        "evidence_id": f"{field.evidence_id}:cross",
                        "metric_id": cross_metric_id,
                    }
                )
            )
            break
    if not extra:
        return item
    return item.model_copy(update={"fields": [*item.fields, *extra]})


def execute_cross_scope(engine: DuckDBEngine, plan: QueryPlan) -> EvidenceBundle:
    decision = evaluate_cross_scope(plan)
    cross_metric_id = plan.metrics[0] if plan.metrics else None

    sub_bundles: dict[str, EvidenceBundle] = {}
    failed_scopes: dict[str, str] = {}
    dropped_by_scope: dict[str, list[str]] = {}
    inferred_currency: list[str] = []
    for scope in plan.scopes:
        metrics = decision.per_scope_metrics.get(scope)
        if metrics is None:
            continue
        sub, dropped, currency_notes = _subplan(engine, plan, scope, metrics)
        if dropped:
            dropped_by_scope[scope] = dropped
        inferred_currency.extend(currency_notes)
        bundle = engine.execute(sub)
        if bundle.answerability in {
            Answerability.FULL,
            Answerability.PARTIAL_WITH_COVERAGE,
            Answerability.NO_RESULT,
        }:
            sub_bundles[scope] = bundle
        else:
            failed_scopes[scope] = (
                (bundle.limitations[0] if bundle.limitations else None)
                or bundle.reason_code
                or "실행 불가"
            )

    disclosure = _disclosure_lines(plan, decision, sub_bundles, failed_scopes)
    for scope, dropped in dropped_by_scope.items():
        disclosure.append(
            f"{SCOPE_LABELS_KO.get(scope, scope)}에 적용할 수 없는 조건은 해당 "
            f"상품군에서만 제외했습니다: {', '.join(dropped)}"
        )
    disclosure.extend(inferred_currency)

    if not sub_bundles:
        limitation = " / ".join(
            [*decision.absent_scopes.values(), *failed_scopes.values()]
        ) or "요청 지표를 실행할 수 있는 상품군이 없습니다."
        bundle = engine.empty_bundle(
            plan,
            answerability=Answerability.UNAVAILABLE,
            reason_code="SOURCE_FIELD_ABSENT",
            limitation=limitation,
        )
        bundle.limitations = (bundle.limitations + disclosure)[:_MAX_LIMITATIONS]
        return bundle

    fused_items: list[ProductEvidence] = []
    unparsed_count = 0
    attempted_unified_rank = decision.mode == "UNIFIED_RANK" and cross_metric_id is not None
    if attempted_unified_rank:
        ranked: list[tuple[Decimal, str, ProductEvidence]] = []
        for scope, bundle in sub_bundles.items():
            scope_metrics = decision.per_scope_metrics[scope]
            for item in bundle.items:
                value = _item_value(item, scope_metrics)
                if value is None:
                    unparsed_count += 1
                    continue
                ranked.append(
                    (value, item.product_uid, _with_cross_field(item, scope_metrics, cross_metric_id))
                )
        descending = not plan.sort or plan.sort[0].direction == "desc"
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=descending)
        for position, (_, _, item) in enumerate(ranked[: plan.limit], start=1):
            fused_items.append(item.model_copy(update={"rank": position}))

    # A concept whose values can't be parsed as numbers (text/rating/category)
    # would otherwise fuse to zero items and report a hollow
    # PARTIAL_WITH_COVERAGE "success". capability.py already prevents this for
    # the reachable single-bound-scope case; this is the general fallback for
    # any other numeric-fusion failure: fall back to presenting each scope's
    # real, already-retrieved items instead of discarding them.
    fallback_to_split = (
        attempted_unified_rank and not fused_items and any(bundle.items for bundle in sub_bundles.values())
    )
    if fallback_to_split:
        decision.mode = "SPLIT_PRESENTATION"
        decision.basis_notes.append(
            f"{cross_metric_id}은(는) 값이 숫자로 통합 정렬되지 않아 상품군별로 분리해 표시합니다"
            + (f" ({unparsed_count:,}건 미해석)" if unparsed_count else "") + "."
        )

    if not attempted_unified_rank or fallback_to_split:
        # SPLIT_PRESENTATION / EXPLAIN_ONLY: keep per-scope ordering, group by
        # scope, keep each item's within-scope rank.
        for scope in plan.scopes:
            bundle = sub_bundles.get(scope)
            if bundle is None:
                continue
            scope_metrics = decision.per_scope_metrics.get(scope, [])
            for item in bundle.items[: plan.limit]:
                fused = (
                    _with_cross_field(item, scope_metrics, cross_metric_id)
                    if cross_metric_id is not None and scope_metrics
                    else item
                )
                fused_items.append(fused)
        # Up to plan.limit per scope is intentional (a "top 5 bonds and top 5
        # funds side by side" EXPLAIN_ONLY answer should show 10, not 5) -- but
        # EvidenceBundle.items has a hard max_length of 50 (app/domain/models.py
        # ProductEvidence list), so the total must still be clamped there
        # regardless of scope count. A bare `plan.limit * len(sub_bundles)`
        # cap can exceed 50 (up to 4 scopes x limit 50 = 200) and raise an
        # unhandled pydantic ValidationError instead of returning an answer.
        fused_items = fused_items[: min(plan.limit * len(sub_bundles), 50)]

    aggregates = [
        aggregate
        for bundle in sub_bundles.values()
        for aggregate in bundle.aggregates
    ][:100]
    retrieval_trace = [
        trace
        for bundle in sub_bundles.values()
        for trace in bundle.retrieval_trace
    ][:24]

    universe = _multi_universe(sub_bundles)
    limitations: list[str] = list(disclosure)
    for scope, bundle in sub_bundles.items():
        label = SCOPE_LABELS_KO.get(scope, scope)
        for line in bundle.limitations:
            entry = f"[{label}] {line}"
            if entry not in limitations:
                limitations.append(entry)

    return EvidenceBundle(
        execution_id=str(uuid.uuid4()),
        answerability=Answerability.PARTIAL_WITH_COVERAGE,
        reason_code="CROSS_SCOPE_SOURCE_LITERAL",
        result_count=len(fused_items) if fused_items else len(aggregates),
        universe=universe,
        coverage=None,
        items=fused_items,
        aggregates=aggregates,
        retrieval_trace=retrieval_trace,
        limitations=limitations[:_MAX_LIMITATIONS],
    )


def _multi_universe(sub_bundles: dict[str, EvidenceBundle]) -> UniverseEvidence:
    raw = serving = eligible = excluded = 0
    parts: list[str] = []
    for scope, bundle in sub_bundles.items():
        if bundle.universe is None:
            continue
        raw += bundle.universe.raw_count
        serving += bundle.universe.serving_count
        eligible += bundle.universe.eligible_count
        excluded += bundle.universe.excluded_count
        parts.append(
            f"{SCOPE_LABELS_KO.get(scope, scope)} serving {bundle.universe.serving_count:,}"
            f"·eligible {bundle.universe.eligible_count:,}"
        )
    return UniverseEvidence(
        scope="multi",
        raw_count=raw,
        serving_count=serving,
        eligible_count=eligible,
        excluded_count=excluded,
        filter_summary="; ".join(parts)[:2000] or "per-scope universes unavailable",
    )


def _disclosure_lines(
    plan: QueryPlan,
    decision: CrossScopeDecision,
    sub_bundles: dict[str, EvidenceBundle],
    failed_scopes: dict[str, str],
) -> list[str]:
    lines = [
        "[교차 상품군 응답: "
        + MODE_LABELS_KO.get(decision.mode, decision.mode)
        + " — "
        + ", ".join(
            SCOPE_LABELS_KO.get(scope, scope) for scope in sub_bundles
        )
        + "]"
    ]
    lines.extend(decision.basis_notes)
    lines.extend(warning_lines_ko(decision.warnings))
    for scope, bundle in sub_bundles.items():
        if bundle.coverage is not None:
            lines.append(
                f"{SCOPE_LABELS_KO.get(scope, scope)} 지표 보유: "
                f"{bundle.coverage.numerator:,}/{bundle.coverage.denominator:,}"
            )
    for scope, note in decision.absent_scopes.items():
        lines.append(f"{SCOPE_LABELS_KO.get(scope, scope)}: {note}")
    for scope, note in failed_scopes.items():
        lines.append(f"{SCOPE_LABELS_KO.get(scope, scope)} 실행 제한: {note}")
    if decision.unresolved_metrics:
        lines.append(
            "다음 지표는 개념 매핑이 없어 제외했습니다: "
            + ", ".join(decision.unresolved_metrics)
        )
    return lines
