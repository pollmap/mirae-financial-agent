"""Small, deterministic Korean parser for development and safe fallback.

This module is not a substitute for HyperCLOVA X.  It deliberately supports
only an allow-listed slice and asks a clarification question instead of
guessing when scope, period, or ranking priority changes the result.
"""

from __future__ import annotations

import re

from app.domain.models import (
    Aggregation,
    ClarificationOption,
    Condition,
    Entity,
    FilterGroup,
    QueryPlan,
    SortItem,
)
from app.execution.registry import MetricRegistry
from app.planner.catalog_filters import resolve_catalog_filters
from app.safety import evaluate_question

_LIMIT = re.compile(r"(?:상위\s*)?(\d{1,2})\s*(?:개(?!월)|건|위)")
_CODES = re.compile(
    r"(?<![A-Za-z0-9])(?:KR[A-Z0-9]{8,12}|XS[A-Z0-9]{8,12}|US[A-Z0-9]{8,12}|[AQ]\d{6}|[A-Z]{1,6}(?:\.[A-Z]))(?![A-Za-z0-9])"
    r"|(?<=티커\s)[A-Z]{1,6}(?![A-Za-z0-9])"
)

_LOOKUP_SCOPE_PREFIX = re.compile(
    r"^(?:(?:국내|해외)\s*)?"
    r"(?:채권|ETF|ETN|ETP|상장지수상품|공모펀드|펀드)\s*(?:상품\s*)?",
    flags=re.IGNORECASE,
)
_LOOKUP_SCOPE_SUFFIX = re.compile(
    r"\s*(?:상품\s*)?(?:(?:국내|해외)\s*)?"
    r"(?:채권|ETF|ETN|ETP|상장지수상품|공모펀드|펀드)$",
    flags=re.IGNORECASE,
)


def _lookup_name(question: str) -> str:
    """Remove only explicit request/scope wrappers, never product-name internals."""

    name = re.sub(
        r"(을|를)?\s*(찾아\s*줘|조회해\s*줘|상세.*)",
        "",
        question,
        flags=re.IGNORECASE,
    ).strip(" .")
    name = _LOOKUP_SCOPE_PREFIX.sub("", name, count=1).strip()
    name = _LOOKUP_SCOPE_SUFFIX.sub("", name, count=1).strip()
    return name


def _infer_scopes(question: str) -> list[str]:
    q = question
    scopes: list[str] = []
    if "domestic_etp" in q:
        scopes.append("domestic_etp")
    if "overseas_etp" in q:
        scopes.append("overseas_etp")
    if re.search(r"(?:^|\W)bond(?:$|\W)", q, flags=re.IGNORECASE):
        scopes.append("bond")
    if re.search(r"(?:^|\W)fund(?:$|\W)", q, flags=re.IGNORECASE):
        scopes.append("fund")
    if "채권" in q and (
        "펀드" not in q
        or any(
            token in q
            for token in ("국내채권", "채권 표면금리", "채권 신용등급", "채권과", "채권·")
        )
    ):
        scopes.append("bond")
    if any(k in q for k in ("펀드", "공모", "사모")):
        scopes.append("fund")
    has_etp = any(k in q for k in ("ETF", "ETN", "ETP", "상장지수"))
    if has_etp:
        if "국내와 해외" in q or "국내·해외" in q or "국내 ETF와 해외 ETF" in q:
            scopes.extend(["domestic_etp", "overseas_etp"])
        elif "해외" in q or re.search(r"미국\s*(?:증시|상장)|NYSE|NASDAQ", q, re.IGNORECASE):
            scopes.append("overseas_etp")
        elif "국내" in q:
            scopes.append("domestic_etp")
        else:
            # An explicit foreign ticker/ISIN is enough to resolve the market.
            if re.search(r"\bUS[A-Z0-9]{10}\b", q) or "해외 티커" in q:
                scopes.append("overseas_etp")
    if "국내 ETF와 공모펀드" in q or "ETF와 공모펀드" in q:
        scopes = ["domestic_etp", "fund"]
    if re.search(r"(?<![A-Za-z0-9])[AQ]\d{6}(?![A-Za-z0-9])", q):
        scopes.append("domestic_etp")
    if re.search(r"(?<![A-Za-z0-9])XS[A-Z0-9]{8,12}(?![A-Za-z0-9])", q):
        scopes.append("bond")
    if "해외 티커" in q or "ISIN" in q.upper() or re.search(r"US[A-Z0-9]{10}", q):
        scopes.append("overseas_etp")
    return list(dict.fromkeys(scopes))


