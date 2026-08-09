"""Strict domain models for the grounded financial-product agent."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Scope = Literal["bond", "domestic_etp", "overseas_etp", "fund"]
Intent = Literal[
    "lookup", "search", "rank", "compare", "aggregate", "explain", "clarify", "unsupported"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Entity(StrictModel):
    name: str | None = Field(default=None, max_length=300)
    code: str | None = Field(default=None, max_length=100)
    scope: Scope | None = None

    @model_validator(mode="after")
    def require_identity(self) -> Entity:
        if not self.name and not self.code:
            raise ValueError("an entity requires a name or code")
        return self


ConditionScalar = str | int | float | bool | None


class Condition(StrictModel):
    field: str = Field(min_length=1, max_length=64)
    op: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "between",
        "in",
        "contains",
        "starts_with",
        "is_null",
        "is_not_null",
    ]
    value: ConditionScalar | list[str | int | float | bool] = None
    value2: ConditionScalar | list[str | int | float | bool] = None
    unit: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_operands(self) -> Condition:
        for operand in (self.value, self.value2):
            if isinstance(operand, str) and len(operand) > 500:
                raise ValueError("condition scalar strings are limited to 500 characters")
            if isinstance(operand, list):
                if len(operand) > 30:
                    raise ValueError("condition lists are limited to 30 members")
                if any(isinstance(item, str) and len(item) > 300 for item in operand):
                    raise ValueError(
                        "condition list string members are limited to 300 characters"
                    )
        if self.op in {"is_null", "is_not_null"}:
            return self
        if self.op == "in":
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("in requires a non-empty list value")
            return self
        if self.op == "between":
            if (
                self.value is None
                or self.value2 is None
                or isinstance(self.value, list)
                or isinstance(self.value2, list)
            ):
                raise ValueError("between requires two scalar operands")
            return self
        if self.value is None:
            raise ValueError(f"{self.op} requires value")
        if isinstance(self.value, list):
            raise ValueError(f"{self.op} requires a scalar value")
        return self


class FilterGroup(StrictModel):
    join: Literal["AND"] = "AND"
    conditions: list[Condition] = Field(min_length=1, max_length=12)


class SortItem(StrictModel):
    field: str = Field(min_length=1, max_length=64)
    direction: Literal["asc", "desc"]
    nulls: Literal["last"] = "last"


class Aggregation(StrictModel):
    function: Literal["count", "sum", "avg", "min", "max"]
    field: str | None = Field(default=None, max_length=64)
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    distinct: bool = False


class ClarificationOption(StrictModel):
    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=300)


class ClarificationRequest(StrictModel):
    question: str = Field(min_length=1, max_length=500)
    missing_slots: list[str] = Field(min_length=1, max_length=8)
    options: list[ClarificationOption] = Field(default_factory=list, max_length=12)
    preserved_plan: dict[str, Any]
    clarification_token: str | None = Field(default=None, max_length=10000)


class QueryPlan(StrictModel):
    version: Literal["1.1"] = "1.1"
    intent: Intent
    scopes: list[Scope] = Field(default_factory=list, max_length=4)
    entities: list[Entity] = Field(default_factory=list, max_length=10)
    filter_groups: list[FilterGroup] = Field(default_factory=list, max_length=5)
    groups_join: Literal["AND", "OR"] = "AND"
    metrics: list[str] = Field(default_factory=list, max_length=12)
    aggregations: list[Aggregation] = Field(default_factory=list, max_length=8)
    sort: list[SortItem] = Field(default_factory=list, max_length=4)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=10, ge=1, le=50)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)
    missing_slots: list[str] = Field(default_factory=list, max_length=8)
    clarification_options: list[ClarificationOption] = Field(default_factory=list, max_length=12)
    preserved_plan: dict[str, Any] | None = None

    @model_validator(mode="after")
    def semantic_shape(self) -> QueryPlan:
        self.scopes = list(dict.fromkeys(self.scopes))
        self.metrics = list(dict.fromkeys(self.metrics))
        self.group_by = list(dict.fromkeys(self.group_by))
        if (self.intent == "clarify") != self.needs_clarification:
            raise ValueError("intent=clarify and needs_clarification=true must be used together")
        if self.needs_clarification:
            if not self.clarification_question:
                raise ValueError("clarification requires a concrete question")
            if not self.missing_slots:
                raise ValueError("clarification requires at least one missing slot")
            option_count = len(self.clarification_options)
            maximum = 4
            if not 2 <= option_count <= maximum:
                raise ValueError(
                    f"clarification requires 2..{maximum} concrete options for these slots"
                )
        elif self.clarification_options:
            raise ValueError("non-clarification plans cannot include clarification options")
        if self.intent == "rank":
            if not self.metrics:
                raise ValueError("rank requires at least one metric")
            if len(self.sort) != len(self.metrics):
                raise ValueError("rank requires one explicit sort item per metric")
            if [item.field for item in self.sort] != self.metrics:
                raise ValueError("rank sort order must exactly match metric priority")
        if self.intent == "aggregate" and not self.aggregations:
            raise ValueError("aggregate requires at least one aggregation")
        aliases = [item.alias for item in self.aggregations]
        if len(aliases) != len(set(aliases)):
            raise ValueError("aggregation aliases must be unique")
        if self.intent == "lookup" and not self.entities:
            raise ValueError("lookup requires at least one entity")
        return self

    def clarification(self, token: str | None = None) -> ClarificationRequest | None:
        if not self.needs_clarification:
            return None
        return ClarificationRequest(
            question=self.clarification_question or "조건을 더 알려주세요.",
            missing_slots=self.missing_slots,
            options=self.clarification_options,
            preserved_plan=self.preserved_plan or self.model_dump(exclude={"preserved_plan"}),
            clarification_token=token,
        )


class Answerability(StrEnum):
    FULL = "FULL"
    PARTIAL_WITH_COVERAGE = "PARTIAL_WITH_COVERAGE"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    NO_RESULT = "NO_RESULT"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPARABLE = "INCOMPARABLE"
    SAFETY_LIMITED = "SAFETY_LIMITED"
    DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"


class UniverseEvidence(StrictModel):
    scope: Literal["bond", "domestic_etp", "overseas_etp", "fund", "multi"]
    raw_count: int = Field(ge=0)
    serving_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    filter_summary: str = Field(max_length=2000)


class CoverageEvidence(StrictModel):
    metric_id: str | None = Field(default=None, max_length=100)
    raw_count: int = Field(ge=0)
    serving_count: int = Field(ge=0)
    present_count: int = Field(ge=0)
    valid_count: int = Field(ge=0)
    rankable_count: int = Field(ge=0)
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    basis: str = Field(min_length=1, max_length=1000)


class FieldEvidence(StrictModel):
    # Raw source cells may contain meaningful leading/trailing whitespace.  Do
    # not let the shared convenience normalizer mutate evidentiary values.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    evidence_id: str = Field(min_length=1, max_length=120)
    metric_id: str = Field(min_length=1, max_length=100)
    source_table_id: Literal["PRBD01N001", "PREF01N001", "PREF02N001", "PRFD01N001"]
    source_file: str = Field(min_length=1, max_length=500)
    source_sheet: str = Field(min_length=1, max_length=100)
    source_excel_row: int = Field(ge=2)
    source_field: str = Field(min_length=1, max_length=100)
    raw_value: str | int | float | bool | None
    normalized_value: str | int | float | bool | None
    unit: str | None = Field(default=None, max_length=100)
    as_of_date: str | None = None
    as_of_status: Literal["AVAILABLE", "DATASET_SNAPSHOT_ONLY", "UNAVAILABLE"]
    source_row_hash: str = Field(pattern=r"^[a-f0-9]{32,64}$")
    quality_flags: list[str] = Field(default_factory=list)


class ProductEvidence(StrictModel):
    product_uid: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=500)
    rank: int | None = Field(default=None, ge=1)
    fields: list[FieldEvidence] = Field(min_length=1, max_length=100)


class AggregateEvidence(StrictModel):
    aggregate_id: str = Field(min_length=1, max_length=100)
    group_key: str | int | float | bool | None = None
    value: str | int | float | bool | None = None
    unit: str | None = Field(default=None, max_length=100)
    source_table_ids: list[Literal["PRBD01N001", "PREF01N001", "PREF02N001", "PRFD01N001"]] = Field(
        min_length=1, max_length=4
    )
    source_fields: list[str] = Field(min_length=1, max_length=20)
    source_row_count: int = Field(ge=0)
    query_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    as_of_date: str | None = None


class CalculationEvidence(StrictModel):
    operation: Literal["lookup", "filter", "sort", "compare", "aggregate"]
    formula: str = Field(max_length=4000)
    formula_version: str = Field(max_length=100)
    rounding: str | None = Field(default=None, max_length=300)
    tie_breakers: list[str] = Field(default_factory=list, max_length=10)


class RetrievalTrace(StrictModel):
    """Bounded, non-sensitive metadata for one retrieval channel decision."""

    channel: Literal["sql", "exact_alias", "graph", "lexical", "vector"]
    status: Literal["used", "validated", "fallback", "unavailable", "skipped"]
    reason: str = Field(min_length=1, max_length=300)
    scope: Scope | None = None
    candidate_count: int = Field(default=0, ge=0)
    verified_count: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(default=0.0, ge=0, le=60_000)
    observed_at_utc: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        pattern=r"^\d{4}-\d{2}-\d{2}T.*(?:Z|\+00:00)$",
        max_length=40,
    )
    data_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    fallback_reason: str | None = Field(default=None, max_length=300)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


class ConditionLedgerEntry(StrictModel):
    """One material user condition and how it reached execution.

    This is deliberately verification metadata, not model reasoning.  It lets
    the server prove that a requested qualifier was grounded, explicitly
    clarified, declared unavailable, or kept separate for comparability.
    """

    condition_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal[
        "scope",
        "intent",
        "metric",
        "product_type",
        "party",
        "region",
        "asset_type",
        "strategy",
        "benchmark",
        "currency",
        "risk_grade",
        "comparison_basis",
    ]
    requested_text: str = Field(min_length=1, max_length=160)
    status: Literal[
        "grounded",
        "clarification_required",
        "unavailable",
        "not_comparable",
    ]
    grounded_fields: list[str] = Field(default_factory=list, max_length=8)
    note: str | None = Field(default=None, max_length=300)


class EvidenceBundle(StrictModel):
    version: Literal["1.1"] = "1.1"
    execution_id: str = Field(min_length=1, max_length=100)
    data_snapshot_date: str = "2026-07-11"
    answerability: Answerability
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    result_count: int = Field(ge=0)
    universe: UniverseEvidence
    coverage: CoverageEvidence | None = None
    clarification: ClarificationRequest | None = None
    calculation: CalculationEvidence | None = None
    aggregates: list[AggregateEvidence] = Field(default_factory=list, max_length=100)
    items: list[ProductEvidence] = Field(default_factory=list, max_length=50)
    retrieval_trace: list[RetrievalTrace] = Field(default_factory=list, max_length=24)
    condition_ledger: list[ConditionLedgerEntry] = Field(default_factory=list, max_length=40)
    limitations: list[str] = Field(default_factory=list, max_length=30)


class OrganizerResponse(StrictModel):
    question_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    retrieved_context: str = Field(max_length=500000)
    think_trace: str = Field(max_length=12000)
    answer: str = Field(max_length=30000)
