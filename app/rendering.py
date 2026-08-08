"""Evidence-only Korean response rendering for end users.

The API carries detailed, evaluator-friendly evidence in ``retrieved_context``.
This module deliberately renders a separate human-facing answer: no internal
field IDs, source-column names, retrieval trace, prompts, or implementation
terms are included in the prose shown to a user.
"""

from __future__ import annotations

import re

from app.domain.models import Answerability, EvidenceBundle, QueryPlan

MAX_ANSWER_CHARS = 30_000
MAX_DISPLAY_ITEMS = 20
_ANSWER_OMISSION = "\n응답 길이 제한으로 이후 항목을 생략했습니다. 조건을 더 구체적으로 알려 주세요."

_SCOPE_LABELS = {
    "bond": "국내채권",
    "domestic_etp": "국내 ETF·ETN",
    "overseas_etp": "해외 ETF·ETN",
    "fund": "공모펀드",
}

_CATALOG_LABELS = {
    "product.id": "상품코드",
    "product.internal_type": "상품유형",
    "product.market": "시장",
    "product.currency": "상품통화",
    "product.manager": "운용사",
    "product.manager_code": "운용사 코드",
    "product.issuer": "발행기관",
    "product.country_code": "국가코드",
    "bond.kind": "채권종류",
    "fund.domestic_overseas_class": "국내·해외 구분",
    "product.asset_class": "자산군",
    "product.asset_type": "자산유형",
    "product.investment_region": "투자지역",
    "product.region": "투자지역",
    "product.risk_grade": "위험등급",
    "product.sale_status": "판매상태",
    "product.public_private": "공모·사모",
    "product.pension_trade_eligible": "연금거래 가능 여부",
    "product.pension_eligible": "연금거래 가능 여부",
    "product.benchmark": "기초지수·벤치마크",
    "product.strategy": "운용전략",
}

_METRIC_LABELS = {
    "bond.issue_amount": "발행잔액",
    "bond.maturity_date": "만기일",
    "bond.coupon_rate": "표면금리",
    "bond.buy_yield": "매수수익률",
    "bond.corporate_pretax_yield": "법인세전수익률",
    "bond.corporate_after_tax_yield": "법인세후수익률",
    "bond.after_tax_yield": "세후수익률",
    "bond.preferential_tax_yield": "세금우대수익률",
    "bond.avg_annual_tax_yield": "연평균 세후수익률",
    "bond.deposit_equivalent_yield_154": "예금환산수익률",
    "bond.duration": "듀레이션",
    "bond.applied_yield": "적용수익률",
    "domestic_etp.return_1y": "1년 수익률",
    "domestic_etp.expense_ratio": "총보수",
    "domestic_etp.net_assets": "순자산총액",
    "domestic_etp.aum_last": "순자산총액",
    "domestic_etp.nav_last": "기준가(NAV)",
    "domestic_etp.volume_1d": "1일 거래량",
    "overseas_etp.expense_ratio": "총보수",
    "overseas_etp.aum_last": "AUM",
    "overseas_etp.nav_last": "기준가(NAV)",
    "overseas_etp.close_price": "종가",
    "overseas_etp.volume_1d": "1일 거래량",
    "fund.return_1y": "1년 수익률",
    "fund.return_6m": "6개월 수익률",
    "fund.return_3m": "3개월 수익률",
    "fund.return_1m": "1개월 수익률",
    "fund.return_3y": "3년 수익률",
    "fund.net_assets": "순자산총액",
    "fund.risk_grade": "위험등급",
}

_DISPLAY_LABELS = {**_CATALOG_LABELS, **_METRIC_LABELS}
_INTERNAL_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:bond|domestic_etp|overseas_etp|fund|product|cross)"
    r"\.[a-z0-9_]+(?![A-Za-z0-9_])"
)
_INTERNAL_SOURCE_RE = re.compile(r"\b(?:PREF|PRBD|PRFD)\d{2}N\d{3}\b")
_INTERNAL_UNIT_LABELS = {
    "PERCENTAGE_POINT_PENDING": "[원문 단위 미확정]",
    "PERCENTAGE_PENDING": "[원문 단위 미확정]",
    "KRW_PENDING": "[원문 단위 미확정]",
    "KRW_PRICE_PENDING": "[원문 단위 미확정]",
    "PRODUCT_CURRENCY_PENDING": "[원문 단위 미확정]",
    "CURRENCY_CONTEXT_REQUIRED": "[원문 단위 미확정]",
    "YEARS_PENDING": "[원문 단위 미확정]",
}


def _label(metric_id: str) -> str:
    """Return a Korean display label without leaking a physical field name."""

    return _DISPLAY_LABELS.get(metric_id, "요청 지표")