def _requested_metrics(question: str, scopes: list[str]) -> list[str]:
    q = question.replace(" ", "")
    scope = scopes[0] if len(scopes) == 1 else None
    metrics: list[str] = []

    if scope:
        canonical = re.findall(
            rf"(?:{re.escape(scope)}\.[a-z0-9_]+|product\.(?:benchmark|strategy))",
            question,
            flags=re.IGNORECASE,
        )
        metrics.extend(item.lower() for item in canonical)
    if "표면금리" in q:
        metrics.append("bond.coupon_rate")
    if "신용등급" in q:
        metrics.append("bond.credit_rating")
    if "매수수익률" in q:
        metrics.append("bond.buy_yield")
    if "매수가능수량" in q:
        metrics.append("bond.buyable_quantity")
    if "보수" in q and scope in {"domestic_etp", "overseas_etp", "fund"}:
        metrics.append(f"{scope}.expense_ratio")
    elif "보수" in q and len(scopes) > 1:
        metrics.append("cross.expense_ratio")
    if "기초지수" in q or "벤치마크" in q:
        if scope == "domestic_etp":
            metrics.append("domestic_etp.base_index")
        elif scope in {"overseas_etp", "fund"}:
            metrics.append("product.benchmark")
    if "운용전략" in q or q.endswith("전략"):
        metrics.append("product.strategy")
    if "AUM" in question.upper() and scope in {"domestic_etp", "overseas_etp"}:
        metrics.append(f"{scope}.aum_last")
    elif "AUM" in question.upper() and len(scopes) > 1:
        metrics.append("cross.aum_last")
    if "NAV" in question.upper() and scope in {"domestic_etp", "overseas_etp"}:
        metrics.append(f"{scope}.nav_last")
    if "순자산" in q and scope in {"domestic_etp", "fund"}:
        metrics.append(
            "domestic_etp.net_assets" if scope == "domestic_etp" else "fund.net_assets"
        )
    if "종가" in q and scope in {"domestic_etp", "overseas_etp"}:
        metrics.append(f"{scope}.close_price")
    if "거래량" in q and scope in {"domestic_etp", "overseas_etp"}:
        metrics.append(f"{scope}.volume_1d")
    if "괴리율" in q and scope in {"domestic_etp", "overseas_etp"}:
        metrics.append(f"{scope}.discrepancy_rate")
    if "위험등급" in q and scope in {"bond", "fund"}:
        metrics.append(f"{scope}.risk_grade")
    if "수익률" in q:
        period = None
        for token, suffix in (
            ("1일", "1d"),
            ("1주", "1w"),
            ("1개월", "1m"),
            ("3개월", "3m"),
            ("6개월", "6m"),
            ("18개월", "18m"),
            ("1년", "1y"),
            ("2년", "2y"),
            ("3년", "3y"),
            ("5년", "5y"),
            ("YTD", "ytd"),
        ):
            if token in question.upper():
                period = suffix
                break
        if period and scope:
            metrics.append(f"{scope}.return_{period}")
        elif period and len(scopes) > 1:
            metrics.append(f"cross.return_{period}")
    return list(dict.fromkeys(metrics))


