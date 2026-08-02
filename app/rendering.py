"""Evidence-only Korean response rendering."""

from __future__ import annotations

from app.domain.models import Answerability, EvidenceBundle, QueryPlan

MAX_ANSWER_CHARS = 30_000
_ANSWER_OMISSION = "\n응답 길이 제한으로 이후 항목을 생략했습니다. 상세 근거는 retrieved_context를 확인해 주세요."

_SCOPE_LABELS = {
    "bond": "국내채권",
    "domestic_etp": "국내 ETP",
    "overseas_etp": "해외 ETP",
    "fund": "펀드",
}

_CATALOG_LABELS = {
    "product.id": "상품코드",
    "product.internal_type": "상품유형",
    "product.market": "시장",
    "product.currency": "상품통화",
    "product.manager": "운용사",
    "product.manager_code": "운용사코드",
    "product.issuer": "발행기관",
    "product.country_code": "국가코드",
    "bond.kind": "채권종류",
    "fund.domestic_overseas_class": "국내·해외 구분",
    "product.asset_class": "자산군",
    "product.investment_region": "투자지역",
    "product.risk_grade": "위험등급",
    "product.sale_status": "판매상태",
    "product.public_private": "공모·사모",
    "product.pension_trade_eligible": "연금거래 가능",
    "product.benchmark": "기초지수·벤치마크",
    "product.strategy": "운용전략(원문)",
}


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
    """Keep deterministic prose inside the public OrganizerResponse contract.

    The executor already bounds row counts, but a schema-valid lookup can ask for
    many fields on every row.  Preserve whole rendered lines where possible and
    make any omission explicit instead of letting Pydantic turn the request into
    an HTTP 500.
    """

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
            unit = field.unit
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
                (
                    "확인 불가"
                    if unusable
                    else _format_value(field.normalized_value, numeric=unit not in text_units)
                ),
                unit,
                field.as_of_date,
            )
    return "확인 불가", None, None


def _aggregate_group_label(plan: QueryPlan, group_key: object) -> str:
    key = str(group_key)
    if plan.group_by != ["product.scope"] or key not in _SCOPE_LABELS:
        return key
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


def render_answer(plan: QueryPlan, evidence: EvidenceBundle) -> str:
    if evidence.answerability == Answerability.NEEDS_CLARIFICATION and evidence.clarification:
        options = " / ".join(option.label for option in evidence.clarification.options)
        suffix = f" 선택지: {options}." if options else ""
        return _bounded_answer(evidence.clarification.question + suffix)

    if evidence.answerability in {
        Answerability.UNAVAILABLE,
        Answerability.INCOMPARABLE,
        Answerability.SAFETY_LIMITED,
        Answerability.DATA_QUALITY_BLOCKED,
    }:
        if not evidence.limitations:
            return "요청을 안전하게 처리할 수 없습니다."
        return _bounded_answer("\n".join(dict.fromkeys(evidence.limitations)))

    if evidence.answerability == Answerability.NO_RESULT:
        return "요청 조건에 맞는 상품을 확인할 수 없습니다. 조건을 넓히거나 상품명·코드를 확인해 주세요."

    lines: list[str] = []
    if evidence.aggregates:
        aggregation_by_alias = {item.alias: item.function for item in plan.aggregations}
        labels = {
            "count": "개수",
            "sum": "합계",
            "avg": "평균",
            "min": "최소",
            "max": "최대",
        }
        rendered = []
        for item in evidence.aggregates:
            alias = item.aggregate_id.split("-", 1)[0]
            function = aggregation_by_alias.get(alias, "count")
            group = (
                f"{_aggregate_group_label(plan, item.group_key)}: "
                if item.group_key is not None
                else ""
            )
            unit = "개" if function == "count" else (f" {item.unit}" if item.unit else "")
            source_date = (
                f", 원천 기준일 {item.as_of_date}" if item.as_of_date else ""
            )
            rendered.append(
                f"{group}{labels[function]} {_format_value(item.value, numeric=True)}{unit}"
                f"(근거 {item.source_row_count:,}행{source_date})"
            )
        lines.append("집계 결과는 " + ", ".join(rendered) + "입니다.")
    elif evidence.items:
        if plan.intent in {"lookup", "explain"}:
            for item in evidence.items:
                details = []
                for field in item.fields:
                    if field.metric_id in {
                        "product.id",
                        "product.internal_type",
                        "product.market",
                        "product.currency",
                        "product.manager",
                        "product.manager_code",
                        "product.issuer",
                        "product.country_code",
                        "bond.kind",
                        "fund.domestic_overseas_class",
                        "product.asset_class",
                        "product.investment_region",
                        "product.risk_grade",
                        "product.sale_status",
                        "product.public_private",
                        "product.pension_trade_eligible",
                        "product.benchmark",
                        "product.strategy",
                    }:
                        label = _CATALOG_LABELS.get(field.metric_id, field.metric_id)
                        details.append(
                            f"{label}[{field.source_field}]="
                            f"{_format_value(field.normalized_value)}"
                        )
                    elif field.metric_id in plan.metrics:
                        value, actual_unit, as_of_date = _metric_value(item, field.metric_id)
                        unit = f" {actual_unit}" if actual_unit else ""
                        date = f" (원천 기준일 {as_of_date})" if as_of_date else ""
                        details.append(
                            f"{field.metric_id}={value}{unit}{date}"
                        )
                lines.append(
                    f"{item.name} ({item.product_uid})"
                    + (": " + ", ".join(details) if details else "")
                )
        elif plan.intent == "compare":
            for item in evidence.items:
                details = []
                for metric_id in plan.metrics:
                    value, unit, as_of_date = _metric_value(item, metric_id)
                    date = f" @ {as_of_date}" if as_of_date else ""
                    details.append(f"{metric_id}={value}{f' {unit}' if unit else ''}{date}")
                lines.append(f"{item.name} ({item.product_uid}): " + ", ".join(details))
        else:
            for index, item in enumerate(evidence.items, start=1):
                prefix = f"{item.rank or index}위" if plan.intent == "rank" else f"{index}."
                if plan.metrics:
                    details = []
                    for metric_id in plan.metrics:
                        value, unit, as_of_date = _metric_value(item, metric_id)
                        suffix = f" {unit}" if unit else ""
                        date = f" (원천 기준일 {as_of_date})" if as_of_date else ""
                        details.append(f"{metric_id}={value}{suffix}{date}")
                    lines.append(f"{prefix} {item.name} — " + ", ".join(details))
                else:
                    details = [
                        f"{field.source_field}={_format_value(field.normalized_value)}"
                        for field in item.fields
                        if field.metric_id
                        in {
                            "product.id",
                            "product.internal_type",
                            "product.market",
                            "product.asset_class",
                            "product.asset_type",
                            "product.investment_region",
                            "product.region",
                            "product.risk_grade",
                            "product.pension_trade_eligible",
                            "product.pension_eligible",
                            "product.sale_status",
                        }
                    ]
                    lines.append(
                        f"{prefix} {item.name}" + (" — " + ", ".join(details) if details else "")
                    )

    if evidence.coverage and evidence.coverage.denominator:
        lines.append(
            f"지표 보유 범위: {evidence.coverage.numerator:,}/{evidence.coverage.denominator:,}개"
            f"(serving 기준)."
        )
    lines.extend(evidence.limitations)
    lines.append("기준: 주최 측 제공 데이터 스냅샷 2026-07-11. 실시간 정보가 아닙니다.")
    return _bounded_answer("\n".join(lines))