def _humanize_text(text: str) -> str:
    """Keep execution warnings useful while removing implementation identifiers."""

    rendered = _INTERNAL_ID_RE.sub(lambda match: _label(match.group(0)), text)
    rendered = re.sub(r"([A-Za-z]+)\(\1\)", r"\1", rendered)
    rendered = _INTERNAL_SOURCE_RE.sub("제공 데이터", rendered)
    return rendered.replace("serving 기준", "제공 데이터 기준").replace(
        "원본 상품 필드", "상품 정보"
    )


def _display_unit(unit: str | None) -> str | None:
    if not unit or unit in {"CODE", "RATING", "TEXT", "RISK_SCALE", "YYYYMMDD", "UNAVAILABLE"}:
        return None
    return _INTERNAL_UNIT_LABELS.get(unit, unit)


def _catalog_value(metric_id: str, value: object) -> str:
    text = _format_value(value)
    if metric_id == "product.currency":
        return {"CURR_CD_KRW": "KRW", "CURR_CD_USD": "USD"}.get(text, text)
    return text


def _format_value(value: object, *, numeric: bool = False) -> str:
    if value is None:
        return "확인 불가"
    text = str(value)
    if not numeric:
        return text
    try:
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        integer = int(text)
        return f"{integer:,}"
    except (ValueError, TypeError):
        return text


def _bounded_answer(text: str) -> str:
    """Bound prose while retaining complete lines whenever possible."""

    if len(text) <= MAX_ANSWER_CHARS:
        return text
    budget = MAX_ANSWER_CHARS - len(_ANSWER_OMISSION)
    boundary = text.rfind("\n", 0, budget)
    if boundary < budget // 2:
        boundary = budget
    return text[:boundary].rstrip() + _ANSWER_OMISSION


def _metric_value(item: object, metric_id: str) -> tuple[str, str | None, str | None]:
    for field in item.fields:
        if field.metric_id == metric_id:
            raw_unit = field.unit
            unit = raw_unit
            monetary_context = bool(
                unit
                and (
                    any(marker in unit for marker in ("CURRENCY", "KRW", "PRICE"))
                    or metric_id.endswith((".issue_amount", ".net_assets", ".aum_last"))
                )
            )
            if monetary_context:
                uses_trading_currency = "TRADING" in unit or "CONTEXT" in unit
                preferred = (
                    ["product.trading_currency", "product.currency"]
                    if uses_trading_currency
                    else ["product.currency", "product.trading_currency"]
                )
                for currency_metric in preferred:
                    currency = next(
                        (
                            candidate.normalized_value
                            for candidate in item.fields
                            if candidate.metric_id == currency_metric
                            and candidate.normalized_value
                        ),
                        None,
                    )
                    if currency:
                        if "PENDING" in unit or "CONTEXT_REQUIRED" in unit:
                            context_label = "거래통화" if uses_trading_currency else "상품통화"
                            unit = f"[지표 단위 미확정; {context_label} {currency}]"
                        else:
                            unit = str(currency)
                        break
            text_units = {"CODE", "RATING", "TEXT", "RISK_SCALE", "YYYYMMDD", "UNAVAILABLE"}
            unusable = any(
                flag in {"SENTINEL", "UNAVAILABLE", "UNUSABLE_CONSTANT"}
                or flag.startswith("MISSING")
                for flag in field.quality_flags
            )
            return (
                "확인 불가"
                if unusable
                else _format_value(field.normalized_value, numeric=raw_unit not in text_units),
                _display_unit(unit),
                field.as_of_date,
            )
    return "확인 불가", None, None


def _aggregate_group_label(plan: QueryPlan, group_key: object) -> str:
    key = str(group_key)
    if plan.group_by != ["product.scope"] or key not in _SCOPE_LABELS:
        return _label(key)
    scope_group = next(
        (
            group
            for group in plan.filter_groups
            if any(
                condition.field == "product.scope"
                and condition.op == "eq"
                and condition.value == key
                for condition in group.conditions
            )
        ),
        None,
    )
    conditions = scope_group.conditions if scope_group else []
    subtype = next(
        (
            str(condition.value)
            for condition in conditions
            if condition.field == "product.internal_type" and condition.op == "eq"
        ),
        None,
    )
    public_private = next(
        (
            str(condition.value)
            for condition in conditions
            if condition.field == "product.public_private" and condition.op == "eq"
        ),
        None,
    )
    if key == "domestic_etp" and subtype:
        return f"국내 {subtype}"
    if key == "overseas_etp" and subtype:
        return f"해외 {subtype}"
    if key == "fund" and public_private:
        return f"{public_private}펀드"
    return _SCOPE_LABELS[key]