def _metric(question: str, scopes: list[str]) -> str | None:
    metrics = _requested_metrics(question, scopes)
    return metrics[0] if metrics else None


def _multiple_rank_metrics(question: str, scopes: list[str]) -> list[tuple[str, str, str]]:
    """Return (metric_id, direction, Korean label) for explicitly mentioned metrics."""

    if len(scopes) != 1:
        return []
    scope = scopes[0]
    candidates: list[tuple[str, str, str]] = []
    if "보수" in question and scope in {"domestic_etp", "overseas_etp", "fund"}:
        candidates.append((f"{scope}.expense_ratio", "asc", "보수"))
    if "AUM" in question.upper() and scope in {"domestic_etp", "overseas_etp"}:
        candidates.append((f"{scope}.aum_last", "desc", "AUM"))
    if "순자산" in question and scope in {"domestic_etp", "fund"}:
        metric = "domestic_etp.net_assets" if scope == "domestic_etp" else "fund.net_assets"
        candidates.append((metric, "desc", "순자산"))
    if "종가" in question and scope in {"domestic_etp", "overseas_etp"}:
        candidates.append((f"{scope}.close_price", "desc", "종가"))
    if "거래량" in question and scope in {"domestic_etp", "overseas_etp"}:
        candidates.append((f"{scope}.volume_1d", "desc", "거래량"))
    requested_return = next(
        (metric for metric in _requested_metrics(question, scopes) if ".return_" in metric),
        None,
    )
    if requested_return:
        period = requested_return.rsplit("return_", 1)[-1]
        candidates.append(
            (requested_return, "desc", f"{_RETURN_OPTION_LABELS.get(period, period)} 수익률")
        )
    return list(dict.fromkeys(candidates))


_RETURN_OPTION_PREFERENCE = {
    "domestic_etp": ("1m", "3m", "1y", "6m"),
    "overseas_etp": ("1m", "3m", "1y", "1d"),
    "fund": ("1m", "1y", "3y", "3m"),
}
_RETURN_OPTION_LABELS = {
    "1d": "1일",
    "1w": "1주",
    "1m": "1개월",
    "3m": "3개월",
    "6m": "6개월",
    "ytd": "YTD",
    "18m": "18개월",
    "1y": "1년",
    "2y": "2년",
    "3y": "3년",
    "5y": "5년",
}

