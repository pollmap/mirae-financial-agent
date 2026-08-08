"""Cross-scope capability decisions: never refuse, always choose a mode.

Replaces the hard multi-scope lock that previously returned INCOMPARABLE for
any cross-scope metric plan. The comparability matrix decides between a
unified rank, a per-scope split presentation, or a side-by-side explanation;
scopes whose binding is ABSENT are routed to an alternatives note instead of
blocking the remaining scopes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models import QueryPlan
from app.semantics.concepts import (
    MODE_STRENGTH,
    load_comparability,
    load_concept_catalog,
)

WARNING_TEXT_KO = {
    "UNIT_STATUS_PENDING": "지표 단위(퍼센트포인트·통화 등)가 공식 확정 전이라 원본 수치 기준입니다.",
    "ZERO_POLICY_DIVERGENT": "상품군별 0값 해석 정책이 서로 다를 수 있어 0값 개수를 함께 공시합니다.",
    "ZERO_MEANING_UNKNOWN": "0값의 공식 의미가 확정되지 않아 0값 개수를 함께 공시합니다.",
    "AS_OF_DIVERGENT": "상품군별 원천 기준일이 달라 상품별 기준일을 함께 표시합니다.",
    "SENTINEL_EXCLUDED": "국내 ETP 수익률의 -100 sentinel 값은 순위에서 제외하고 개수를 공시합니다.",
    "CURRENCY_MIXED": "통화가 서로 달라 통합 순위 대신 상품군별로 분리해 제시합니다.",
    "RISK_SCALE_MISMATCH": "위험 척도가 서로 달라 하나의 순위로 합치지 않고 나란히 설명합니다.",
    "METRIC_BASIS_MISMATCH": "지표의 성격이 서로 달라 하나의 순위로 합치지 않고 나란히 설명합니다.",
    "SOURCE_FIELD_ABSENT": "해당 상품군에는 이 지표의 원본 필드가 없습니다.",
    "UNUSABLE_CONSTANT": "해당 상품군의 원본값이 전부 동일(0)하여 순위에 사용할 수 없습니다.",
    "SEVERELY_PARTIAL": "해당 상품군의 지표 보유율이 극히 낮아 실질 비교가 어렵습니다.",
    "VENUE_DIFFERENT": "거래소·시장이 서로 달라 거래량의 시장 맥락이 다를 수 있습니다.",
}


@dataclass(slots=True)
class CrossScopeDecision:
    mode: str  # UNIFIED_RANK | SPLIT_PRESENTATION | EXPLAIN_ONLY
    concept_ids: list[str]
    per_scope_metrics: dict[str, list[str]] = field(default_factory=dict)
    absent_scopes: dict[str, str] = field(default_factory=dict)  # scope -> note
    warnings: list[str] = field(default_factory=list)
    basis_notes: list[str] = field(default_factory=list)
    unresolved_metrics: list[str] = field(default_factory=list)

    @property
    def executable_scopes(self) -> list[str]:
        return [scope for scope, metrics in self.per_scope_metrics.items() if metrics]


def evaluate_cross_scope(plan: QueryPlan) -> CrossScopeDecision:
    """Resolve a multi-scope plan into an execution mode. Never refuses."""

    catalog = load_concept_catalog()
    matrix = load_comparability()

    concept_ids: list[str] = []
    unresolved: list[str] = []
    for metric in plan.metrics:
        concept = catalog.resolve_metric_concept(metric)
        if concept is None:
            unresolved.append(metric)
        elif concept not in concept_ids:
            concept_ids.append(concept)

    decision = CrossScopeDecision(
        mode="SPLIT_PRESENTATION" if not concept_ids else "UNIFIED_RANK",
        concept_ids=concept_ids,
        unresolved_metrics=unresolved,
    )

    if not concept_ids:
        # Metric-free multi-scope plan (listing/search): per-scope sections.
        for scope in plan.scopes:
            decision.per_scope_metrics[scope] = []
        decision.basis_notes.append(
            "지표 없이 여러 상품군을 함께 조회하여 상품군별로 분리해 제시합니다."
        )
        return decision

    if len(concept_ids) > 1:
        # Different concepts across scopes (e.g. 표면금리 vs 1년 수익률):
        # side-by-side explanation, one concept per scope that supports it.
        decision.mode = "EXPLAIN_ONLY"
        if "METRIC_BASIS_MISMATCH" not in decision.warnings:
            decision.warnings.append("METRIC_BASIS_MISMATCH")

    weakest = "UNIFIED_RANK"
    for concept_id in concept_ids:
        bound_scopes: list[str] = []
        for scope in plan.scopes:
            binding = catalog.binding(concept_id, scope)
            if binding is None:
                concept = catalog.concepts[concept_id]
                decision.absent_scopes.setdefault(
                    scope,
                    f"{concept.label_ko}은(는) 해당 상품군에 원본 필드가 없습니다.",
                )
                continue
            bound_scopes.append(scope)
            decision.per_scope_metrics.setdefault(scope, [])
            if binding not in decision.per_scope_metrics[scope]:
                decision.per_scope_metrics[scope].append(binding)
        # Nothing to unify a single (or zero) bound scope against -- e.g.
        # credit_rating binds only to bond, so a bond+fund plan has no
        # pairwise comparison to run at all. Leaving `weakest` at its
        # UNIFIED_RANK default here would try to numerically fuse a ranking
        # that was never compared against anything, which for a text-valued
        # concept (rating/scale/category) silently produces zero fused items
        # instead of the single scope's real answer.
        if len(bound_scopes) <= 1 and MODE_STRENGTH.get("SPLIT_PRESENTATION", 1) < MODE_STRENGTH.get(
            weakest, 3
        ):
            weakest = "SPLIT_PRESENTATION"
        for index, scope_a in enumerate(bound_scopes):
            for scope_b in bound_scopes[index + 1 :]:
                entry = matrix.get((concept_id, frozenset({scope_a, scope_b})))
                if entry is None:
                    # Unlisted pair: be conservative but never refuse.
                    pair_mode = "SPLIT_PRESENTATION"
                    note = (
                        f"{concept_id}의 {scope_a}·{scope_b} 비교 기준이 등록되지 않아 "
                        "상품군별로 분리해 제시합니다."
                    )
                    decision.basis_notes.append(note)
                else:
                    pair_mode = entry.mode
                    if entry.mode == "SCOPE_ABSENT":
                        # Matrix marks one side unusable; the loop above already
                        # captured truly absent bindings, so this covers
                        # unusable-constant style rows.
                        pair_mode = "SPLIT_PRESENTATION"
                    if entry.basis_note_ko and entry.basis_note_ko not in decision.basis_notes:
                        decision.basis_notes.append(entry.basis_note_ko)
                    for code in entry.warnings:
                        if code not in decision.warnings:
                            decision.warnings.append(code)
                if MODE_STRENGTH.get(pair_mode, 1) < MODE_STRENGTH.get(weakest, 3):
                    weakest = pair_mode

    if len(concept_ids) == 1:
        decision.mode = weakest

    for scope, metrics in list(decision.per_scope_metrics.items()):
        if not metrics:
            del decision.per_scope_metrics[scope]

    return decision


def warning_lines_ko(codes: list[str]) -> list[str]:
    return [WARNING_TEXT_KO[code] for code in codes if code in WARNING_TEXT_KO]