def _catalog_details(item: object, plan: QueryPlan) -> list[str]:
    details: list[str] = []
    internal_type = next(
        (
            str(field.normalized_value)
            for field in item.fields
            if field.metric_id == "product.internal_type" and field.normalized_value is not None
        ),
        None,
    )
    for field in item.fields:
        if field.metric_id in _CATALOG_LABELS:
            label = _CATALOG_LABELS[field.metric_id]
            if field.metric_id == "product.manager" and internal_type == "ETN":
                label = "발행사"
            details.append(f"{label}: {_catalog_value(field.metric_id, field.normalized_value)}")
        elif field.metric_id in plan.metrics:
            value, actual_unit, as_of_date = _metric_value(item, field.metric_id)
            unit = f" {actual_unit}" if actual_unit else ""
            date = f" (기준일 {as_of_date})" if as_of_date else ""
            details.append(f"{_label(field.metric_id)}: {value}{unit}{date}")
    return details


def render_answer(plan: QueryPlan, evidence: EvidenceBundle) -> str:
    """Render only supplied official-data evidence as readable Korean prose."""

    if evidence.answerability == Answerability.NEEDS_CLARIFICATION and evidence.clarification:
        options = " / ".join(option.label for option in evidence.clarification.options)
        suffix = f" 선택지: {options}." if options else ""
        return _bounded_answer(_humanize_text(evidence.clarification.question + suffix))

    if evidence.answerability in {
        Answerability.UNAVAILABLE,
        Answerability.INCOMPARABLE,
        Answerability.SAFETY_LIMITED,
        Answerability.DATA_QUALITY_BLOCKED,
    }:
        if not evidence.limitations:
            return "요청을 안전하게 처리할 수 없습니다."
        return _bounded_answer("\n".join(dict.fromkeys(_humanize_text(line) for line in evidence.limitations)))

    if evidence.answerability == Answerability.NO_RESULT:
        return "요청 조건에 맞는 상품을 확인할 수 없습니다. 조건을 넓히거나 상품명·코드를 확인해 주세요."

    lines: list[str] = []
    if evidence.aggregates:
        aggregation_by_alias = {item.alias: item.function for item in plan.aggregations}
        function_labels = {"count": "개수", "sum": "합계", "avg": "평균", "min": "최소", "max": "최대"}
        rendered = []
        for item in evidence.aggregates:
            alias = item.aggregate_id.split("-", 1)[0]
            function = aggregation_by_alias.get(alias, "count")
            group = f"{_aggregate_group_label(plan, item.group_key)}: " if item.group_key is not None else ""
            unit = "개" if function == "count" else (f" {item.unit}" if item.unit else "")
            source_date = f", 기준일 {item.as_of_date}" if item.as_of_date else ""
            rendered.append(
                f"{group}{function_labels[function]} {_format_value(item.value, numeric=True)}{unit}"
                f"(대상 {item.source_row_count:,}개{source_date})"
            )
        lines.append("집계 결과: " + ", ".join(rendered) + ".")
    elif evidence.items:
        display_items = evidence.items[:MAX_DISPLAY_ITEMS]
        omitted_items = len(evidence.items) > len(display_items)
        if plan.intent in {"lookup", "explain"}:
            for item in display_items:
                details = _catalog_details(item, plan)
                lines.append(item.name + (": " + ", ".join(details) if details else ""))
        elif plan.intent == "compare":
            for item in display_items:
                details = []
                for metric_id in plan.metrics:
                    value, unit, as_of_date = _metric_value(item, metric_id)
                    date = f" (기준일 {as_of_date})" if as_of_date else ""
                    details.append(f"{_label(metric_id)}: {value}{f' {unit}' if unit else ''}{date}")
                lines.append(f"{item.name}: " + ", ".join(details))
        else:
            for index, item in enumerate(display_items, start=1):
                prefix = f"{item.rank or index}위" if plan.intent == "rank" else f"{index}."
                if plan.metrics:
                    details = []
                    for metric_id in plan.metrics:
                        value, unit, as_of_date = _metric_value(item, metric_id)
                        suffix = f" {unit}" if unit else ""
                        date = f" (기준일 {as_of_date})" if as_of_date else ""
                        details.append(f"{_label(metric_id)}: {value}{suffix}{date}")
                    lines.append(f"{prefix} {item.name} — " + ", ".join(details))
                else:
                    details = [
                        f"{_CATALOG_LABELS[field.metric_id]}: "
                        f"{_catalog_value(field.metric_id, field.normalized_value)}"
                        for field in item.fields
                        if field.metric_id in _CATALOG_LABELS
                    ]
                    lines.append(f"{prefix} {item.name}" + (" — " + ", ".join(details) if details else ""))
        if omitted_items:
            lines.append(_ANSWER_OMISSION.lstrip())

    if evidence.coverage and evidence.coverage.denominator:
        lines.append(
            f"값 확인 범위: {evidence.coverage.numerator:,}/{evidence.coverage.denominator:,}개 상품."
        )
    lines.extend(_humanize_text(line) for line in evidence.limitations)
    lines.append("기준: 주최 측 제공 데이터 스냅샷 2026-07-11. 실시간 정보가 아닙니다.")
    return _bounded_answer("\n".join(lines))