_COMPARISON_METRIC_OPTIONS = {
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


def _preferred_return_periods(registry: MetricRegistry, scopes: list[str]) -> list[str]:
    if not scopes:
        return []
    available_by_scope = [set(registry.available_return_periods(scope)) for scope in scopes]
    available = set.intersection(*available_by_scope)
    preferred = _RETURN_OPTION_PREFERENCE.get(scopes[0], tuple(_RETURN_OPTION_LABELS))
    ordered = [period for period in preferred if period in available]
    ordered.extend(period for period in _RETURN_OPTION_LABELS if period in available and period not in ordered)
    return ordered[:4]


def _clarification(
    question: str,
    scopes: list[str],
    missing: str,
    *,
    return_periods: list[str] | None = None,
) -> QueryPlan:
    if missing == "scope":
        prompt = "어느 상품군을 대상으로 볼까요?"
        options = [
            ClarificationOption(value="bond", label="국내채권"),
            ClarificationOption(value="domestic_etp", label="국내 ETF·ETN"),
            ClarificationOption(value="overseas_etp", label="해외 ETF·ETN"),
            ClarificationOption(value="fund", label="공모펀드"),
        ]
    elif missing == "market":
        prompt = "ETF·ETN의 시장을 국내와 해외 중 어느 쪽으로 볼까요?"
        options = [
            ClarificationOption(value="domestic_etp", label="국내"),
            ClarificationOption(value="overseas_etp", label="해외"),
        ]
    elif missing == "return_period":
        prompt = "수익률 비교 기간을 선택해 주세요. 상품군에 실제 제공된 기간만 실행합니다."
        options = [
            ClarificationOption(value=period, label=_RETURN_OPTION_LABELS[period])
            for period in (return_periods or [])
        ]
    else:
        prompt = "여러 정렬 조건의 우선순위를 알려주세요."
        options = []
    return QueryPlan(
        intent="clarify",
        scopes=scopes,
        needs_clarification=True,
        clarification_question=prompt,
        missing_slots=[missing],
        clarification_options=options,
        preserved_plan={"original_question": question, "resolved_scopes": scopes},
        limit=10,
    )


class DeterministicPlanner:
    """Parse only known query forms; fail closed on ambiguity."""

    name = "deterministic"

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or MetricRegistry.load()

    def _comparison_metric_options(self, scopes: list[str]) -> list[ClarificationOption]:
        if len(scopes) != 1:
            return []
        options: list[ClarificationOption] = []
        for metric_id, label in _COMPARISON_METRIC_OPTIONS.get(scopes[0], ()):
            policy = self.registry.get(metric_id)
            if policy is None or policy.source_field is None or policy.quality_status == "UNAVAILABLE":
                continue
            options.append(
                ClarificationOption(
                    value=metric_id,
                    label=label,
                    description="선택한 원본 지표로 두 상품을 나란히 비교",
                )
            )
        return options[:4]

    async def plan(self, question: str) -> QueryPlan:
        safety = evaluate_question(question)
        scopes = _infer_scopes(question)
        if safety.blocked:
            return QueryPlan(
                intent="unsupported",
                scopes=scopes,
                assumptions=[f"policy_reason={safety.reason_code}"],
            )

        has_etp = any(k in question for k in ("ETF", "ETN", "ETP", "상장지수"))
        if not scopes:
            if has_etp:
                return _clarification(question, [], "market")
            if any(k in question for k in ("상품", "수익률", "보수", "AUM", "위험")):
                return _clarification(question, [], "scope")

        requested_metrics = _requested_metrics(question, scopes)
        metric = requested_metrics[0] if requested_metrics else None
        if "수익률" in question and metric is None:
            periods = _preferred_return_periods(self.registry, scopes)
            if len(periods) < 2:
                return QueryPlan(
                    intent="unsupported",
                    scopes=scopes,
                    assumptions=["policy_reason=RETURN_METRICS_UNAVAILABLE"],
                )
            return _clarification(
                question,
                scopes,
                "return_period",
                return_periods=periods,
            )

        limit_match = _LIMIT.search(question)
        limit = min(int(limit_match.group(1)), 50) if limit_match else 10
        multi_metrics = _multiple_rank_metrics(question, scopes)

        count_request = any(
            k in question for k in ("몇 개인", "몇 개인가", "몇 개", "몇 건", "수는")
        )
        rank_request = (
            any(
                k in question
                for k in (
                    "상위",
                    "하위",
                    "최대",
                    "최소",
                    "높은 순",
                    "낮은 순",
                    "높은",
                    "낮은",
                    "가장 높은",
                    "가장 낮은",
                    "큰 순",
                    "큰 ",
                    "작은",
                    "많은",
                    "적은",
                    "좋은",
                    "순서대로",
                    "정렬",
                )
            )
            and metric is not None
        )
        # "비교" plus a rank keyword is ambiguous Korean phrasing: "국내 ETN을
        # AUM이 큰 순서로 비교해줘" means "rank/compare the whole category"
        # (no named targets -> rank_request should win, tested by
        # GOLD-D08), but "A와 B를 보수가 낮은 게 어느 건지 비교해줘" names two
        # specific products and should never fall through to a silent
        # "rank the entire universe" answer that drops them. The two cases
        # are told apart by whether >=2 explicit product codes are present:
        # rank_request alone can't check that (codes/entities are extracted
        # further down), so do a cheap, code-only pre-check here.
        explicit_targets = len(_CODES.findall(question)) >= 2
        compare_request = "비교" in question and (not rank_request or explicit_targets)

        if rank_request and len(multi_metrics) > 1:
            explicit_priority = next(
                (label for _, _, label in multi_metrics if f"{label} 우선" in question), None
            )
            if explicit_priority is None and not any(
                token in question for token in ("그다음", "동률이면", "1순위", "2순위")
            ):
                return QueryPlan(
                    intent="clarify",
                    scopes=scopes,
                    needs_clarification=True,
                    clarification_question=(
                        "여러 정렬조건을 합산 점수로 추정하지 않습니다. 어느 지표를 먼저 정렬할까요?"
                    ),
                    missing_slots=["ranking_priority"],
                    clarification_options=[
                        ClarificationOption(
                            value=metric_id,
                            label=f"{label} 우선",
                            description="선택 지표를 1차 정렬하고 다른 지표를 동률 기준으로 사용",
                        )
                        for metric_id, _, label in multi_metrics
                    ],
                    preserved_plan={
                        "original_question": question,
                        "resolved_scopes": scopes,
                        "metrics": [item[0] for item in multi_metrics],
                        "limit": limit,
                    },
                    limit=limit,
                )
            if explicit_priority:
                multi_metrics.sort(key=lambda item: item[2] != explicit_priority)

        code_matches = list(_CODES.finditer(question))
        codes = [match.group(0) for match in code_matches]
        if scopes == ["overseas_etp"]:
            reserved = {
                "ETF",
                "ETN",
                "ETP",
                "USD",
                "KRW",
                "AUM",
                "NAV",
                "ISIN",
                "YTD",
                "NYSE",
            }
            claimed_spans = [match.span() for match in code_matches]
            supplemental_codes = []
            for match in re.finditer(r"(?<![A-Za-z0-9])[A-Z]{1,6}(?![A-Za-z0-9])", question):
                token = match.group(0)
                if token in reserved or token in codes:
                    continue
                # Skip fragments of an already-claimed code (e.g. the bare
                # "AAAA" and "K" this pattern would otherwise also find
                # inside a dot-ticker like "AAAA.K" already matched above).
                start, end = match.span()
                if any(start < c_end and c_start < end for c_start, c_end in claimed_spans):
                    continue
                supplemental_codes.append(token)
            codes.extend(supplemental_codes[: 10 - len(codes)])
        entities: list[Entity] = []
        for code in codes[:10]:
            entity_scope = scopes[0] if len(scopes) == 1 else None
            entities.append(Entity(code=code, scope=entity_scope))

        if compare_request:
            if len(entities) < 2:
                return QueryPlan(
                    intent="clarify",
                    scopes=scopes,
                    needs_clarification=True,
                    clarification_question="비교할 상품 두 개 이상을 상품명이나 코드로 지정해 주세요.",
                    missing_slots=["comparison_targets"],
                    clarification_options=[
                        ClarificationOption(value="상품명 2개 입력", label="상품명으로 지정"),
                        ClarificationOption(value="상품코드 2개 입력", label="상품코드로 지정"),
                    ],
                    preserved_plan={
                        "original_question": question,
                        "resolved_scopes": scopes,
                        "metrics": requested_metrics,
                    },
                    limit=limit,
                )
            if not requested_metrics:
                options = self._comparison_metric_options(scopes)
                if len(options) < 2:
                    return QueryPlan(
                        intent="unsupported",
                        scopes=scopes,
                        assumptions=["policy_reason=COMPARISON_METRIC_UNAVAILABLE"],
                    )
                return QueryPlan(
                    intent="clarify",
                    scopes=scopes,
                    needs_clarification=True,
                    clarification_question="어떤 지표로 상품을 비교할까요?",
                    missing_slots=["comparison_metric"],
                    clarification_options=options,
                    preserved_plan={
                        "original_question": question,
                        "resolved_scopes": scopes,
                        "entities": [entity.model_dump(mode="json") for entity in entities],
                    },
                    limit=limit,
                )
            return QueryPlan(
                intent="compare",
                scopes=scopes,
                entities=entities,
                metrics=requested_metrics,
                limit=limit,
            )

        if "설명" in question:
            if not entities:
                return QueryPlan(
                    intent="clarify",
                    scopes=scopes,
                    needs_clarification=True,
                    clarification_question=(
                        "원본 상품 정보로 설명할 대상을 상품명 또는 상품코드 중 어떻게 지정할까요?"
                    ),
                    missing_slots=["explanation_target"],
                    clarification_options=[
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
                    preserved_plan={
                        "original_question": question,
                        "resolved_scopes": scopes,
                    },
                    limit=limit,
                )
            return QueryPlan(
                intent="explain",
                scopes=scopes,
                entities=entities,
                metrics=requested_metrics,
                limit=limit,
            )

        catalog_resolution = resolve_catalog_filters(question, scopes)
        if catalog_resolution.reason_code:
            return QueryPlan(
                intent="unsupported",
                scopes=scopes,
                assumptions=[f"policy_reason={catalog_resolution.reason_code}"],
            )

        if entities or (
            not catalog_resolution.conditions
            and any(k in question for k in ("상세", "조회", "찾아줘", "찾아 줘"))
        ):
            if not entities:
                name = _lookup_name(question)
                if name:
                    entities.append(
                        Entity(name=name, scope=scopes[0] if len(scopes) == 1 else None)
                    )
            if entities:
                return QueryPlan(
                    intent="lookup",
                    scopes=scopes,
                    entities=entities,
                    metrics=requested_metrics,
                    limit=limit,
                )

        filters: list[Condition] = list(catalog_resolution.conditions)
        if (
            scopes == ["overseas_etp"]
            and re.search(r"미국\s*주식형", question)
            and not any(condition.field == "product.region" for condition in filters)
        ):
            filters.append(
                Condition(
                    field="product.region",
                    op="eq",
                    value="United States of America",
                )
            )
        if "장내" in question:
            filters.append(Condition(field="bond.exchange", op="eq", value="장내"))
        if "장외" in question:
            filters.append(Condition(field="bond.exchange", op="eq", value="장외"))
        # Both substrings present ("ETF와 ETN", "ETF·ETN", "ETN/ETF", ...) means
        # "both types", not "filter to just one" -- and since FilterGroup is
        # always AND-joined, emitting internal_type='ETF' AND internal_type=
        # 'ETN' together would guarantee zero rows for any separator order.
        # Enumerating every connecting phrase/order is a losing game (a
        # previous version covered "ETF/ETN" but not "ETN/ETF"); checking
        # both substrings directly is order- and separator-agnostic.
        both_etf_and_etn_mentioned = "ETF" in question and "ETN" in question
        if "ETF" in question and not both_etf_and_etn_mentioned:
            filters.append(Condition(field="product.internal_type", op="eq", value="ETF"))
        if "ETN" in question and not both_etf_and_etn_mentioned:
            filters.append(Condition(field="product.internal_type", op="eq", value="ETN"))
        if "거래통화가 USD" in question or "거래통화 USD" in question:
            filters.append(Condition(field="product.trading_currency", op="eq", value="USD"))
        if scopes == ["fund"] and "사모" not in question and "공모펀드 속성행" not in question:
            filters.append(Condition(field="product.public_private", op="eq", value="공모"))
        if "사모" in question:
            filters.append(Condition(field="product.public_private", op="eq", value="사모"))
        if "판매중" in question:
            filters.append(Condition(field="product.sale_status", op="eq", value="판매중"))
        if "판매완료" in question:
            filters.append(Condition(field="product.sale_status", op="eq", value="판매완료"))
        if "0보다 큰" in question and metric:
            filters.append(Condition(field=metric, op="gt", value=0))

        filter_groups = [FilterGroup(conditions=filters)] if filters else []
        assumptions: list[str] = []
        if "원본" in question and not (metric and "제공" in question):
            assumptions.append("count_basis=raw")
        if any(k in question for k in ("격리", "placeholder")) or (
            "손상행" in question and "제외" not in question
        ):
            assumptions.append("count_basis=quarantine")
        if "속성행" in question:
            assumptions.append("count_basis=fund_attribute")
        if (
            count_request
            and metric
            and any(
                phrase in question
                for phrase in ("제공된", "제공 된", "값이 있는", "값 있는", "원본값 존재")
            )
        ):
            assumptions.append("metric_count_basis=source_present")

        if count_request:
            aggregations = [
                Aggregation(
                    function="count", field="product.id", alias="product_count", distinct=True
                )
            ]
            if len(scopes) > 1 and "각각" in question and metric is None:
                cross_filters: list[FilterGroup] = []
                if any(scope.endswith("etp") for scope in scopes):
                    if "ETF" in question and "ETN" not in question and "ETP" not in question:
                        cross_filters.append(
                            FilterGroup(
                                conditions=[
                                    Condition(
                                        field="product.internal_type", op="eq", value="ETF"
                                    )
                                ]
                            )
                        )
                    elif "ETN" in question and "ETF" not in question:
                        cross_filters.append(
                            FilterGroup(
                                conditions=[
                                    Condition(
                                        field="product.internal_type", op="eq", value="ETN"
                                    )
                                ]
                            )
                        )
                if "fund" in scopes and "공모" in question:
                    cross_filters.append(
                        FilterGroup(
                            conditions=[
                                Condition(
                                    field="product.public_private", op="eq", value="공모"
                                )
                            ]
                        )
                    )
                return QueryPlan(
                    intent="aggregate",
                    scopes=scopes,
                    filter_groups=cross_filters,
                    groups_join="OR" if len(cross_filters) > 1 else "AND",
                    aggregations=aggregations,
                    group_by=["product.scope"],
                    limit=limit,
                )
            group_by = (
                ["product.internal_type"]
                if "각각" in question and "ETF" in question and "ETN" in question
                else []
            )
            return QueryPlan(
                intent="aggregate",
                scopes=scopes,
                filter_groups=filter_groups,
                metrics=requested_metrics,
                aggregations=aggregations,
                group_by=group_by,
                assumptions=assumptions,
                limit=limit,
            )

        if rank_request:
            direction = (
                "asc"
                if any(k in question for k in ("낮은", "작은", "적은", "하위", "최소"))
                else "desc"
            )
            metrics = (
                [item[0] for item in multi_metrics]
                if multi_metrics
                else requested_metrics
            )
            if len(multi_metrics) > 1:
                # Same-scope explicit multi-metric priority: each metric
                # keeps its own detected direction (e.g. 보수 ascending
                # alongside 수익률 descending).
                sort = [
                    SortItem(field=metric_id, direction=item_direction)
                    for metric_id, item_direction, _ in multi_metrics
                ]
            elif len(metrics) > 1:
                # Cross-scope: `_requested_metrics` can return one metric per
                # scope (e.g. bond.coupon_rate + fund.return_1y for a
                # side-by-side rank). QueryPlan requires exactly one sort
                # item per metric, mirrored in the same order, regardless of
                # scope count.
                sort = [SortItem(field=metric_id, direction=direction) for metric_id in metrics]
            else:
                sort = [SortItem(field=metric or "product.id", direction=direction)]
            return QueryPlan(
                intent="rank",
                scopes=scopes,
                filter_groups=filter_groups,
                metrics=metrics,
                sort=sort,
                assumptions=assumptions,
                limit=limit,
            )

        return QueryPlan(
            intent="search",
            scopes=scopes,
            filter_groups=filter_groups,
            metrics=requested_metrics,
            assumptions=assumptions,
            limit=limit,
        )
