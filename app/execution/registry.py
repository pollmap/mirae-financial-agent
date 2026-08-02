"""Executable allow-lists derived from the audited registries."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.domain.models import Answerability, QueryPlan

CATALOG_FIELD_MAP = {
    "product.id": "product_id",
    "product.name": "name",
    "product.internal_type": "internal_type",
    "product.market": "market",
    "product.currency": "currency",
    "product.trading_currency": "trading_currency",
    "product.manager": "manager",
    "product.manager_code": "manager_code",
    "product.issuer": "issuer",
    "product.asset_class": "asset_type",
    "product.investment_region": "region",
    "product.country_code": "country_code",
    "product.pension_trade_eligible": "pension_eligible",
    # Compatibility aliases retained until the provisional API contract is frozen.
    "product.asset_type": "asset_type",
    "product.region": "region",
    "product.risk_grade": "risk_grade",
    "product.sale_status": "sale_status",
    "product.public_private": "public_private",
    "product.pension_eligible": "pension_eligible",
    "product.benchmark": "benchmark",
    "product.strategy": "strategy",
    "bond.exchange": "market",
    "bond.kind": "bond_kind",
    "fund.domestic_overseas_class": "domestic_overseas_class",
}

_IDENTITY_CATALOG_FIELDS = {
    "product.id",
    "product.name",
    "product.currency",
    "product.trading_currency",
    "product.asset_class",
    "product.asset_type",
}
CATALOG_SCOPE_FIELDS = {
    "bond": {
        *_IDENTITY_CATALOG_FIELDS,
        "product.market",
        "product.issuer",
        "product.country_code",
        "product.risk_grade",
        "bond.exchange",
        "bond.kind",
    },
    "domestic_etp": {
        *_IDENTITY_CATALOG_FIELDS,
        "product.internal_type",
        "product.market",
        "product.manager",
        "product.investment_region",
        "product.region",
        "product.strategy",
        "product.risk_grade",
        "product.sale_status",
        "product.pension_trade_eligible",
        "product.pension_eligible",
        "product.benchmark",
    },
    "overseas_etp": {
        *_IDENTITY_CATALOG_FIELDS,
        "product.internal_type",
        "product.market",
        "product.manager",
        "product.investment_region",
        "product.region",
        "product.strategy",
        "product.sale_status",
        "product.benchmark",
    },
    "fund": {
        *_IDENTITY_CATALOG_FIELDS,
        "product.manager_code",
        "product.investment_region",
        "product.region",
        "product.risk_grade",
        "product.sale_status",
        "product.public_private",
        "product.benchmark",
        "fund.domestic_overseas_class",
    },
}

CATALOG_FILTER_OPERATORS = {
    "eq",
    "ne",
    "in",
    "contains",
    "starts_with",
    "is_null",
    "is_not_null",
}
NUMERIC_METRIC_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "in",
    "is_null",
    "is_not_null",
}
TEXT_METRIC_OPERATORS = {
    "eq",
    "ne",
    "in",
    "contains",
    "starts_with",
    "is_null",
    "is_not_null",
}
DATE_METRIC_OPERATORS = {*TEXT_METRIC_OPERATORS, "gt", "gte", "lt", "lte", "between"}
TEXT_UNITS = {"CODE", "RATING", "TEXT", "RISK_SCALE", "UNAVAILABLE"}
ALLOWED_GROUP_BY = {"product.internal_type", "product.scope"}
ALLOWED_ASSUMPTIONS = {
    "count_basis=raw",
    "count_basis=quarantine",
    "count_basis=fund_attribute",
    "metric_count_basis=source_present",
}
_CROSS_SCOPE_COUNT_FILTERS = {
    "product.internal_type": {"ETF", "ETN"},
    "product.public_private": {"공모", "사모"},
}
_CROSS_SCOPE_FILTER_SCOPES = {
    "product.internal_type": {"domestic_etp", "overseas_etp"},
    "product.public_private": {"fund"},
}
_ADDITIVE_SUM_METRICS = {
    "bond.buyable_quantity",
    "domestic_etp.aum_last",
    "domestic_etp.net_assets",
    "domestic_etp.volume_1d",
    "overseas_etp.aum_last",
    "overseas_etp.volume_1d",
}
_NON_AVERAGEABLE_METRICS = {"fund.net_assets"}
_DECIMAL_MAX = Decimal("9999999999999999999999999999.9999999999")
_RETURN_PERIOD_ORDER = ("1d", "1w", "1m", "3m", "6m", "ytd", "18m", "1y", "2y", "3y", "5y")


def canonicalize_numeric_operand(value: Any) -> Decimal:
    """Return a finite DECIMAL(38,10)-safe operand used by policy and SQL.

    DuckDB's serving numeric column is DECIMAL(38,10).  Validation and
    execution must therefore use one canonical representation; accepting a
    comma-formatted string during policy evaluation but binding the original
    string would otherwise turn a controlled user error into an HTTP 500.
    """

    if isinstance(value, bool) or value is None:
        raise ValueError("numeric operand must be a finite decimal")
    text = str(value).strip()
    if not text:
        raise ValueError("numeric operand must not be empty")
    if "," in text and not re.fullmatch(
        r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d*)?(?:[eE][+-]?\d+)?", text
    ):
        raise ValueError("numeric operand has malformed thousands separators")
    text = text.replace(",", "")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric operand is not a decimal") from exc
    if not number.is_finite():
        raise ValueError("numeric operand must be finite")
    if number == 0:
        return Decimal(0)
    if number.copy_abs() > _DECIMAL_MAX:
        raise ValueError("numeric operand is outside DECIMAL(38,10)")
    digits = list(number.as_tuple().digits)
    exponent = number.as_tuple().exponent
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if exponent < -10:
        raise ValueError("numeric operand is outside DECIMAL(38,10)")
    # DuckDB's Python binding interprets Decimal('1E+3') as DECIMAL(4,3)
    # value 1.000 rather than 1000.  Force fixed-point notation so the value
    # validated here is exactly the value bound by the executor.
    fixed = format(number, "f")
    if "." in fixed:
        fixed = fixed.rstrip("0").rstrip(".")
    return Decimal(fixed)


def _metric_period(metric_id: str, unit_status: str) -> str:
    if unit_status == "YYYYMMDD" or metric_id.endswith(("issue_date", "maturity_date")):
        return "date"
    suffix = metric_id.rsplit("return_", 1)[-1] if ".return_" in metric_id else ""
    return suffix or "point_in_time"


def _quality_status(row: dict[str, str]) -> str:
    ranking = row["ranking_policy"]
    source_field = row["source_field"].strip().upper()
    if source_field in {"", "NONE"} or ranking == "UNAVAILABLE":
        return "UNAVAILABLE"
    if ranking == "BLOCKED_CONSTANT":
        return "UNUSABLE_CONSTANT"
    if ranking == "BLOCKED_LOW_COVERAGE":
        return "SEVERELY_PARTIAL"
    if ranking == "BLOCKED_PENDING_ZERO_POLICY":
        return "PENDING_ZERO_POLICY"
    if ranking == "BLOCKED_PENDING_ORDER":
        return "PENDING_ORDER_POLICY"
    if "OUTLIER_REVIEW" in ranking:
        return "USABLE_WITH_OUTLIER_REVIEW"
    if ranking.startswith("BLOCKED"):
        return ranking
    return "USABLE"


@dataclass(frozen=True)
class MetricPolicy:
    metric_id: str
    scope: str
    source_table: str
    source_field: str | None
    period: str
    unit_status: str
    allowed_ops: frozenset[str]
    filter_policy: str
    sort_policy: str
    quality_policy: str
    zero_policy: str
    ranking_allowed: bool
    cross_product_allowed: bool
    quality_status: str
    coverage_count: int
    coverage_ratio: float
    notes: str

    @property
    def value_kind(self) -> str:
        if self.period == "date" or self.unit_status == "YYYYMMDD":
            return "date"
        if self.unit_status in TEXT_UNITS:
            return "text"
        return "numeric"

    @property
    def requires_currency_partition(self) -> bool:
        """Whether ranking/aggregation would otherwise mix monetary units."""

        return any(
            marker in self.unit_status
            for marker in ("CURRENCY", "KRW", "PRICE")
        ) or self.metric_id.endswith((".issue_amount", ".net_assets", ".aum_last"))


@dataclass(frozen=True)
class PlanPolicyDecision:
    allowed: bool
    answerability: Answerability
    reason_code: str | None = None
    limitation: str | None = None


class MetricRegistry:
    def __init__(self, policies: dict[str, MetricPolicy]) -> None:
        self._policies = policies

    @classmethod
    def load(cls, path: Path | None = None) -> MetricRegistry:
        registry_path = path or PROJECT_ROOT / "registry" / "metric_policy_v1.csv"
        canonical_path = PROJECT_ROOT / "registry" / "canonical_fields_v1.csv"
        with canonical_path.open(encoding="utf-8-sig", newline="") as handle:
            canonical_rows = list(csv.DictReader(handle))
        canonical_by_id = {row["canonical_field"]: row for row in canonical_rows}
        canonical_by_source = {
            (row["source_table"], row["source_field"]): row for row in canonical_rows
        }
        policies: dict[str, MetricPolicy] = {}
        with registry_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                source_field = row["source_field"].strip()
                serving_present = row["serving_present_count"].strip()
                serving_denominator = int(row["serving_denominator"])
                ranking_policy = row["ranking_policy"]
                canonical = canonical_by_id.get(row["metric_id"]) or canonical_by_source.get(
                    (row["source_table"], source_field)
                )
                policy = MetricPolicy(
                    metric_id=row["metric_id"],
                    scope=row["scope"],
                    source_table=row["source_table"],
                    source_field=(None if source_field.upper() in {"", "NONE"} else source_field),
                    period=_metric_period(row["metric_id"], row["unit_status"]),
                    unit_status=row["unit_status"],
                    allowed_ops=frozenset(
                        canonical["allowed_ops"].split("|") if canonical else []
                    ),
                    filter_policy=canonical["filter_policy"] if canonical else "UNAVAILABLE",
                    sort_policy=canonical["sort_policy"] if canonical else ranking_policy,
                    quality_policy=canonical["quality_policy"] if canonical else row["zero_policy"],
                    zero_policy=row["zero_policy"],
                    ranking_allowed=(
                        ranking_policy.startswith("ALLOWED") or ranking_policy == "TYPE_CONDITIONAL"
                    ),
                    cross_product_allowed=row["cross_product_policy"] == "ALLOWED",
                    quality_status=_quality_status(row),
                    coverage_count=(int(serving_present) if serving_present.isdigit() else 0),
                    coverage_ratio=(
                        int(serving_present) / serving_denominator
                        if serving_present.isdigit() and serving_denominator
                        else 0.0
                    ),
                    notes=row["notes"],
                )
                policies[policy.metric_id] = policy
        return cls(policies)

    def get(self, metric_id: str) -> MetricPolicy | None:
        return self._policies.get(metric_id)

    def require(self, metric_id: str) -> MetricPolicy:
        policy = self.get(metric_id)
        if policy is None:
            raise ValueError(f"metric is not allow-listed: {metric_id}")
        return policy

    def available_return_periods(self, scope: str) -> tuple[str, ...]:
        """Return only source-backed return periods usable for ranking.

        A field that is absent, locked, or constant is not a meaningful option
        in a clarification prompt.  In particular, the overseas ETP registry
        currently exposes no usable return period: its one populated return
        field is an all-zero constant and the longer-period source fields are
        absent.
        """

        periods = {
            policy.period
            for policy in self._policies.values()
            if policy.scope == scope
            and ".return_" in policy.metric_id
            and policy.source_field is not None
            and policy.ranking_allowed
            and policy.quality_status in {"USABLE", "USABLE_WITH_OUTLIER_REVIEW"}
        }
        return tuple(period for period in _RETURN_PERIOD_ORDER if period in periods)

    @staticmethod
    def _is_safe_cross_scope_count(plan: QueryPlan) -> bool:
        """Allow only dimension-free, separately grouped product counts."""

        if not (
            len(plan.scopes) > 1
            and plan.intent == "aggregate"
            and plan.group_by == ["product.scope"]
            and not plan.metrics
            and not plan.entities
            and not plan.sort
            and not plan.assumptions
            and len(plan.aggregations) == 1
        ):
            return False
        aggregation = plan.aggregations[0]
        if not (
            aggregation.function == "count"
            and aggregation.field == "product.id"
            and aggregation.distinct
        ):
            return False
        routed_scopes: list[str] = []
        has_scope_routes = any(
            condition.field == "product.scope"
            for group in plan.filter_groups
            for condition in group.conditions
        )
        for group in plan.filter_groups:
            scope_conditions = [
                condition
                for condition in group.conditions
                if condition.field == "product.scope"
            ]
            routed_scope: str | None = None
            if has_scope_routes:
                if (
                    len(scope_conditions) != 1
                    or scope_conditions[0].op != "eq"
                    or isinstance(scope_conditions[0].value, list)
                    or str(scope_conditions[0].value) not in plan.scopes
                ):
                    return False
                routed_scope = str(scope_conditions[0].value)
                routed_scopes.append(routed_scope)
            elif scope_conditions:
                return False
            for condition in group.conditions:
                if condition.field == "product.scope":
                    continue
                allowed_values = _CROSS_SCOPE_COUNT_FILTERS.get(condition.field)
                if allowed_values is None or condition.op not in {"eq", "in"}:
                    return False
                values = condition.value if isinstance(condition.value, list) else [condition.value]
                if not values or any(str(value) not in allowed_values for value in values):
                    return False
                applicable_scopes = _CROSS_SCOPE_FILTER_SCOPES[condition.field]
                if routed_scope:
                    if routed_scope not in applicable_scopes:
                        return False
                elif not applicable_scopes.intersection(plan.scopes):
                    return False
        return not (
            has_scope_routes
            and (
                plan.groups_join != "OR"
                or len(routed_scopes) != len(set(routed_scopes))
                or set(routed_scopes) != set(plan.scopes)
            )
        )

    @staticmethod
    def _special_count_basis_decision(
        plan: QueryPlan, scope: str
    ) -> PlanPolicyDecision | None:
        """Validate raw/quarantine/fund-attribute count plans as a matrix.

        These paths query objects other than the normal serving catalog.  No
        ordinary filter/entity/metric semantics can be silently carried over
        to them, so every allowed shape is stated here and everything else is
        rejected before the executor sees it.
        """

        if not plan.assumptions:
            return None
        basis_items = [
            item for item in plan.assumptions if item.startswith("count_basis=")
        ]
        if not basis_items:
            return None
        valid_shape = (
            len(plan.assumptions) == 1
            and len(basis_items) == 1
            and plan.intent == "aggregate"
            and len(plan.scopes) == 1
            and not plan.metrics
            and not plan.filter_groups
            and not plan.entities
            and not plan.sort
            and len(plan.aggregations) == 1
        )
        aggregation = plan.aggregations[0] if len(plan.aggregations) == 1 else None
        valid_shape = valid_shape and bool(
            aggregation
            and aggregation.function == "count"
            and aggregation.field == "product.id"
            and aggregation.distinct
        )
        if not valid_shape:
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "SPECIAL_COUNT_BASIS_SHAPE_INVALID",
                "raw·quarantine·attribute 개수는 단일 상품군의 필터 없는 distinct 상품 count 한 개만 실행합니다.",
            )
        basis = basis_items[0].split("=", 1)[1]
        if basis == "fund_attribute":
            allowed_group_by: list[str] = []
            allowed_scope = scope == "fund"
        elif basis == "quarantine":
            allowed_group_by = []
            allowed_scope = True
        else:  # raw
            allowed_group_by = (
                ["product.internal_type"]
                if scope in {"domestic_etp", "overseas_etp"}
                else []
            )
            allowed_scope = True
        if not allowed_scope:
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "SPECIAL_COUNT_BASIS_SCOPE_MISMATCH",
                "fund_attribute 기준은 펀드 상품군에만 적용합니다.",
            )
        if plan.group_by not in ([], allowed_group_by):
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "SPECIAL_COUNT_BASIS_GROUP_UNSUPPORTED",
                "선택한 원본 개수 기준에서 지원하지 않는 그룹 필드는 실행하지 않습니다.",
            )
        return PlanPolicyDecision(True, Answerability.FULL)

    def evaluate(self, plan: QueryPlan) -> PlanPolicyDecision:
        if plan.needs_clarification:
            return PlanPolicyDecision(False, Answerability.NEEDS_CLARIFICATION, "MISSING_CONDITION")
        if not plan.scopes:
            return PlanPolicyDecision(
                False,
                Answerability.NEEDS_CLARIFICATION,
                "MISSING_SCOPE",
                "상품군이 정해지지 않아 실행하지 않습니다.",
            )
        if len(plan.scopes) > 1:
            if self._is_safe_cross_scope_count(plan):
                return PlanPolicyDecision(True, Answerability.FULL)
            joined_metrics = " ".join(plan.metrics)
            if "expense_ratio" in joined_metrics:
                reason = "ZERO_AND_UNIT_POLICY_PENDING"
            elif "aum_last" in joined_metrics:
                reason = "CURRENCY_CONVERSION_NOT_DEFINED"
            elif "coupon_rate" in joined_metrics:
                reason = "METRIC_BASIS_MISMATCH"
            elif "credit_rating" in joined_metrics or "risk_grade" in joined_metrics:
                reason = "RISK_SCALE_MISMATCH"
            else:
                reason = "CROSS_PRODUCT_LOCKED_PENDING_BASIS"
            return PlanPolicyDecision(
                False,
                Answerability.INCOMPARABLE,
                reason,
                "서로 다른 상품군의 단위·기준일·통화·위험척도 정책이 확정되지 않아 혼합 순위를 만들지 않습니다.",
            )
        scope = plan.scopes[0]
        if plan.intent != "aggregate" and (plan.aggregations or plan.group_by):
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "NON_AGGREGATE_SHAPE_UNSUPPORTED",
                "조회·검색·순위·비교 계획에는 집계식이나 그룹 기준을 함께 적용하지 않습니다.",
            )
        if plan.groups_join == "OR" and len(plan.filter_groups) > 1 and (
            plan.intent in {"rank", "aggregate"}
            or any(
                self.get(condition.field) is not None
                for group in plan.filter_groups
                for condition in group.conditions
            )
        ):
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "OR_METRIC_POLICY_UNSUPPORTED",
                "OR 분기와 지표 coverage·필수 안전조건을 함께 쓰는 계획은 현재 MVP에서 실행하지 않습니다.",
            )
        if plan.intent == "explain" and not plan.entities:
            return PlanPolicyDecision(
                False,
                Answerability.NEEDS_CLARIFICATION,
                "EXPLANATION_TARGET_REQUIRED",
                "원본 정보로 설명할 정확한 상품명 또는 상품코드가 필요합니다.",
            )
        if any(entity.scope and entity.scope != scope for entity in plan.entities):
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "ENTITY_SCOPE_MISMATCH",
                "상품 식별자의 상품군과 질의 상품군이 일치하지 않습니다.",
            )
        if any(group_by not in ALLOWED_GROUP_BY for group_by in plan.group_by):
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "GROUP_FIELD_NOT_ALLOWLISTED",
                "허용되지 않은 그룹 기준은 실행하지 않습니다.",
            )
        if any(assumption not in ALLOWED_ASSUMPTIONS for assumption in plan.assumptions):
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "ASSUMPTION_NOT_ALLOWLISTED",
                "서버가 허용하지 않은 실행 가정은 적용하지 않습니다.",
            )
        special_basis = self._special_count_basis_decision(plan, scope)
        if special_basis is not None:
            return special_basis
        if "metric_count_basis=source_present" in plan.assumptions:
            source_present_shape = (
                plan.assumptions == ["metric_count_basis=source_present"]
                and plan.intent == "aggregate"
                and len(plan.metrics) == 1
                and len(plan.aggregations) == 1
                and plan.aggregations[0].function == "count"
                and plan.aggregations[0].field == "product.id"
                and plan.aggregations[0].distinct
                and not plan.group_by
                and not plan.sort
            )
            if not source_present_shape:
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "SOURCE_PRESENT_COUNT_SHAPE_INVALID",
                    "원본 값 제공 개수 기준은 단일 상품군·단일 지표의 distinct 상품 count 한 개에만 적용합니다.",
                )
        if plan.intent == "aggregate" and plan.group_by and not (
            plan.group_by == ["product.internal_type"]
            and scope in {"domestic_etp", "overseas_etp"}
        ):
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "AGGREGATE_GROUP_SHAPE_UNSUPPORTED",
                "단일 상품군 집계는 그룹 없음 또는 ETP의 단일 상품유형 그룹만 지원합니다.",
            )
        if plan.intent == "compare":
            if len(plan.entities) < 2:
                return PlanPolicyDecision(
                    False,
                    Answerability.NEEDS_CLARIFICATION,
                    "COMPARE_TARGETS_REQUIRED",
                    "비교에는 서로 다른 상품 대상이 2개 이상 필요합니다.",
                )
            identities = [
                (
                    entity.scope or scope,
                    "code" if entity.code else "name",
                    str(entity.code or entity.name).strip().casefold(),
                )
                for entity in plan.entities
            ]
            if len(identities) != len(set(identities)):
                return PlanPolicyDecision(
                    False,
                    Answerability.NEEDS_CLARIFICATION,
                    "DUPLICATE_COMPARE_TARGET",
                    "같은 비교 대상을 중복 지정하지 말고 서로 다른 상품을 지정해 주세요.",
                )
            if plan.limit < len(plan.entities):
                return PlanPolicyDecision(
                    False,
                    Answerability.NEEDS_CLARIFICATION,
                    "COMPARE_LIMIT_TOO_SMALL",
                    "결과 제한이 비교 대상 수보다 작아 일부 상품을 누락할 수 있으므로 실행하지 않습니다.",
                )
            if not plan.metrics:
                return PlanPolicyDecision(
                    False,
                    Answerability.NEEDS_CLARIFICATION,
                    "COMPARE_METRIC_REQUIRED",
                    "비교할 지표를 지정해 주세요.",
                )
            if plan.aggregations or plan.group_by:
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "COMPARE_SHAPE_UNSUPPORTED",
                    "상품 비교 계획에 집계나 그룹 조건을 함께 적용하지 않습니다.",
                )
            if any(
                self.get(condition.field) is not None
                for group in plan.filter_groups
                for condition in group.conditions
            ):
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "COMPARE_METRIC_FILTER_UNSUPPORTED",
                    "비교 대상 일부가 지표 조건으로 사라지는 불완전 비교를 막기 위해 비교에는 지표 필터를 적용하지 않습니다.",
                )
        for group in plan.filter_groups:
            for condition in group.conditions:
                if condition.field in CATALOG_FIELD_MAP:
                    if condition.field not in CATALOG_SCOPE_FIELDS[scope]:
                        return PlanPolicyDecision(
                            False,
                            Answerability.UNAVAILABLE,
                            "SOURCE_FIELD_ABSENT",
                            "선택한 상품군의 원본에는 요청한 상품 필드가 없습니다.",
                        )
                    if condition.field == "bond.exchange" and scope != "bond":
                        return PlanPolicyDecision(
                            False,
                            Answerability.UNAVAILABLE,
                            "FILTER_SCOPE_MISMATCH",
                            "장내·장외 조건은 국내채권에만 적용합니다.",
                        )
                    if condition.op not in CATALOG_FILTER_OPERATORS:
                        return PlanPolicyDecision(
                            False,
                            Answerability.UNAVAILABLE,
                            "FILTER_OPERATOR_NOT_ALLOWLISTED",
                            "문자형 상품 필드에 허용되지 않은 연산자는 실행하지 않습니다.",
                        )
                    if condition.op not in {"is_null", "is_not_null"}:
                        operands = (
                            condition.value
                            if isinstance(condition.value, list)
                            else [condition.value]
                        )
                        if not operands or any(not isinstance(value, str) for value in operands):
                            return PlanPolicyDecision(
                                False,
                                Answerability.UNAVAILABLE,
                                "CATALOG_OPERAND_TYPE_INVALID",
                                "상품 분류·코드·통화 조건값은 문자열만 허용합니다.",
                            )
                    continue
                condition_policy = self.get(condition.field)
                if condition_policy is None:
                    return PlanPolicyDecision(
                        False,
                        Answerability.UNAVAILABLE,
                        "FILTER_FIELD_NOT_ALLOWLISTED",
                        f"요청 조건({condition.field})은 실행 허용 목록에 없습니다.",
                    )
                if condition_policy.scope != scope:
                    return PlanPolicyDecision(
                        False,
                        Answerability.UNAVAILABLE,
                        "FILTER_SCOPE_MISMATCH",
                        "조건 지표의 상품군과 질의 상품군이 일치하지 않습니다.",
                    )
                if condition.field not in plan.metrics:
                    return PlanPolicyDecision(
                        False,
                        Answerability.UNAVAILABLE,
                        "FILTER_METRIC_NOT_DECLARED",
                        "지표 필터는 근거와 coverage를 반환하도록 metrics에도 명시해야 합니다.",
                    )
                if condition.op not in {"is_null", "is_not_null"} and (
                    condition_policy.filter_policy != "ALLOWED"
                    or "PENDING_ZERO" in condition_policy.quality_status
                    or condition_policy.zero_policy.startswith("SENTINEL_MINUS_100")
                    or (
                        condition_policy.allowed_ops
                        and condition.op not in condition_policy.allowed_ops
                    )
                ):
                    return PlanPolicyDecision(
                        False,
                        Answerability.DATA_QUALITY_BLOCKED,
                        "METRIC_FILTER_POLICY_BLOCKED",
                        "공식 필드 정책에서 잠긴 지표·연산자는 값 조회 외 필터에 사용하지 않습니다.",
                    )
                allowed_ops = {
                    "numeric": NUMERIC_METRIC_OPERATORS,
                    "text": TEXT_METRIC_OPERATORS,
                    "date": DATE_METRIC_OPERATORS,
                }[condition_policy.value_kind]
                if condition.op not in allowed_ops:
                    return PlanPolicyDecision(
                        False,
                        Answerability.UNAVAILABLE,
                        "FILTER_OPERATOR_NOT_ALLOWLISTED",
                        "해당 지표 유형에 허용되지 않은 연산자는 실행하지 않습니다.",
                    )
                if condition_policy.value_kind == "numeric" and condition.op not in {
                    "is_null",
                    "is_not_null",
                }:
                    operands = (
                        condition.value if isinstance(condition.value, list) else [condition.value]
                    )
                    if condition.op == "between":
                        operands.append(condition.value2)
                    try:
                        for value in operands:
                            canonicalize_numeric_operand(value)
                    except ValueError:
                        return PlanPolicyDecision(
                            False,
                            Answerability.UNAVAILABLE,
                            "NON_NUMERIC_METRIC_OPERAND",
                            "수치 지표 조건은 유한한 DECIMAL(38,10) 범위 값만 허용합니다.",
                        )
                elif condition.op not in {"is_null", "is_not_null"}:
                    operands = (
                        condition.value
                        if isinstance(condition.value, list)
                        else [condition.value]
                    )
                    if condition.op == "between":
                        operands.append(condition.value2)
                    if not operands or any(not isinstance(value, str) for value in operands):
                        return PlanPolicyDecision(
                            False,
                            Answerability.UNAVAILABLE,
                            "TEXT_METRIC_OPERAND_TYPE_INVALID",
                            "문자·등급·날짜 지표 조건값은 문자열만 허용합니다.",
                        )
                    if condition_policy.value_kind == "date":
                        try:
                            for value in operands:
                                if not re.fullmatch(r"\d{8}", value):
                                    raise ValueError
                                datetime.strptime(value, "%Y%m%d")
                        except ValueError:
                            return PlanPolicyDecision(
                                False,
                                Answerability.UNAVAILABLE,
                                "DATE_OPERAND_FORMAT_INVALID",
                                "날짜 지표 조건은 실제 달력에 존재하는 YYYYMMDD 문자열만 허용합니다.",
                            )
                if (
                    condition_policy.requires_currency_partition
                    and condition.op not in {"is_null", "is_not_null"}
                    and not self._has_currency_filter(plan, condition_policy.unit_status)
                ):
                    return PlanPolicyDecision(
                        False,
                        Answerability.DATA_QUALITY_BLOCKED,
                        "CURRENCY_FILTER_REQUIRED",
                        "통화 단위 지표 필터에는 단일 상품통화 또는 거래통화 조건이 필요합니다.",
                    )
        for metric_id in plan.metrics:
            if metric_id in CATALOG_FIELD_MAP:
                if plan.intent not in {"lookup", "explain"} or metric_id not in CATALOG_SCOPE_FIELDS[scope]:
                    return PlanPolicyDecision(
                        False,
                        Answerability.UNAVAILABLE,
                        "CATALOG_METRIC_NOT_EXECUTABLE",
                        "상품 상세 필드는 단일 상품 조회에서만 반환합니다.",
                    )
                continue
            if metric_id.startswith("cross."):
                return PlanPolicyDecision(
                    False,
                    Answerability.INCOMPARABLE,
                    "INCOMPATIBLE_METRICS",
                    "비교하려는 상품군의 metric 정의가 같다고 확인되지 않았습니다.",
                )
            policy = self.get(metric_id)
            if policy is None:
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "METRIC_NOT_ALLOWLISTED",
                    f"요청한 지표({metric_id})는 실행 허용 목록에 없습니다.",
                )
            if policy.scope != scope:
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "METRIC_SCOPE_MISMATCH",
                    f"요청 지표({metric_id})의 상품군이 질의 상품군과 다릅니다.",
                )
            if policy.source_field is None or policy.quality_status == "UNAVAILABLE":
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "SOURCE_FIELD_ABSENT",
                    f"제공된 {policy.source_table} 원본에는 요청 지표 필드가 없습니다.",
                )
            if policy.quality_status == "UNUSABLE_CONSTANT" and plan.intent not in {
                "lookup",
                "explain",
            }:
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "UNUSABLE_CONSTANT",
                    "제공된 값이 전 상품에서 같은 상수이므로 검색·비교·순위·집계 값으로 사용하지 않습니다.",
                )
            if plan.intent == "rank" and not policy.ranking_allowed:
                reason = policy.quality_status
                if policy.quality_status == "SEVERELY_PARTIAL":
                    reason = "SEVERELY_PARTIAL"
                elif policy.zero_policy == "UNUSABLE_CONSTANT":
                    reason = "UNUSABLE_CONSTANT"
                elif "PENDING_ZERO" in policy.quality_status:
                    reason = "PENDING_ZERO_POLICY"
                elif "ORDER" in policy.quality_status:
                    reason = "PENDING_ORDER_POLICY"
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    reason,
                    policy.notes,
                )
        if plan.sort and plan.intent != "rank":
            return PlanPolicyDecision(
                False,
                Answerability.UNAVAILABLE,
                "SORT_WITHOUT_RANK_INTENT",
                "정렬 기준은 rank 질의에서만 실행합니다.",
            )
        if plan.intent == "rank" and "bond.buyable_quantity" in plan.metrics:
            positive_only = any(
                condition.field == "bond.buyable_quantity"
                and condition.op in {"gt", "gte"}
                and canonicalize_numeric_operand(condition.value) > 0
                for group in plan.filter_groups
                for condition in group.conditions
            )
            if not positive_only:
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "POSITIVE_QUANTITY_FILTER_REQUIRED",
                    "매수가능수량 순위는 0의 의미가 미확정이므로 양수 조건을 명시해야 합니다.",
                )
        if plan.intent == "rank" and "domestic_etp.aum_last" in plan.metrics:
            etn_only = any(
                condition.field == "product.internal_type"
                and condition.op == "eq"
                and condition.value == "ETN"
                for group in plan.filter_groups
                for condition in group.conditions
            )
            if etn_only:
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "ETN_AUM_CONSTANT_ZERO",
                    "국내 ETN의 제공 AUM 값 409건이 모두 0이므로 크기 순위를 만들지 않습니다.",
                )
            etf_only = any(
                condition.field == "product.internal_type"
                and condition.op == "eq"
                and condition.value == "ETF"
                for group in plan.filter_groups
                for condition in group.conditions
            )
            if not etf_only:
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "ETF_SUBTYPE_FILTER_REQUIRED",
                    "국내 AUM 순위는 ETN 값이 전부 0이므로 ETF 조건이 필수입니다.",
                )
        if plan.intent == "rank" and "overseas_etp.aum_last" in plan.metrics:
            currency_fixed = any(
                condition.field == "product.trading_currency"
                and condition.op == "eq"
                and bool(condition.value)
                for group in plan.filter_groups
                for condition in group.conditions
            )
            if not currency_fixed:
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "TRADING_CURRENCY_FILTER_REQUIRED",
                    "해외 AUM 순위는 통화를 섞지 않도록 거래통화 조건이 필수입니다.",
                )
        if plan.intent == "rank" and any(
            self.require(metric_id).requires_currency_partition for metric_id in plan.metrics
        ) and not all(
            self._has_currency_filter(plan, self.require(metric_id).unit_status)
            for metric_id in plan.metrics
            if self.require(metric_id).requires_currency_partition
        ):
            return PlanPolicyDecision(
                False,
                Answerability.DATA_QUALITY_BLOCKED,
                "CURRENCY_FILTER_REQUIRED",
                "서로 다른 통화 값을 섞어 순위를 만들지 않습니다. 상품통화 또는 거래통화를 지정해 주세요.",
            )
        if plan.intent == "rank" and any(
            metric.startswith("domestic_etp.return_") for metric in plan.metrics
        ):
            ascending_return = any(
                item.field.startswith("domestic_etp.return_") and item.direction == "asc"
                for item in plan.sort
            )
            if ascending_return:
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "MINUS_100_SENTINEL_POLICY_PENDING",
                    "국내 ETP 수익률의 -100 값 의미가 확정되지 않아 낮은 순위는 만들지 않습니다.",
                )
        for aggregation in plan.aggregations:
            if aggregation.function == "count":
                if not aggregation.distinct:
                    return PlanPolicyDecision(
                        False,
                        Answerability.UNAVAILABLE,
                        "COUNT_DISTINCT_REQUIRED",
                        "상품 수는 중복 행이 아니라 distinct 상품 ID 기준으로만 계산합니다.",
                    )
                if len(plan.metrics) > 1 and aggregation.field in {None, "product.id"}:
                    return PlanPolicyDecision(
                        False,
                        Answerability.NEEDS_CLARIFICATION,
                        "MULTI_METRIC_COUNT_BASIS_REQUIRED",
                        "여러 지표가 모두 있는 상품 수인지 각 지표별 상품 수인지 기준을 하나로 지정해 주세요.",
                    )
                if aggregation.field not in {None, "product.id", *plan.metrics}:
                    return PlanPolicyDecision(
                        False,
                        Answerability.UNAVAILABLE,
                        "AGGREGATION_FIELD_NOT_ALLOWLISTED",
                        "count는 상품 ID 또는 허용 지표에만 적용합니다.",
                    )
                continue
            if aggregation.distinct:
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "DISTINCT_NUMERIC_AGGREGATION_UNSUPPORTED",
                    "수치 합계·평균·최소·최대에는 값 distinct 옵션을 적용하지 않습니다.",
                )
            if aggregation.field is None:
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "AGGREGATION_FIELD_REQUIRED",
                    "합계·평균·최소·최대에는 수치 지표가 필요합니다.",
                )
            policy = self.get(aggregation.field)
            if policy is None or policy.scope != scope or policy.value_kind != "numeric":
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "AGGREGATION_FIELD_NOT_ALLOWLISTED",
                    "합계·평균·최소·최대는 같은 상품군의 허용 수치 지표에만 적용합니다.",
                )
            if not policy.ranking_allowed:
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "AGGREGATION_POLICY_BLOCKED",
                    "단위·0·품질 정책이 잠긴 지표에는 합계·평균·최소·최대를 적용하지 않습니다.",
                )
            if "CURRENCY" in policy.unit_status and not self._has_currency_filter(
                plan, policy.unit_status
            ):
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "CURRENCY_FILTER_REQUIRED",
                    "통화 단위 지표 집계에는 단일 상품통화 또는 거래통화 조건이 필요합니다.",
                )
            if policy.requires_currency_partition and not self._has_currency_filter(
                plan, policy.unit_status
            ):
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "CURRENCY_FILTER_REQUIRED",
                    "가격·금액 지표 집계에는 단일 상품통화 또는 거래통화 조건이 필요합니다.",
                )
            if aggregation.function == "sum" and aggregation.field not in _ADDITIVE_SUM_METRICS:
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "NON_ADDITIVE_SUM_BLOCKED",
                    "수익률·금리·가격·기간 및 공식 share-class 중복 제거 규칙이 없는 "
                    "펀드 순자산처럼 경제적으로 안전하게 합산할 수 없는 지표는 합계를 만들지 않습니다.",
                )
            if (
                aggregation.function == "avg"
                and aggregation.field in _NON_AVERAGEABLE_METRICS
            ):
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "SHARE_CLASS_AVERAGE_BLOCKED",
                    "공식 share-class 통합 규칙이 없는 펀드 순자산은 클래스 중복이 평균을 왜곡할 수 있어 평균을 만들지 않습니다.",
                )
            if policy.zero_policy.startswith("SENTINEL_MINUS_100"):
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "MINUS_100_SENTINEL_POLICY_PENDING",
                    "-100 후보의 의미가 확정되지 않은 수익률에는 합계·평균·최소·최대를 적용하지 않습니다.",
                )
            if aggregation.field == "bond.buyable_quantity" and not any(
                condition.field == "bond.buyable_quantity"
                and condition.op in {"gt", "gte"}
                and canonicalize_numeric_operand(condition.value) > 0
                for group in plan.filter_groups
                for condition in group.conditions
            ):
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "POSITIVE_QUANTITY_FILTER_REQUIRED",
                    "매수가능수량 집계는 0 의미가 미확정이므로 양수 조건이 필수입니다.",
                )
            if aggregation.field == "domestic_etp.aum_last" and not any(
                condition.field == "product.internal_type"
                and condition.op == "eq"
                and condition.value == "ETF"
                for group in plan.filter_groups
                for condition in group.conditions
            ):
                return PlanPolicyDecision(
                    False,
                    Answerability.DATA_QUALITY_BLOCKED,
                    "ETF_SUBTYPE_FILTER_REQUIRED",
                    "국내 AUM 집계는 ETN 상수 0을 제외하도록 ETF 조건이 필수입니다.",
                )
            if aggregation.field not in plan.metrics:
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "AGGREGATION_METRIC_NOT_DECLARED",
                    "집계 지표는 metrics에도 명시해야 합니다.",
                )
            if plan.assumptions:
                return PlanPolicyDecision(
                    False,
                    Answerability.UNAVAILABLE,
                    "SPECIAL_BASIS_AGGREGATION_UNSUPPORTED",
                    "raw·quarantine·attribute 특수 기준에는 count 외 집계를 적용하지 않습니다.",
                )
        return PlanPolicyDecision(True, Answerability.FULL)

    @property
    def metric_ids(self) -> set[str]:
        return set(self._policies)

    @property
    def scope_metric_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for policy in self._policies.values():
            counts[policy.scope] = counts.get(policy.scope, 0) + 1
        return counts

    @staticmethod
    def _has_currency_filter(plan: QueryPlan, unit_status: str) -> bool:
        if "TRADING_CURRENCY" in unit_status or "CONTEXT_REQUIRED" in unit_status:
            allowed_fields = {"product.trading_currency"}
        elif "PRODUCT_CURRENCY" in unit_status:
            allowed_fields = {"product.currency"}
        else:
            allowed_fields = {"product.currency", "product.trading_currency"}
        return any(
            condition.field in allowed_fields
            and condition.op == "eq"
            and bool(condition.value)
            for group in plan.filter_groups
            for condition in group.conditions
        )
