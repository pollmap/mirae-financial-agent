"""Fail-closed financial safety and data-integrity policies."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyDecision:
    blocked: bool
    reason_code: str | None = None
    answerability: str | None = None
    message: str | None = None


_HARD_ADVICE = re.compile(
    r"(무조건|반드시|확실(?:히|한)|손실\s*(?:이\s*)?없|전재산|보장|확정\s*추천|"
    r"유망(?:한|할)?|"
    r"(?:나에게|내게|나한테|저에게|제게|저한테|"
    r"(?:내|제)\s*(?:재무\s*)?(?:상황|성향|투자\s*성향|위험\s*성향|"
    r"목표|투자\s*목표|조건|포트폴리오)).{0,16}(?:맞는|적합|추천|골라)|"
    r"개인(?:화|\s*맞춤).{0,12}(?:추천|선정|골라)|"
    r"당장\s*사|사라고|사야(?:\s*해|\s*할|\s*하나|\s*돼)?|"
    r"사도\s*(?:돼|될|괜찮)|살까|사는\s*(?:게|것이)\s*(?:좋아|좋을)|"
    r"매수(?:해도\s*(?:돼|될|괜찮)|하면\s*(?:돼|될)|해야|할까|하라고|해\s*줘|추천))"
)
_SUBJECTIVE_BEST = re.compile(r"(?:가장\s*좋은|최고의)")
_OBJECTIVE_BEST = re.compile(
    r"(?:수익률|보수|AUM|NAV|순자산|표면금리|매수수익률|신용등급|위험등급|"
    r"종가|거래량|괴리율)(?:이|가|은|는)?\s*(?:가장\s*좋은|최고의)",
    re.IGNORECASE,
)
_GENERIC_SELECTION = re.compile(
    r"(?:추천(?!\s*(?:기준|방식|시스템|알고리즘|기능|문구))|"
    r"(?:골라|선정|제안)\s*(?:줘|주세요|해\s*줘|해줘))"
)
_OBJECTIVE_SCREENING = re.compile(
    r"(?:\d+\s*(?:일|주|개월|년)|수익률|보수|AUM|NAV|순자산|표면금리|매수수익률|"
    r"신용등급|위험등급|종가|거래량|괴리율|판매중|판매완료|공모|사모|연금|"
    r"거래통화|미국|주식형|장내|장외|높은|낮은|큰|작은|많은|적은)",
    re.IGNORECASE,
)
_FORECAST = re.compile(
    r"((?:20\d{2}년|올해\s*말|연말|내년|미래|앞으로|향후|장래).{0,30}"
    r"(?:오를|내릴|수익|상승|하락|예측|전망|예상|유망)|"
    r"(?:\d+\s*(?:일|주|개월|년)\s*(?:후|뒤)).{0,30}"
    r"(?:오를|내릴|수익|상승|하락|예측|전망|예상|유망)|"
    r"(?:전망|예상|예측).{0,20}(?:ETF|ETN|채권|펀드|수익|상승|하락))"
)
_INVENT_MISSING = re.compile(
    r"(?:결측(?:값)?|없는\s*값|제공되지\s*않은|빈\s*값|공란|N/?A|NULL).{0,24}"
    r"(?:0으로|0\s*처리|0으로\s*보고|추정|있는\s*것처럼|만들)",
    re.IGNORECASE,
)
_REALTIME = re.compile(
    r"(?:실시간|지금|현재|최신|오늘|금일|현재가)", re.IGNORECASE
)
_EXPLICIT_SNAPSHOT_BASIS = re.compile(
    r"(?:주최\s*측\s*)?제공(?:된)?\s*데이터(?:\s*스냅샷)?\s*(?:을|를)?\s*기준|"
    r"2026[-.]?07[-.]?11\s*(?:을|를)?\s*기준"
)
_INJECTION = re.compile(
    r"(ignore\s+(all\s+)?previous|system\s+prompt|developer\s+message|raw\s+sql|drop\s+table)",
    re.IGNORECASE,
)


def evaluate_question(question: str) -> SafetyDecision:
    normalized = " ".join(question.split())
    if _INJECTION.search(normalized):
        return SafetyDecision(
            blocked=True,
            reason_code="INSTRUCTION_INJECTION",
            answerability="SAFETY_LIMITED",
            message="외부 지시나 임의 SQL 실행 요청은 처리할 수 없습니다. 금융상품 조건을 자연어로 질문해 주세요.",
        )
    if _INVENT_MISSING.search(normalized):
        if "실시간" in normalized or "시세" in normalized:
            return SafetyDecision(
                True,
                "SNAPSHOT_NOT_REALTIME",
                "UNAVAILABLE",
                "제공된 데이터는 2026-07-11 스냅샷이며 없는 실시간 값을 생성할 수 없습니다.",
            )
        if "추정" in normalized:
            return SafetyDecision(
                True,
                "SOURCE_FIELD_ABSENT",
                "UNAVAILABLE",
                "원본에 없는 값을 임의로 추정할 수 없습니다.",
            )
        return SafetyDecision(
            True,
            "MISSING_IS_NOT_ZERO",
            "DATA_QUALITY_BLOCKED",
            "결측값은 0과 다르므로 0으로 바꾸어 순위를 계산할 수 없습니다.",
        )
    if _REALTIME.search(normalized) and not _EXPLICIT_SNAPSHOT_BASIS.search(normalized):
        return SafetyDecision(
            True,
            "SNAPSHOT_NOT_REALTIME",
            "UNAVAILABLE",
            "제공된 데이터는 2026-07-11 스냅샷이므로 실시간 시세나 실시간 순위를 확인할 수 없습니다.",
        )
    if _FORECAST.search(normalized) or _HARD_ADVICE.search(normalized):
        return SafetyDecision(
            True,
            "FORECAST_OR_DEFINITIVE_RECOMMENDATION",
            "SAFETY_LIMITED",
            "미래 수익을 보장·예측하거나 특정 상품 매수를 단정할 수 없습니다. 확인 가능한 조건별 상품 정보와 비교는 제공할 수 있습니다.",
        )
    return SafetyDecision(blocked=False)


def needs_selection_criteria(question: str) -> bool:
    """Generic pick/best request without an objective basis.

    The official task requires asking for missing conditions instead of
    refusing, so these flow to a criteria clarification rather than a
    safety block. Definitive/personal/forecast advice stays blocked in
    ``evaluate_question`` and never reaches this relaxed path.
    """

    normalized = " ".join(question.split())
    if _FORECAST.search(normalized) or _HARD_ADVICE.search(normalized):
        return False
    unsafe_selection = bool(_GENERIC_SELECTION.search(normalized)) and not bool(
        _OBJECTIVE_SCREENING.search(normalized)
    )
    subjective_best = bool(_SUBJECTIVE_BEST.search(normalized)) and not bool(
        _OBJECTIVE_BEST.search(normalized)
    )
    return unsafe_selection or subjective_best


def validate_rendered_answer(
    answer: str, evidence_names: set[str], evidence_values: set[str]
) -> None:
    """Reject obvious policy violations in the final deterministic answer.

    This is intentionally conservative.  It is a last line of defense and does
    not replace field-level rendering from evidence.
    """

    if _HARD_ADVICE.search(answer) or _FORECAST.search(answer):
        raise ValueError("rendered answer violates recommendation/forecast policy")
    for token in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", answer):
        compact = token.replace(",", "")
        if compact in {"2026", "07", "11"}:
            continue
        if compact not in evidence_values:
            raise ValueError(f"numeric claim is not grounded in evidence: {token}")


def validate_rendered_policy(answer: str) -> None:
    """Apply only semantic safety checks to deterministic rendered prose.

    Numeric grounding is guaranteed by the evidence-only renderer and is tested
    separately.  This production guard intentionally avoids interpreting harmless
    counts, dates, row numbers, or metric names as ungrounded numeric claims.
    """

    if _HARD_ADVICE.search(answer) or _FORECAST.search(answer):
        raise ValueError("rendered answer violates recommendation/forecast policy")
