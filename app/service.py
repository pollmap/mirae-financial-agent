"""End-to-end orchestration from question to grounded organizer response."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Protocol

from app.clarification import (
    ClarificationCodec,
    ClarificationState,
    ClarificationTokenError,
)
from app.config import Settings
from app.domain.models import (
    Answerability,
    ClarificationOption,
    ClarificationRequest,
    Condition,
    EvidenceBundle,
    OrganizerResponse,
    ProductEvidence,
    QueryPlan,
)
from app.execution.engine import DuckDBEngine
from app.planner.catalog_filters import resolve_catalog_filters
from app.planner.hcx import HCXError
from app.rendering import render_answer
from app.safety import evaluate_question, needs_selection_criteria, validate_rendered_policy

RETURN_PERIODS = (
    ("18개월", "18m"),
    ("1개월", "1m"),
    ("3개월", "3m"),
    ("6개월", "6m"),
    ("1일", "1d"),
    ("1주", "1w"),
    ("1년", "1y"),
    ("2년", "2y"),
    ("3년", "3y"),
    ("5년", "5y"),
    ("YTD", "ytd"),
)
RETURN_LABELS = {suffix: label for label, suffix in RETURN_PERIODS}
RETURN_OPTION_PREFERENCE = {
    "domestic_etp": ("1m", "3m", "1y", "6m"),
    "overseas_etp": ("1m", "3m", "1y", "1d"),
    "fund": ("1m", "1y", "3y", "3m"),
}
COMPARISON_METRIC_OPTIONS = {
    "bond": (
        ("bond.coupon_rate", "표면금리"),
        ("bond.buy_yield", "매수수익률"),
        ("bond.credit_rating", "신용등급"),
    ),
    "domestic_etp": (
        ("domestic_etp.return_1y", "1년 수익률"),
        ("domestic_etp.net_assets", "순자산"),
        ("domestic_etp.nav_last", "NAV"),
    ),
    "overseas_etp": (
        ("overseas_etp.aum_last", "AUM"),
        ("overseas_etp.close_price", "종가"),
        ("overseas_etp.volume_1d", "거래량"),
    ),
    "fund": (
        ("fund.return_1y", "1년 수익률"),
        ("fund.net_assets", "순자산"),
        ("fund.risk_grade", "위험등급"),
    ),
}
SUPPORTED_POLICY_REASONS = frozenset(
    {
        "RETURN_METRICS_UNAVAILABLE",
        "CROSS_PRODUCT_LOCKED_PENDING_BASIS",
        "CATALOG_VALUE_FILTER_NOT_RANKABLE",
        "CATALOG_VALUE_SCOPE_AMBIGUOUS",
        "CATALOG_VALUE_UNAVAILABLE",
        "FORECAST_OR_DEFINITIVE_RECOMMENDATION",
        "SNAPSHOT_NOT_REALTIME",
        "INSTRUCTION_INJECTION",
        "SOURCE_FIELD_ABSENT",
        "MISSING_IS_NOT_ZERO",
        "COMPARISON_METRIC_UNAVAILABLE",
        "CLARIFICATION_STATE_CONFLICT",
        "UNSUPPORTED_REQUEST",
    }
)

_SCOPE_CLARIFICATION_SLOTS = frozenset({"market", "scope"})
_ENTITY_CLARIFICATION_SLOTS = frozenset(
    {
        "product_identity",
        "comparison_target_identity",
        "comparison_targets",
        "explanation_target",
    }
)
_METRIC_CLARIFICATION_SLOTS = frozenset(
    {"return_period", "return_period_priority", "ranking_priority", "comparison_metric"}
)

# Keep the public evidence payload evaluator-friendly: a compact context is
# easier for the grading pipeline to transfer and parse, and the full row-level
# audit trail still exists server-side. Sized so the MAX_RESULT_LIMIT=50 item
# contract (~80KB of field evidence) never truncates, while pathological
# payloads stay bounded far below the 500,000 outer schema cap.
_RETRIEVED_CONTEXT_MAX_CHARS = 100_000
_RETRIEVED_CONTEXT_TARGET_CHARS = 95_000
_RESPONSE_TRUNCATION_PREFIX = "응답 크기 제한으로 전체"
_CLARIFICATION_TOKEN_MAX_CHARS = 10_000


class Planner(Protocol):
    name: str

    async def plan(self, question: str) -> QueryPlan: ...


class PlannerUnavailable(RuntimeError):
    """HCX could not safely produce a valid plan."""


class AgentService:
    def __init__(self, settings: Settings, planner: Planner, engine: DuckDBEngine) -> None:
        self.settings = settings
        self.planner = planner
        self.engine = engine
        self._db_semaphore = asyncio.Semaphore(settings.db_max_concurrency)
        self.clarification_codec = (
            ClarificationCodec(settings.clarification_signing_key)
            if settings.enable_clarification_state
            else None
        )

    def _encode_clarification(
        self,
        *,
        original_question: str,
        missing_slots: list[str],
        preserved_plan: dict[str, object],
    ) -> str | None:
        if self.clarification_codec is None:
            return None
        token = self.clarification_codec.encode(
            original_question=original_question,
            missing_slots=missing_slots,
            preserved_plan=preserved_plan,
        )
        # The public model deliberately permits stateless clarification.  A
        # maximum-length Korean question plus a large valid plan can expand
        # beyond 10,000 characters after UTF-8/base64 encoding; omitting that
        # token is safer than truncating signed state or failing the request.
        return token if len(token) <= _CLARIFICATION_TOKEN_MAX_CHARS else None

    @staticmethod
    def _signed_plan_snapshot(plan: QueryPlan) -> dict[str, object]:
        """Return the complete plan state that must survive the next turn."""

        preserved = plan.preserved_plan or {}
        unresolved = preserved.get("unresolved_plan")
        if isinstance(unresolved, dict):
            snapshot = unresolved
        else:
            snapshot = plan.model_dump(mode="json", exclude={"preserved_plan"})
        return {"unresolved_plan": snapshot}

    @staticmethod
    def _preserved_plan_payload(state: ClarificationState) -> dict[str, object]:
        """Unwrap current and legacy signed clarification-plan shapes."""

        payload: object = state.preserved_plan
        for _ in range(4):
            if not isinstance(payload, dict):
                return {}
            unresolved = payload.get("unresolved_plan")
            if isinstance(unresolved, dict):
                payload = unresolved
                continue
            nested = payload.get("preserved_plan")
            if isinstance(nested, dict) and isinstance(nested.get("unresolved_plan"), dict):
                payload = nested["unresolved_plan"]
                continue
            break
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _active_plan_payload(plan: QueryPlan) -> tuple[dict[str, object], bool]:
        """Return the executable plan carried by a possible clarification plan."""

        if plan.needs_clarification and plan.preserved_plan:
            unresolved = plan.preserved_plan.get("unresolved_plan")
            if isinstance(unresolved, dict):
                return dict(unresolved), True
        return plan.model_dump(mode="json", exclude={"preserved_plan"}), False

    @staticmethod
    def _value_key(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _clarification_conflict_plan(
        base_payload: dict[str, object], candidate: QueryPlan
    ) -> QueryPlan:
        raw_scopes = base_payload.get("scopes")
        scopes = (
            [scope for scope in raw_scopes if scope in {"bond", "domestic_etp", "overseas_etp", "fund"}]
            if isinstance(raw_scopes, list)
            else list(candidate.scopes)
        )
        return QueryPlan(
            intent="unsupported",
            scopes=list(dict.fromkeys(scopes or candidate.scopes)),
            assumptions=["policy_reason=CLARIFICATION_STATE_CONFLICT"],
        )

    def _reconcile_follow_up_plan(
        self, state: ClarificationState, candidate: QueryPlan
    ) -> QueryPlan:
        """Make a follow-up plan a monotonic refinement of signed state.

        A missing preserved component can be restored without changing the
        user's established intent.  A conflicting replacement is failed
        closed.  Only the slot asked by the server may legitimately resolve a
        previously unknown scope, identity, or metric.
        """

        base = self._preserved_plan_payload(state)
        protected_fields = {"scopes", "entities", "filter_groups", "metrics"}
        if not protected_fields.intersection(base):
            return candidate
        if candidate.intent == "unsupported":
            return candidate

        payload, carried_by_clarification = self._active_plan_payload(candidate)
        slots = set(state.missing_slots)
        base_intent = base.get("intent")
        candidate_intent = payload.get("intent")
        if (
            base_intent not in {None, "clarify", "unsupported"}
            and candidate_intent not in {"clarify", "unsupported"}
            and candidate_intent != base_intent
        ):
            return self._clarification_conflict_plan(base, candidate)

        base_scopes = list(base.get("scopes") or [])
        candidate_scopes = list(payload.get("scopes") or [])
        if base_scopes:
            if not candidate_scopes:
                payload["scopes"] = base_scopes
                candidate_scopes = base_scopes
            elif set(candidate_scopes) != set(base_scopes):
                return self._clarification_conflict_plan(base, candidate)
        elif (
            candidate_scopes
            and base.get("scopes") == []
            and base_intent not in {None, "clarify"}
            and not slots.intersection(_SCOPE_CLARIFICATION_SLOTS)
        ):
            return self._clarification_conflict_plan(base, candidate)

        base_entities = list(base.get("entities") or [])
        candidate_entities = list(payload.get("entities") or [])
        base_entity_keys = {self._value_key(item) for item in base_entities}
        candidate_entity_keys = {self._value_key(item) for item in candidate_entities}
        entity_slots = slots.intersection(_ENTITY_CLARIFICATION_SLOTS)
        if base_entities and not entity_slots:
            if not candidate_entities or candidate_entity_keys.issubset(base_entity_keys):
                payload["entities"] = base_entities
            elif candidate_entity_keys != base_entity_keys:
                return self._clarification_conflict_plan(base, candidate)
        elif base_entities and entity_slots:
            if "comparison_targets" in slots:
                if not base_entity_keys.issubset(candidate_entity_keys):
                    return self._clarification_conflict_plan(base, candidate)
            elif "comparison_target_identity" in slots:
                preserved_count = len(base_entity_keys.intersection(candidate_entity_keys))
                if len(candidate_entities) < len(base_entities) or preserved_count < max(
                    0, len(base_entities) - 1
                ):
                    return self._clarification_conflict_plan(base, candidate)
            elif len(candidate_entities) != 1:
                return self._clarification_conflict_plan(base, candidate)

        base_groups = list(base.get("filter_groups") or [])
        candidate_groups = list(payload.get("filter_groups") or [])
        if base_groups:
            base_join = base.get("groups_join", "AND")
            candidate_join = payload.get("groups_join", "AND")
            if not candidate_groups:
                payload["filter_groups"] = base_groups
                payload["groups_join"] = base_join
            elif candidate_join != base_join or len(candidate_groups) != len(base_groups):
                return self._clarification_conflict_plan(base, candidate)
            else:
                merged_groups: list[dict[str, object]] = []
                for base_group, candidate_group in zip(base_groups, candidate_groups, strict=True):
                    if not isinstance(base_group, dict) or not isinstance(candidate_group, dict):
                        return self._clarification_conflict_plan(base, candidate)
                    base_conditions = list(base_group.get("conditions") or [])
                    merged_conditions = list(candidate_group.get("conditions") or [])
                    candidate_condition_keys = {
                        self._value_key(item) for item in merged_conditions
                    }
                    for condition in base_conditions:
                        condition_key = self._value_key(condition)
                        if condition_key in candidate_condition_keys:
                            continue
                        field = condition.get("field") if isinstance(condition, dict) else None
                        if any(
                            isinstance(item, dict) and item.get("field") == field
                            for item in merged_conditions
                        ):
                            return self._clarification_conflict_plan(base, candidate)
                        merged_conditions.append(condition)
                    merged_group = dict(candidate_group)
                    merged_group["conditions"] = merged_conditions
                    merged_groups.append(merged_group)
                payload["filter_groups"] = merged_groups

        base_metrics = list(base.get("metrics") or [])
        candidate_metrics = list(payload.get("metrics") or [])
        metric_slots = slots.intersection(_METRIC_CLARIFICATION_SLOTS)
        if base_metrics and not metric_slots:
            base_metric_set = set(base_metrics)
            candidate_metric_set = set(candidate_metrics)
            if not candidate_metrics or candidate_metric_set.issubset(base_metric_set):
                payload["metrics"] = base_metrics
                if base_intent == "rank":
                    payload["sort"] = list(base.get("sort") or [])
            elif candidate_metric_set != base_metric_set:
                return self._clarification_conflict_plan(base, candidate)
            elif candidate_metrics != base_metrics:
                payload["metrics"] = base_metrics
                if base_intent == "rank":
                    payload["sort"] = list(base.get("sort") or [])
        elif "ranking_priority" in slots and base_metrics:
            if set(candidate_metrics) != set(base_metrics):
                return self._clarification_conflict_plan(base, candidate)
        elif "comparison_metric" in slots and base_metrics:
            if not set(base_metrics).issubset(candidate_metrics) or len(candidate_metrics) > len(
                base_metrics
            ) + 1:
                return self._clarification_conflict_plan(base, candidate)
        elif slots.intersection({"return_period", "return_period_priority"}):
            base_non_return = [metric for metric in base_metrics if ".return_" not in metric]
            candidate_non_return = [
                metric for metric in candidate_metrics if ".return_" not in metric
            ]
            candidate_returns = [metric for metric in candidate_metrics if ".return_" in metric]
            if not set(candidate_non_return).issubset(base_non_return) or not candidate_returns:
                return self._clarification_conflict_plan(base, candidate)
            payload["metrics"] = list(
                dict.fromkeys([*candidate_metrics, *base_non_return])
            )

        if payload.get("intent") == "rank":
            metric_order = list(payload.get("metrics") or [])
            candidate_sort = {
                item.get("field"): item
                for item in list(payload.get("sort") or [])
                if isinstance(item, dict)
            }
            base_sort = {
                item.get("field"): item
                for item in list(base.get("sort") or [])
                if isinstance(item, dict)
            }
            merged_sort = [
                candidate_sort.get(metric) or base_sort.get(metric) for metric in metric_order
            ]
            if any(item is None for item in merged_sort):
                return self._clarification_conflict_plan(base, candidate)
            payload["sort"] = merged_sort

        payload.pop("preserved_plan", None)
        try:
            reconciled = QueryPlan.model_validate(payload)
        except (TypeError, ValueError):
            return self._clarification_conflict_plan(base, candidate)

        if carried_by_clarification:
            outer = candidate.model_dump(mode="json")
            outer["scopes"] = reconciled.scopes
            outer["preserved_plan"] = {
                "original_question": state.original_question,
                "unresolved_plan": reconciled.model_dump(mode="json"),
            }
            try:
                return QueryPlan.model_validate(outer)
            except ValueError:
                return self._clarification_conflict_plan(base, candidate)
        if reconciled.needs_clarification:
            clarified = reconciled.model_dump(mode="json")
            clarified["preserved_plan"] = {
                "original_question": state.original_question,
                "unresolved_plan": reconciled.model_dump(
                    mode="json", exclude={"preserved_plan"}
                ),
            }
            try:
                return QueryPlan.model_validate(clarified)
            except ValueError:
                return self._clarification_conflict_plan(base, candidate)
        return reconciled

    def _available_return_periods(self, scope: str) -> tuple[str, ...]:
        return self.engine.registry.available_return_periods(scope)

    def _preferred_return_periods(self, scope: str) -> list[str]:
        available = set(self._available_return_periods(scope))
        preferred = RETURN_OPTION_PREFERENCE.get(scope, tuple(RETURN_LABELS))
        ordered = [period for period in preferred if period in available]
        ordered.extend(
            period for period in RETURN_LABELS if period in available and period not in ordered
        )
        return ordered[:4]

    def _comparison_metric_options(self, scopes: list[str]) -> list[ClarificationOption]:
        if len(scopes) != 1:
            return []
        options: list[ClarificationOption] = []
        for metric_id, label in COMPARISON_METRIC_OPTIONS.get(scopes[0], ()):
            policy = self.engine.registry.get(metric_id)
            if policy is None or policy.source_field is None or policy.quality_status in {
                "UNAVAILABLE",
                "UNUSABLE_CONSTANT",
                "SEVERELY_PARTIAL",
            }:
                continue
            options.append(
                ClarificationOption(
                    value=metric_id,
                    label=label,
                    description="선택한 원본 지표로 두 상품을 나란히 비교",
                )
            )
        return options[:4]

    @staticmethod
    def _unavailable_return_plan(scopes: list[str]) -> QueryPlan:
        return QueryPlan(
            intent="unsupported",
            scopes=scopes,
            assumptions=["policy_reason=RETURN_METRICS_UNAVAILABLE"],
        )

    @staticmethod
    def _metric_label(metric_id: str) -> str:
        suffix = metric_id.rsplit(".", 1)[-1]
        labels = {
            "expense_ratio": "보수",
            "aum_last": "AUM",
            "net_assets": "순자산",
            "coupon_rate": "표면금리",
            "buy_yield": "매수수익률",
            "buyable_quantity": "매수가능수량",
            "volume_1d": "거래량",
            "close_price": "종가",
            "nav_last": "NAV",
        }
        if suffix.startswith("return_"):
            period = suffix.removeprefix("return_").upper()
            return f"{period} 수익률"
        return labels.get(suffix, metric_id)

    @staticmethod
    def _explicit_return_periods(question: str) -> list[str]:
        upper = question.upper()
        periods: list[str] = []
        for token, suffix in RETURN_PERIODS:
            if token == "YTD":
                matched = re.search(r"(?<![A-Z])YTD(?![A-Z])", upper) is not None
            else:
                match = re.fullmatch(r"(\d+)(개월|일|주|년)", token)
                if match is None:
                    continue
                number, unit = match.groups()
                matched = (
                    re.search(rf"(?<!\d){number}\s*{unit}(?!\d)", upper) is not None
                )
            if matched and suffix not in periods:
                periods.append(suffix)
        return periods

    @classmethod
    def _explicit_return_period(cls, question: str) -> str | None:
        periods = cls._explicit_return_periods(question)
        return periods[0] if len(periods) == 1 else None

    @staticmethod
    def _explicit_etp_scope(question: str) -> str | None:
        upper = question.upper()
        if re.search(
            r"(?:국내\s*(?:와|과|·|/|,)\s*해외|해외\s*(?:와|과|·|/|,)\s*국내)"
            r"\s*(?:상장\s*)?(?:ETF|ETN|ETP|상장지수)|"
            r"국내\s*(?:ETF|ETN|ETP).{0,12}해외\s*(?:ETF|ETN|ETP)|"
            r"해외\s*(?:ETF|ETN|ETP).{0,12}국내\s*(?:ETF|ETN|ETP)",
            upper,
        ):
            return None
        overseas = bool(
            re.search(
                r"(?:해외\s*(?:상장\s*)?(?:ETF|ETN|ETP|상장지수)|"
                r"미국\s*(?:증시|상장)|NYSE|NASDAQ|해외\s*티커|ISIN|"
                r"추가\s*조건\s*:\s*해외)",
                upper,
            )
            or re.search(r"(?<![A-Z0-9])US[A-Z0-9]{10}(?![A-Z0-9])", upper)
        )
        domestic = bool(
            re.search(
                r"(?:국내\s*(?:상장\s*)?(?:ETF|ETN|ETP|상장지수)|"
                r"KOSPI|KOSDAQ|KRX|추가\s*조건\s*:\s*국내)",
                upper,
            )
            or re.search(r"(?<![A-Z0-9])[AQ]\d{6}(?![A-Z0-9])", upper)
        )
        if overseas == domestic:
            return None
        return "overseas_etp" if overseas else "domestic_etp"

    @staticmethod
    def _market_is_explicit(question: str) -> bool:
        upper = question.upper()
        return bool(
            re.search(
                r"(?:국내|해외)\s*(?:와|과|·|/|,)?\s*(?:상장\s*)?"
                r"(?:ETF|ETN|ETP|상장지수)|"
                r"미국\s*(?:증시|상장)|NYSE|NASDAQ|KOSPI|KOSDAQ|KRX|"
                r"해외\s*티커|ISIN|추가\s*조건\s*:\s*(?:국내|해외)|"
                r"(?<![A-Z0-9])(?:[AQ]\d{6}|US[A-Z0-9]{10})(?![A-Z0-9])",
                upper,
            )
        )

    def _align_explicit_etp_scope(self, question: str, plan: QueryPlan) -> QueryPlan:
        """Make an explicit listing market authoritative over a generated scope."""

        target = self._explicit_etp_scope(question)
        if target is None or not any(token in question.upper() for token in ("ETF", "ETN", "ETP")):
            return plan
        current_etp = [scope for scope in plan.scopes if scope.endswith("etp")]
        if current_etp == [target]:
            return plan
        payload = plan.model_dump(mode="json")
        payload["scopes"] = list(
            dict.fromkeys([scope for scope in plan.scopes if not scope.endswith("etp")] + [target])
        )

        def remap_field(field: str | None) -> str | None:
            if field is None:
                return None
            if not field.startswith(("domestic_etp.", "overseas_etp.")):
                return field
            candidate = target + "." + field.split(".", 1)[1]
            return candidate if self.engine.registry.get(candidate) is not None else field

        payload["metrics"] = list(dict.fromkeys(remap_field(item) for item in payload["metrics"]))
        for entity in payload["entities"]:
            if entity["scope"] in {"domestic_etp", "overseas_etp"}:
                entity["scope"] = target
        for group in payload["filter_groups"]:
            for condition in group["conditions"]:
                condition["field"] = remap_field(condition["field"])
        for aggregation in payload["aggregations"]:
            aggregation["field"] = remap_field(aggregation["field"])
        for item in payload["sort"]:
            item["field"] = remap_field(item["field"])
        return QueryPlan.model_validate(payload)

    @staticmethod
    def _clarification_plan(
        *,
        original_question: str,
        scopes: list[str],
        question: str,
        missing_slot: str,
        options: list[ClarificationOption],
        plan: QueryPlan | None = None,
    ) -> QueryPlan:
        return QueryPlan(
            intent="clarify",
            scopes=scopes,
            needs_clarification=True,
            clarification_question=question,
            missing_slots=[missing_slot],
            clarification_options=options,
            preserved_plan={
                "original_question": original_question,
                **(
                    {"unresolved_plan": plan.model_dump(mode="json")}
                    if plan is not None
                    else {}
                ),
            },
            limit=plan.limit if plan is not None else 10,
        )

    def _preflight_clarification(self, question: str) -> QueryPlan | None:
        """Resolve obvious high-impact ambiguity before spending an HCX request."""

        if needs_selection_criteria(question):
            # Official task: ask for the missing condition instead of refusing.
            # Definitive/personal/forecast advice never reaches here — it is
            # blocked earlier by evaluate_question.
            return self._clarification_plan(
                original_question=question,
                scopes=[],
                question=(
                    "단정적인 추천 대신 객관적 기준으로 조회합니다. "
                    "어떤 기준을 우선할까요?"
                ),
                missing_slot="selection_criteria",
                options=[
                    ClarificationOption(value="수익률 높은 순", label="수익률 높은 순"),
                    ClarificationOption(value="보수 낮은 순", label="보수 낮은 순"),
                    ClarificationOption(value="순자산 큰 순", label="순자산(AUM) 큰 순"),
                    ClarificationOption(value="위험등급 낮은 순", label="위험등급 낮은 순"),
                ],
            )

        upper = question.upper()
        has_etp = any(token in upper for token in ("ETF", "ETN", "ETP"))
        if has_etp and not self._market_is_explicit(question):
            return self._clarification_plan(
                original_question=question,
                scopes=[],
                question="ETF·ETN의 시장을 국내와 해외 중 어느 쪽으로 볼까요?",
                missing_slot="market",
                options=[
                    ClarificationOption(value="domestic_etp", label="국내 ETF·ETN"),
                    ClarificationOption(value="overseas_etp", label="해외 ETF·ETN"),
                ],
            )

        return_scope = None
        if any(token in question for token in ("국내 ETF", "국내 ETN", "국내 ETP")):
            return_scope = "domestic_etp"
        elif any(
            token in question
            for token in (
                "해외 ETF",
                "해외 ETN",
                "해외 ETP",
                "미국 증시 ETF",
                "미국 상장 ETF",
            )
        ) or (has_etp and re.search(r"NYSE|NASDAQ", upper)):
            return_scope = "overseas_etp"
        elif "펀드" in question:
            return_scope = "fund"
        if (
            any(token in question for token in ("수익률", "성과", "퍼포먼스"))
            and return_scope is not None
            and not self._explicit_return_periods(question)
        ):
            preferred = self._preferred_return_periods(return_scope)
            if len(preferred) < 2:
                return self._unavailable_return_plan([return_scope])
            return self._clarification_plan(
                original_question=question,
                scopes=[return_scope],
                question="어느 기간의 수익률을 사용할까요?",
                missing_slot="return_period",
                options=[
                    ClarificationOption(value=suffix, label=RETURN_LABELS[suffix])
                    for suffix in preferred
                ],
            )
        return None

    def _align_return_period(
        self, original_question: str, plan: QueryPlan
    ) -> QueryPlan:
        """Make an explicit user period authoritative over a generated metric suffix."""

        has_return_metric = any(".return_" in metric for metric in plan.metrics)
        asks_return = any(
            token in original_question for token in ("수익률", "성과", "퍼포먼스")
        )
        if (not asks_return and not has_return_metric) or len(plan.scopes) != 1:
            return plan
        scope = plan.scopes[0]
        if scope not in RETURN_OPTION_PREFERENCE:
            return plan
        periods = self._explicit_return_periods(original_question)
        available = self._available_return_periods(scope)
        if not available:
            if len(periods) != 1:
                return self._unavailable_return_plan(plan.scopes)
            # Preserve a single explicit request so the registry can distinguish
            # an absent field from a populated-but-unusable constant.
            available = (periods[0],)
        if len(periods) > 1:
            requested_available = [period for period in periods if period in available]
            if plan.intent in {"lookup", "compare", "explain"} and requested_available:
                payload = plan.model_dump(mode="json")
                payload["metrics"] = [
                    metric
                    for metric in payload["metrics"]
                    if not metric.startswith(f"{scope}.return_")
                ]
                payload["metrics"].extend(
                    f"{scope}.return_{period}" for period in requested_available
                )
                payload["metrics"] = list(dict.fromkeys(payload["metrics"]))
                return QueryPlan.model_validate(payload)
            options = (
                requested_available
                if len(requested_available) >= 2
                else self._preferred_return_periods(scope)
            )
            return self._clarification_plan(
                original_question=original_question,
                scopes=plan.scopes,
                question=(
                    "여러 수익률 기간 중 이번 정렬·비교의 기준 기간을 하나 선택해 주세요. "
                    "선택지에는 이 상품군에서 실제 사용 가능한 기간만 표시합니다."
                ),
                missing_slot="return_period_priority",
                options=[
                    ClarificationOption(value=suffix, label=RETURN_LABELS[suffix])
                    for suffix in options[:4]
                ],
                plan=plan,
            )
        period = periods[0] if periods else None
        if period is None:
            preferred = self._preferred_return_periods(scope)
            return self._clarification_plan(
                original_question=original_question,
                scopes=plan.scopes,
                question="어느 기간의 수익률을 사용할까요?",
                missing_slot="return_period",
                options=[
                    ClarificationOption(value=suffix, label=RETURN_LABELS[suffix])
                    for suffix in preferred
                ],
                plan=plan,
            )
        if period not in available:
            preferred = self._preferred_return_periods(scope)
            return self._clarification_plan(
                original_question=original_question,
                scopes=plan.scopes,
                question=(
                    f"요청한 {RETURN_LABELS[period]} 수익률은 이 상품군 원본에 없습니다. "
                    "제공된 기간 중 어느 기간을 사용할까요?"
                ),
                missing_slot="return_period",
                options=[
                    ClarificationOption(value=suffix, label=RETURN_LABELS[suffix])
                    for suffix in preferred
                ],
                plan=plan,
            )

        expected = f"{scope}.return_{period}"
        payload = plan.model_dump(mode="json")
        old_return_metrics = {
            metric for metric in plan.metrics if metric.startswith(f"{scope}.return_")
        }
        metrics = [expected if metric in old_return_metrics else metric for metric in plan.metrics]
        if not old_return_metrics and plan.intent in {
            "lookup",
            "search",
            "rank",
            "compare",
            "aggregate",
        }:
            metrics.append(expected)
        payload["metrics"] = list(dict.fromkeys(metrics))
        for item in payload["sort"]:
            if item["field"] in old_return_metrics:
                item["field"] = expected
        if plan.intent == "rank" and expected not in {item["field"] for item in payload["sort"]}:
            direction = "asc" if any(token in original_question for token in ("낮은", "작은")) else "desc"
            payload["sort"].append(
                {"field": expected, "direction": direction, "nulls": "last"}
            )
        for group in payload["filter_groups"]:
            for condition in group["conditions"]:
                if condition["field"] in old_return_metrics:
                    condition["field"] = expected
        for aggregation in payload["aggregations"]:
            if aggregation["field"] in old_return_metrics:
                aggregation["field"] = expected
        return QueryPlan.model_validate(payload)

    @staticmethod
    def _explicit_priority_metric(question: str, metrics: list[str]) -> str | None:
        labels = {
            "보수": lambda metric: metric.endswith("expense_ratio"),
            "AUM": lambda metric: metric.endswith("aum_last"),
            "순자산": lambda metric: metric.endswith("net_assets"),
            "수익률": lambda metric: ".return_" in metric,
            "거래량": lambda metric: metric.endswith("volume_1d"),
            "표면금리": lambda metric: metric.endswith("coupon_rate"),
            "NAV": lambda metric: metric.endswith("nav_last"),
            "매수수익률": lambda metric: metric.endswith("buy_yield"),
        }
        for label, matches in labels.items():
            if not re.search(
                rf"{re.escape(label)}\s*(?:을|를)?\s*(?:우선|먼저|1순위)",
                question,
                flags=re.IGNORECASE,
            ):
                continue
            candidates = [metric for metric in metrics if matches(metric)]
            if len(candidates) == 1:
                return candidates[0]
        return None

    def _align_ranking_priority(self, original_question: str, plan: QueryPlan) -> QueryPlan:
        if plan.intent != "rank" or len(plan.metrics) <= 1:
            return plan
        selected = self._explicit_priority_metric(original_question, plan.metrics)
        if selected is None:
            return self._clarification_plan(
                original_question=original_question,
                scopes=plan.scopes,
                question=(
                    "여러 지표를 임의의 합산점수로 섞지 않습니다. "
                    "어느 지표를 먼저 정렬할까요?"
                ),
                missing_slot="ranking_priority",
                options=[
                    ClarificationOption(
                        value=metric_id,
                        label=f"{self._metric_label(metric_id)} 우선",
                        description="선택한 지표를 1차 정렬 기준으로 사용",
                    )
                    for metric_id in plan.metrics[:4]
                ],
                plan=plan,
            )
        ordered_metrics = [selected, *[item for item in plan.metrics if item != selected]]
        sort_by_field = {item.field: item.model_dump(mode="json") for item in plan.sort}
        payload = plan.model_dump(mode="json")
        payload["metrics"] = ordered_metrics
        payload["sort"] = [sort_by_field[metric] for metric in ordered_metrics]
        return QueryPlan.model_validate(payload)

    @staticmethod
    def _is_simple_cross_scope_count_question(question: str) -> bool:
        count_request = bool(
            re.search(r"(?:몇\s*(?:개|건)|개수|상품\s*수)", question)
        )
        metric_request = any(
            token in question.upper()
            for token in (
                "수익률",
                "성과",
                "퍼포먼스",
                "보수",
                "AUM",
                "NAV",
                "금리",
                "수익",
                "위험등급",
                "가격",
                "거래량",
            )
        )
        return "각각" in question and count_request and not metric_request

    @staticmethod
    def _cross_scope_count_plan(
        original_question: str, scopes: list[str], limit: int
    ) -> QueryPlan:
        filter_groups: list[dict[str, object]] = []
        upper = original_question.upper()
        for scope in scopes:
            conditions: list[dict[str, object]] = [
                {"field": "product.scope", "op": "eq", "value": scope}
            ]
            if scope in {"domestic_etp", "overseas_etp"}:
                market_label = "국내" if scope == "domestic_etp" else "해외"
                subtype = None
                if re.search(rf"{market_label}\s*(?:상장\s*)?ETF", upper):
                    subtype = "ETF"
                elif re.search(rf"{market_label}\s*(?:상장\s*)?ETN", upper):
                    subtype = "ETN"
                if subtype:
                    conditions.append(
                        {
                            "field": "product.internal_type",
                            "op": "eq",
                            "value": subtype,
                        }
                    )
            if scope == "fund":
                if "사모" in original_question:
                    conditions.append(
                        {"field": "product.public_private", "op": "eq", "value": "사모"}
                    )
                else:
                    conditions.append(
                        {"field": "product.public_private", "op": "eq", "value": "공모"}
                    )
            filter_groups.append({"conditions": conditions})
        return QueryPlan(
            intent="aggregate",
            scopes=scopes,
            filter_groups=filter_groups,
            groups_join="OR",
            aggregations=[
                {
                    "function": "count",
                    "field": "product.id",
                    "alias": "product_count",
                    "distinct": True,
                }
            ],
            group_by=["product.scope"],
            limit=limit,
        )

    @staticmethod
    def _apply_resolved_catalog_filters(
        original_question: str, plan: QueryPlan
    ) -> QueryPlan:
        resolution = resolve_catalog_filters(original_question, plan.scopes)
        if resolution.reason_code:
            return QueryPlan(
                intent="unsupported",
                scopes=plan.scopes,
                assumptions=[f"policy_reason={resolution.reason_code}"],
            )
        if not resolution.conditions:
            return plan
        resolved_fields = {condition.field for condition in resolution.conditions}
        payload = plan.model_dump(mode="json")
        groups = payload["filter_groups"] or [{"join": "AND", "conditions": []}]
        for group in groups:
            group["conditions"] = [
                condition
                for condition in group["conditions"]
                if condition["field"] not in resolved_fields
            ]
            group["conditions"].extend(
                condition.model_dump(mode="json") for condition in resolution.conditions
            )
        payload["filter_groups"] = groups
        suffixes = {
            "product.asset_type": (".asset_type", ".asset_class"),
            "product.region": (".region", ".investment_region"),
            "product.risk_grade": (".risk_grade",),
            "product.pension_eligible": (".pension_eligible",),
        }
        blocked_suffixes = tuple(
            suffix
            for field in resolved_fields
            for suffix in suffixes.get(field, ())
        )
        payload["metrics"] = [
            metric
            for metric in payload["metrics"]
            if metric not in resolved_fields and not metric.endswith(blocked_suffixes)
        ]
        if plan.intent == "rank":
            payload["sort"] = [
                item for item in payload["sort"] if item["field"] in payload["metrics"]
            ]
            if not payload["metrics"]:
                return QueryPlan(
                    intent="unsupported",
                    scopes=plan.scopes,
                    assumptions=["policy_reason=CATALOG_VALUE_FILTER_NOT_RANKABLE"],
                )
        return QueryPlan.model_validate(payload)

    @staticmethod
    def _ensure_public_fund_universe(plan: QueryPlan) -> QueryPlan:
        """Default the official fund product scope to public funds.

        Explicit private-fund and raw/audit questions remain executable so the
        supplied-file anomalies can be inspected, but an unqualified "fund"
        product request must not silently include the 15 private rows or the
        rows whose public/private label is missing.
        """

        audit_count_bases = {
            "count_basis=raw",
            "count_basis=quarantine",
            "count_basis=fund_attribute",
        }
        if plan.scopes != ["fund"] or audit_count_bases.intersection(plan.assumptions):
            return plan
        payload = plan.model_dump(mode="json")
        groups = payload["filter_groups"] or [{"join": "AND", "conditions": []}]
        targets = groups if payload["groups_join"] == "OR" else groups[:1]
        for group in targets:
            if any(
                condition["field"] == "product.public_private"
                for condition in group["conditions"]
            ):
                continue
            group["conditions"].append(
                Condition(
                    field="product.public_private", op="eq", value="공모"
                ).model_dump(mode="json")
            )
        payload["filter_groups"] = groups
        return QueryPlan.model_validate(payload)

    def _enforce_required_clarification(self, original_question: str, plan: QueryPlan) -> QueryPlan:
        """Apply server-side ambiguity gates even when the planner omits them."""

        simple_cross_count = self._is_simple_cross_scope_count_question(original_question)
        if plan.intent == "unsupported" or (plan.intent == "clarify" and not simple_cross_count):
            return plan
        plan = self._align_explicit_etp_scope(original_question, plan)
        required_scopes: list[str] = []
        bond_fund_phrase = re.search(r"채권\s*(?:형\s*)?펀드", original_question)
        if "국내채권" in original_question or (
            re.search(r"(?:^|\s)채권(?:\s|$)", original_question)
            and not bond_fund_phrase
        ):
            required_scopes.append("bond")
        if "펀드" in original_question:
            required_scopes.append("fund")
        if any(token in original_question for token in ("국내 ETF", "국내 ETN", "국내 ETP")):
            required_scopes.append("domestic_etp")
        if any(token in original_question for token in ("해외 ETF", "해외 ETN", "해외 ETP")):
            required_scopes.append("overseas_etp")
        required_scopes = list(dict.fromkeys(required_scopes))
        if required_scopes and not set(required_scopes).issubset(plan.scopes):
            plan = QueryPlan.model_validate(
                {
                    **plan.model_dump(mode="json"),
                    "scopes": list(dict.fromkeys([*plan.scopes, *required_scopes])),
                }
            )
        has_etp = any(token in original_question.upper() for token in ("ETF", "ETN", "ETP"))
        explicit_market = self._market_is_explicit(original_question)
        if has_etp and not explicit_market:
            return QueryPlan(
                intent="clarify",
                scopes=[],
                needs_clarification=True,
                clarification_question="ETF·ETN의 시장을 국내와 해외 중 어느 쪽으로 볼까요?",
                missing_slots=["market"],
                clarification_options=[
                    ClarificationOption(value="국내", label="국내 ETF·ETN"),
                    ClarificationOption(value="해외", label="해외 ETF·ETN"),
                ],
                preserved_plan={
                    "original_question": original_question,
                    "unresolved_plan": plan.model_dump(mode="json"),
                },
                limit=plan.limit,
            )
        if not plan.scopes:
            return QueryPlan(
                intent="clarify",
                scopes=[],
                needs_clarification=True,
                clarification_question="어느 금융상품군을 대상으로 조회할까요?",
                missing_slots=["scope"],
                clarification_options=[
                    ClarificationOption(value="국내채권", label="국내채권"),
                    ClarificationOption(value="국내 ETF·ETN", label="국내 ETF·ETN"),
                    ClarificationOption(value="해외 ETF·ETN", label="해외 ETF·ETN"),
                    ClarificationOption(value="공모펀드", label="공모펀드"),
                ],
                preserved_plan={
                    "original_question": original_question,
                    "unresolved_plan": plan.model_dump(mode="json"),
                },
                limit=plan.limit,
            )
        if simple_cross_count and len(plan.scopes) > 1:
            return self._cross_scope_count_plan(original_question, plan.scopes, plan.limit)
        explicit_subtype = None
        if "ETF" in original_question and not any(
            token in original_question for token in ("ETN", "ETP", "ETF·ETN", "ETF와 ETN")
        ):
            explicit_subtype = "ETF"
        elif "ETN" in original_question and "ETF" not in original_question:
            explicit_subtype = "ETN"
        if explicit_subtype and any(scope.endswith("etp") for scope in plan.scopes):
            subtype_present = any(
                condition.field == "product.internal_type"
                and condition.op == "eq"
                and condition.value == explicit_subtype
                for group in plan.filter_groups
                for condition in group.conditions
            )
            if not subtype_present:
                existing_groups = [group.model_dump(mode="json") for group in plan.filter_groups]
                if not existing_groups:
                    existing_groups = [{"join": "AND", "conditions": []}]
                for group in existing_groups:
                    group["conditions"].append(
                        Condition(
                            field="product.internal_type",
                            op="eq",
                            value=explicit_subtype,
                        ).model_dump(mode="json")
                    )
                plan = QueryPlan.model_validate(
                    {
                        **plan.model_dump(mode="json"),
                        "filter_groups": existing_groups,
                    }
                )
        if plan.intent == "explain" and not plan.entities:
            return self._clarification_plan(
                original_question=original_question,
                scopes=plan.scopes,
                question=(
                    "원본 상품 정보로 설명할 대상을 상품명 또는 상품코드 중 어떻게 지정할까요?"
                ),
                missing_slot="explanation_target",
                options=[
                    ClarificationOption(
                        value="상품명 입력",
                        label="상품명으로 지정",
                        description="설명할 정확한 상품명을 입력",
                    ),
                    ClarificationOption(
                        value="상품코드 입력",
                        label="상품코드로 지정",
                        description="티커·종목번호·ISIN 등 상품코드를 입력",
                    ),
                ],
                plan=plan,
            )
        plan = self._apply_resolved_catalog_filters(original_question, plan)
        if plan.intent == "unsupported":
            return plan
        plan = self._ensure_public_fund_universe(plan)
        needs_return_period = any(
            token in original_question for token in ("수익률", "성과", "퍼포먼스")
        ) and any(
            scope in {"domestic_etp", "overseas_etp", "fund"} for scope in plan.scopes
        )
        explicit_return_period = bool(self._explicit_return_periods(original_question))
        if needs_return_period and not explicit_return_period:
            if len(plan.scopes) == 1:
                preferred = self._preferred_return_periods(plan.scopes[0])
                if len(preferred) < 2:
                    return self._unavailable_return_plan(plan.scopes)
                return self._clarification_plan(
                    original_question=original_question,
                    scopes=plan.scopes,
                    question="어느 기간의 수익률을 사용할까요?",
                    missing_slot="return_period",
                    options=[
                        ClarificationOption(value=suffix, label=RETURN_LABELS[suffix])
                        for suffix in preferred
                    ],
                    plan=plan,
                )
            return QueryPlan(
                intent="unsupported",
                scopes=plan.scopes,
                assumptions=["policy_reason=CROSS_PRODUCT_LOCKED_PENDING_BASIS"],
            )
        if plan.intent == "compare" and len(plan.entities) < 2:
            return QueryPlan(
                intent="clarify",
                scopes=plan.scopes,
                needs_clarification=True,
                clarification_question="비교할 상품 두 개 이상을 어떻게 지정할까요?",
                missing_slots=["comparison_targets"],
                clarification_options=[
                    ClarificationOption(value="상품명 2개 입력", label="상품명으로 지정"),
                    ClarificationOption(value="상품코드 2개 입력", label="상품코드로 지정"),
                ],
                preserved_plan={
                    "original_question": original_question,
                    "unresolved_plan": plan.model_dump(mode="json"),
                },
                limit=plan.limit,
            )
        if plan.intent == "compare" and not plan.metrics:
            options = self._comparison_metric_options(plan.scopes)
            if len(options) < 2:
                return QueryPlan(
                    intent="unsupported",
                    scopes=plan.scopes,
                    assumptions=["policy_reason=COMPARISON_METRIC_UNAVAILABLE"],
                )
            return QueryPlan(
                intent="clarify",
                scopes=plan.scopes,
                needs_clarification=True,
                clarification_question="어떤 지표로 두 상품을 비교할까요?",
                missing_slots=["comparison_metric"],
                clarification_options=options,
                preserved_plan={
                    "original_question": original_question,
                    "unresolved_plan": plan.model_dump(mode="json"),
                },
                limit=plan.limit,
            )
        plan = self._align_return_period(original_question, plan)
        if plan.needs_clarification:
            return plan
        return self._align_ranking_priority(original_question, plan)

    @staticmethod
    def _field_value(item: ProductEvidence, metric_id: str) -> str | None:
        for field in item.fields:
            if field.metric_id == metric_id and field.normalized_value not in (None, ""):
                return str(field.normalized_value)
        return None

    @staticmethod
    def _bounded_detail_question(prefix: str, detail: str, suffix: str) -> str:
        """Fit dynamic clarification detail while preserving the instruction."""

        maximum = 500
        text = f"{prefix}{detail}{suffix}"
        if len(text) <= maximum:
            return text
        detail_budget = maximum - len(prefix) - len(suffix)
        if detail_budget <= 1:
            return (prefix + suffix)[:maximum]
        clipped = detail[: detail_budget - 1].rstrip() + "…"
        return f"{prefix}{clipped}{suffix}"

    @classmethod
    def _identity_option(cls, item: ProductEvidence, scope: str) -> ClarificationOption | None:
        product_id = cls._field_value(item, "product.id")
        if not product_id:
            return None
        response_prefix = {
            "bond": "국내채권 코드",
            "domestic_etp": "국내 ETF·ETN 코드",
            "overseas_etp": "해외 티커",
            "fund": "공모펀드 코드",
        }[scope]
        descriptors: list[str] = []
        for metric_id, label in (
            ("product.internal_type", "유형"),
            ("product.market", "시장"),
            ("product.currency", "통화"),
        ):
            value = cls._field_value(item, metric_id)
            if value:
                descriptors.append(f"{label} {value}")
        source_row = next((field.source_excel_row for field in item.fields), None)
        if source_row:
            descriptors.append(f"원본 Excel {source_row}행")
        value = f"{response_prefix} {product_id}"
        if len(value) > 100:
            # Truncating an identifier would make the follow-up select a
            # different (or no) product, so omit that option instead.
            return None
        suffix = f" · {product_id}"
        label_budget = 160 - len(suffix)
        if len(item.name) > label_budget:
            label_name = item.name[: max(label_budget - 1, 1)].rstrip() + "…"
        else:
            label_name = item.name
        description = " · ".join(descriptors) or "상품코드로 구분"
        if len(description) > 300:
            description = description[:299].rstrip() + "…"
        return ClarificationOption(
            value=value,
            label=f"{label_name}{suffix}",
            description=description,
        )

    async def _clarify_ambiguous_target(
        self,
        *,
        original_question: str,
        plan: QueryPlan,
    ) -> EvidenceBundle | None:
        """Resolve cardinality before a potentially large lookup is executed."""

        if (
            plan.intent not in {"lookup", "explain"}
            or len(plan.scopes) != 1
            or not plan.entities
            or not self.engine.policy_decision(plan).allowed
        ):
            return None
        async with self._db_semaphore:
            resolutions = await asyncio.to_thread(self.engine.resolve_entity_matches, plan)
        ambiguous = [
            (index, label, matches)
            for index, (label, matches) in enumerate(resolutions)
            if len(matches) > 1
        ]
        if not ambiguous:
            return None

        options: list[ClarificationOption] = []
        if len(ambiguous) == 1:
            entity_index, _, _ = ambiguous[0]
            candidate_plan = QueryPlan.model_validate(
                {
                    **plan.model_dump(mode="json"),
                    # Search mode returns only compact identity evidence.  It
                    # must not load every requested lookup field merely to
                    # construct a bounded list of disambiguation choices.
                    "intent": "search",
                    "entities": [plan.entities[entity_index].model_dump(mode="json")],
                    "metrics": [],
                    "aggregations": [],
                    "sort": [],
                    "group_by": [],
                    "limit": 12,
                }
            )
            async with self._db_semaphore:
                candidates = await asyncio.to_thread(self.engine.execute, candidate_plan)
            options = [
                option
                for item in candidates.items
                if (option := self._identity_option(item, plan.scopes[0])) is not None
            ]
        if len(options) < 2:
            options = [
                ClarificationOption(
                    value="정확한 상품코드 입력",
                    label="상품코드로 다시 지정",
                    description="티커·종목번호·ISIN 등 정확한 상품코드를 입력",
                ),
                ClarificationOption(
                    value="더 구체적인 상품명 입력",
                    label="상품명으로 다시 지정",
                    description="운용사·발행기관·클래스 등을 포함한 더 구체적인 상품명을 입력",
                ),
            ]
        token = self._encode_clarification(
            original_question=original_question,
            missing_slots=["product_identity"],
            preserved_plan=self._signed_plan_snapshot(plan),
        )
        entity = plan.entities[ambiguous[0][0]]
        identity = entity.code or entity.name or "요청한 식별자"
        is_name_only = all(
            plan.entities[index].name and not plan.entities[index].code
            for index, _, _ in ambiguous
        )
        reason_code = (
            "AMBIGUOUS_PRODUCT_NAME" if is_name_only else "AMBIGUOUS_PRODUCT_IDENTITY"
        )
        question = (
            f"'{identity}' 식별자에 해당하는 상품이 여러 개입니다. 어느 상품코드를 조회할까요?"
            if len(ambiguous) == 1
            else (
                f"요청한 상품 식별자 중 여러 상품에 일치하는 항목이 {len(ambiguous)}개입니다. "
                "정확한 상품코드 또는 더 구체적인 상품명으로 다시 지정해 주세요."
            )
        )
        clarification = ClarificationRequest(
            question=question,
            missing_slots=["product_identity"],
            options=options,
            preserved_plan=plan.model_dump(mode="json"),
            clarification_token=token,
        )
        async with self._db_semaphore:
            return await asyncio.to_thread(
                self.engine.empty_bundle,
                plan,
                answerability=Answerability.NEEDS_CLARIFICATION,
                reason_code=reason_code,
                limitation=(
                    "동일하거나 유사한 상품명을 임의로 하나 선택하지 않았습니다. "
                    "상품코드로 구분해 주세요."
                ),
                clarification=clarification,
            )

    @staticmethod
    def _replace_response_limitation(evidence: EvidenceBundle, message: str) -> None:
        evidence.limitations = [
            item
            for item in evidence.limitations
            if not item.startswith(_RESPONSE_TRUNCATION_PREFIX)
        ]
        if len(evidence.limitations) >= 30:
            evidence.limitations[-1] = message
        else:
            evidence.limitations.append(message)

    @classmethod
    def _bounded_response_evidence(cls, evidence: EvidenceBundle) -> EvidenceBundle:
        """Return evidence that always fits the public serialized-context limit."""

        bounded = evidence.model_copy(deep=True)
        if bounded.answerability == Answerability.NEEDS_CLARIFICATION:
            # A clarification is a decision request, not a partial result.  Do
            # not retain a large result that the service explicitly refused to
            # choose among.
            bounded.result_count = 0
            bounded.items = []
            bounded.aggregates = []
            bounded.calculation = None

        if len(bounded.model_dump_json(exclude_none=False)) <= _RETRIEVED_CONTEXT_MAX_CHARS:
            return bounded

        original_count = evidence.result_count
        while bounded.items or bounded.aggregates:
            if bounded.items:
                bounded.items.pop()
            else:
                bounded.aggregates.pop()
            bounded.result_count = len(bounded.items) or len(bounded.aggregates)
            bounded.answerability = Answerability.PARTIAL_WITH_COVERAGE
            bounded.reason_code = "RESPONSE_TRUNCATED"
            cls._replace_response_limitation(
                bounded,
                (
                    f"{_RESPONSE_TRUNCATION_PREFIX} {original_count:,}건 중 앞에서 "
                    f"{bounded.result_count:,}건만 반환했습니다. 조건을 좁혀 다시 조회해 주세요."
                ),
            )
            if not bounded.items and not bounded.aggregates:
                break
            if (
                len(bounded.model_dump_json(exclude_none=False))
                <= _RETRIEVED_CONTEXT_TARGET_CHARS
            ):
                return bounded

        # One result (or non-result metadata) can theoretically exceed the
        # budget because FieldEvidence preserves literal source cells.  In that
        # case return a small, honest refusal instead of slicing JSON or raising
        # a response-model ValidationError.
        bounded.answerability = Answerability.DATA_QUALITY_BLOCKED
        bounded.reason_code = "RESPONSE_SIZE_LIMIT"
        bounded.result_count = 0
        bounded.coverage = None
        bounded.clarification = None
        bounded.calculation = None
        bounded.items = []
        bounded.aggregates = []
        bounded.limitations = [
            "원본 근거가 응답 크기 제한을 넘어 안전하게 반환할 수 없습니다. "
            "상품코드·상품군·지표 조건을 더 좁혀 다시 조회해 주세요."
        ]
        return bounded

    @staticmethod
    def _policy_validation_text(answer: str, evidence: EvidenceBundle) -> str:
        """Mask exact source-backed names before semantic prose validation."""

        policy_text = answer
        names = sorted({item.name for item in evidence.items}, key=len, reverse=True)
        for name in names:
            policy_text = policy_text.replace(name, "[근거 상품명]")
        return policy_text

    async def _clarify_unresolved_compare(
        self,
        *,
        original_question: str,
        plan: QueryPlan,
        evidence: EvidenceBundle,
    ) -> None:
        if plan.intent != "compare":
            return
        async with self._db_semaphore:
            resolutions = await asyncio.to_thread(self.engine.resolve_entity_matches, plan)
        unresolved = [
            (label, len(matches))
            for label, matches in resolutions
            if len(matches) != 1
        ]
        resolved_uids = [matches[0] for _, matches in resolutions if len(matches) == 1]
        duplicate_uids = {uid for uid in resolved_uids if resolved_uids.count(uid) > 1}
        if duplicate_uids:
            unresolved.extend(
                (label, 1)
                for label, matches in resolutions
                if len(matches) == 1 and matches[0] in duplicate_uids
            )
        if not unresolved:
            return
        summary = ", ".join(f"{label}({count}건)" for label, count in unresolved)
        if duplicate_uids:
            summary += "; 서로 다른 입력이 동일 상품을 가리킴"
        token = self._encode_clarification(
            original_question=original_question,
            missing_slots=["comparison_target_identity"],
            preserved_plan=self._signed_plan_snapshot(plan),
        )
        evidence.answerability = Answerability.NEEDS_CLARIFICATION
        evidence.reason_code = "COMPARE_TARGET_NOT_UNIQUE"
        evidence.clarification = ClarificationRequest(
            question=self._bounded_detail_question(
                "비교 대상 중 정확히 한 상품으로 확인되지 않은 항목이 있습니다: ",
                summary,
                ". 정확한 상품을 어떻게 다시 지정할까요?",
            ),
            missing_slots=["comparison_target_identity"],
            options=[
                ClarificationOption(value="정확한 상품코드 입력", label="상품코드로 다시 지정"),
                ClarificationOption(value="정확한 상품명 2개 입력", label="상품명으로 다시 지정"),
            ],
            preserved_plan=plan.model_dump(mode="json"),
            clarification_token=token,
        )
        evidence.limitations.append(
            "비교 대상이 누락되거나 여러 상품에 일치해 불완전한 비교 결과를 확정하지 않았습니다."
        )

    async def answer(
        self,
        *,
        question_id: str,
        question: str,
        clarification_token: str | None = None,
        clarification_response: str | None = None,
    ) -> OrganizerResponse:
        execution_question = question
        follow_up_state: ClarificationState | None = None
        if clarification_token or clarification_response:
            if not self.settings.enable_clarification_state:
                raise ClarificationTokenError("clarification follow-up state is disabled")
            if not clarification_token or not clarification_response:
                raise ClarificationTokenError(
                    "clarification_token and clarification_response must be supplied together"
                )
            if self.clarification_codec is None:
                raise ClarificationTokenError("clarification follow-up state is disabled")
            follow_up_state, execution_question = self.clarification_codec.resolve_follow_up(
                clarification_token, clarification_response
            )

        safety = evaluate_question(execution_question)
        if safety.blocked:
            plan = QueryPlan(
                intent="unsupported",
                scopes=[],
                assumptions=[f"policy_reason={safety.reason_code}"],
            )
            async with self._db_semaphore:
                evidence = await asyncio.to_thread(
                    self.engine.empty_bundle,
                    plan,
                    answerability=Answerability(safety.answerability or "SAFETY_LIMITED"),
                    reason_code=safety.reason_code or "SAFETY_POLICY",
                    limitation=safety.message or "금융 안전정책상 처리할 수 없습니다.",
                )
        else:
            plan = self._preflight_clarification(execution_question)
            if plan is None:
                try:
                    plan = await self.planner.plan(execution_question)
                except HCXError as exc:
                    raise PlannerUnavailable("HCX planner is temporarily unavailable") from exc
            if follow_up_state is not None:
                # Restore signed constraints before ambiguity guards run, so a
                # planner omission cannot manufacture a new scope question.
                plan = self._reconcile_follow_up_plan(follow_up_state, plan)
            plan = self._enforce_required_clarification(execution_question, plan)
            if follow_up_state is not None:
                # Guards may canonicalize fields; re-check that this did not
                # replace any condition established in the previous turn.
                plan = self._reconcile_follow_up_plan(follow_up_state, plan)
            if plan.needs_clarification:
                token = self._encode_clarification(
                    original_question=execution_question,
                    missing_slots=plan.missing_slots,
                    preserved_plan=self._signed_plan_snapshot(plan),
                )
                clarification = plan.clarification(token)
                async with self._db_semaphore:
                    evidence = await asyncio.to_thread(
                        self.engine.empty_bundle,
                        plan,
                        answerability=Answerability.NEEDS_CLARIFICATION,
                        reason_code="MISSING_CONDITION",
                        limitation="결과를 임의로 바꾸지 않기 위해 필요한 조건을 확인합니다.",
                        clarification=clarification,
                    )
            elif plan.intent == "unsupported":
                reason = next(
                    (
                        item.split("=", 1)[1]
                        for item in plan.assumptions
                        if item.startswith("policy_reason=")
                    ),
                    "UNSUPPORTED_REQUEST",
                )
                if reason not in SUPPORTED_POLICY_REASONS:
                    reason = "UNSUPPORTED_REQUEST"
                if reason == "RETURN_METRICS_UNAVAILABLE":
                    answerability = Answerability.UNAVAILABLE
                    limitation = (
                        "선택한 상품군에는 순위·비교에 사용할 수 있는 수익률 기간이 없습니다. "
                        "해외 ETP는 제공된 1일 수익률이 전부 0인 상수이고 장기 수익률 필드는 "
                        "없습니다. 대신 AUM·종가·거래량을 각각의 단위·기준일 제한과 함께 "
                        "조회할 수 있습니다."
                    )
                elif reason == "CROSS_PRODUCT_LOCKED_PENDING_BASIS":
                    answerability = Answerability.INCOMPARABLE
                    limitation = (
                        "상품군별 수익률의 단위·기준일 정책이 확정되지 않아 교차 비교를 "
                        "실행하지 않습니다."
                    )
                elif reason == "COMPARISON_METRIC_UNAVAILABLE":
                    answerability = Answerability.UNAVAILABLE
                    limitation = "선택한 상품군에서 안전하게 비교할 수 있는 원본 지표를 확인할 수 없습니다."
                elif reason == "CLARIFICATION_STATE_CONFLICT":
                    answerability = Answerability.SAFETY_LIMITED
                    limitation = (
                        "재질문 전 확정된 상품군·상품·필터·지표와 후속 계획이 충돌해 "
                        "실행하지 않았습니다. 처음 질문부터 다시 확인해 주세요."
                    )
                elif reason.startswith("CATALOG_VALUE_"):
                    answerability = Answerability.UNAVAILABLE
                    limitation = (
                        "요청한 상품 분류 표현을 선택한 상품군의 원본 라벨 하나로 안전하게 "
                        "확정할 수 없습니다. 상품군과 원본 분류값을 더 구체적으로 지정해 주세요."
                    )
                else:
                    answerability = Answerability.SAFETY_LIMITED
                    limitation = (
                        "요청은 제공 데이터의 조회·비교 범위 또는 금융 안전정책을 벗어납니다."
                    )
                async with self._db_semaphore:
                    evidence = await asyncio.to_thread(
                        self.engine.empty_bundle,
                        plan,
                        answerability=answerability,
                        reason_code=reason,
                        limitation=limitation,
                    )
            else:
                ambiguity = await self._clarify_ambiguous_target(
                    original_question=execution_question,
                    plan=plan,
                )
                if ambiguity is not None:
                    evidence = ambiguity
                else:
                    async with self._db_semaphore:
                        evidence = await asyncio.to_thread(self.engine.execute, plan)
                    await self._clarify_unresolved_compare(
                        original_question=execution_question,
                        plan=plan,
                        evidence=evidence,
                    )

        evidence = self._bounded_response_evidence(evidence)
        answer = render_answer(plan, evidence)
        if evidence.answerability in {
            Answerability.FULL,
            Answerability.PARTIAL_WITH_COVERAGE,
            Answerability.NO_RESULT,
        }:
            validate_rendered_policy(self._policy_validation_text(answer, evidence))
        filter_fields = ",".join(
            condition.field
            for group in plan.filter_groups
            for condition in group.conditions
        ) or "none"
        sort_summary = ",".join(
            f"{item.field}:{item.direction}:nulls_{item.nulls}" for item in plan.sort
        ) or "none"
        trace = (
            f"intent={plan.intent}; scopes={','.join(plan.scopes) or 'unresolved'}; "
            f"metrics={','.join(plan.metrics) or 'none'}; groups={len(plan.filter_groups)}; "
            f"filter_fields={filter_fields}; sort={sort_summary}; "
            f"result_count={evidence.result_count}; answerability={evidence.answerability}; "
            f"planner={self.planner.name}; fallback_llm=none; executor=DuckDB; data=2026-07-11"
        )
        context = evidence.model_dump_json(exclude_none=False)
        if len(context) > _RETRIEVED_CONTEXT_MAX_CHARS:
            raise AssertionError("bounded evidence exceeded OrganizerResponse context limit")
        return OrganizerResponse(
            question_id=question_id,
            question=question,
            retrieved_context=context,
            think_trace=trace,
            answer=answer,
        )

    async def aclose(self) -> None:
        close = getattr(self.planner, "aclose", None)
        if close:
            await close()
