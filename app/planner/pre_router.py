"""Deterministic pre-router for exact-identifier lookup questions.

Before spending an HCX request, questions that contain exactly one product
identifier (ISIN, Korean ETP/fund code, context-confirmed ticker) and read as
a plain detail request are routed straight to a ``lookup`` :class:`QueryPlan`.
This trims TPM and latency for the most common evaluator query class.

The router is precision-first: whenever detection, question shape, or scope
inference is anything less than certain it returns ``None`` so the normal
planner (deterministic or HCX) makes the call.  It never touches the database
or the network.
"""

from __future__ import annotations

import re

from app.domain.models import Entity, QueryPlan

# Reused verbatim from app/planner/deterministic.py: `_CODES` (identifier
# alternatives) and `_infer_scopes` (question-text scope inference).
from app.planner.deterministic import _CODES, _infer_scopes

# One product is expected per exact-identifier lookup; keep the plan cheap.
_DEFAULT_LIMIT = 5

# Boundary convention adapted from `deterministic._CODES`: ASCII lookarounds
# instead of `\b`, because Hangul is a `\w` character and Korean particles
# attach directly to codes (e.g. "US0378331005의") which would defeat `\b`.
# Shape itself follows the strict ISIN layout: 2-letter prefix, 9 alphanumeric
# characters, 1 check digit.
_ISIN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}[A-Z0-9]{9}[0-9](?![A-Za-z0-9])")

# 6-digit KRX ticker.  `.`/`,` are excluded from the boundaries so decimals
# and grouped amounts (e.g. "1,234,567.890123") can never contribute a match.
# Only consulted when the question carries ticker context words.
_KRX_TICKER = re.compile(r"(?<![A-Za-z0-9.,])\d{6}(?![A-Za-z0-9.,])")

# Fund-code shape (letters + long digit run).  Only consulted with 코드 context.
_FUND_CODE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,3}\d{5,}(?![A-Za-z0-9.,])")

# Branch classifiers for `deterministic._CODES` matches.
_DOMESTIC_ETP_CODE = re.compile(r"[AQ]\d{6}")
_PREFIX_CODE = re.compile(r"(?:KR|XS|US)[A-Z0-9]{8,12}")

_TICKER_CONTEXT_WORDS = ("종목", "코드", "티커")

# The question must read as a plain detail request ...
_LOOKUP_HINT = re.compile(r"상세|정보|알려\s*[주줘]|뭐야|설명")
# ... and must not carry rank / aggregate / compare vocabulary.  Over-blocking
# is fine (the full planner runs); under-blocking is not.
_RANK_AGGREGATE = re.compile(r"높은|낮은|순|개수|몇\s*개|평균|비교|상위|하위|가장")


def _claim(found: list[tuple[int, int, str]], start: int, end: int, code: str) -> None:
    """Record a span unless an earlier (higher-priority) detector claimed it."""

    for other_start, other_end, _ in found:
        if start < other_end and other_start < end:
            return
    found.append((start, end, code))


def _extract_identifiers(question: str) -> list[str]:
    """Return unique identifier strings detected with high confidence."""

    found: list[tuple[int, int, str]] = []
    has_ticker_context = any(word in question for word in _TICKER_CONTEXT_WORDS)
    has_code_context = "코드" in question

    # 1) Strict ISINs are unambiguous on shape alone.
    for match in _ISIN.finditer(question):
        _claim(found, match.start(), match.end(), match.group(0))

    # 2) `deterministic._CODES` alternatives, gated per branch for precision.
    for match in _CODES.finditer(question):
        code = match.group(0)
        if _DOMESTIC_ETP_CODE.fullmatch(code):
            pass  # A/Q + 6 digits is a code by convention; no context needed.
        elif _PREFIX_CODE.fullmatch(code):
            # Non-ISIN-shaped KR/XS/US strings (ISIN-shaped ones were claimed
            # above) also match currency-amount runs such as "USD1000000",
            # so demand explicit 코드 context before trusting them.
            if not has_code_context:
                continue
        elif "." in code:
            # Dot tickers ("BRK.B") collide with abbreviations ("U.S.").
            if not has_ticker_context:
                continue
        # else: the `티커 <SYMBOL>` branch carries its own context.
        _claim(found, match.start(), match.end(), code)

    # 3) Bare 6-digit KRX tickers only alongside 종목/코드/티커 words, so
    #    years and amounts (e.g. "AUM 100000 이상") never qualify.
    if has_ticker_context:
        for match in _KRX_TICKER.finditer(question):
            _claim(found, match.start(), match.end(), match.group(0))

    # 4) Fund-code shapes only with explicit 코드 context.
    if has_code_context:
        for match in _FUND_CODE.finditer(question):
            _claim(found, match.start(), match.end(), match.group(0))

    ordered = [code for _, _, code in sorted(found)]
    return list(dict.fromkeys(ordered))


def _code_shape_scope(code: str) -> str | None:
    """Scope implied by the identifier shape alone (None when ambiguous)."""

    # Mirrors the code-shape rules inside `deterministic._infer_scopes`:
    # [AQ]\d{6} -> domestic_etp, XS -> bond, US -> overseas_etp.  A bare
    # 6-digit ticker is a KRX listing, hence domestic.  KR ISINs stay None:
    # they cover bonds, ETPs, and funds alike.
    if _DOMESTIC_ETP_CODE.fullmatch(code) or re.fullmatch(r"\d{6}", code):
        return "domestic_etp"
    if code.startswith("XS"):
        return "bond"
    if code.startswith("US"):
        return "overseas_etp"
    return None


def _resolve_scope(question: str, code: str) -> str | None:
    """Single confident scope from question text + code shape, else None."""

    text_scopes = _infer_scopes(question)
    shape_scope = _code_shape_scope(code)
    scopes = list(text_scopes) if text_scopes else ([shape_scope] if shape_scope else [])
    if len(scopes) != 1:
        return None
    if shape_scope is not None and shape_scope != scopes[0]:
        return None  # Text and code shape disagree; let the planner decide.
    return scopes[0]


def pre_route(question: str) -> QueryPlan | None:
    """Return a lookup ``QueryPlan`` for exact-identifier detail questions.

    Returns ``None`` whenever the question is not certainly such a lookup:
    zero or multiple identifiers, missing detail-request wording, any rank /
    aggregate / compare vocabulary, or an unresolvable scope.
    """

    if not question or not question.strip():
        return None
    identifiers = _extract_identifiers(question)
    if len(identifiers) != 1:
        return None  # Includes the two-code compare case.
    if not _LOOKUP_HINT.search(question):
        return None
    if _RANK_AGGREGATE.search(question):
        return None
    code = identifiers[0]
    scope = _resolve_scope(question, code)
    if scope is None:
        return None
    return QueryPlan(
        intent="lookup",
        scopes=[scope],
        entities=[Entity(code=code, name="", scope=scope)],
        limit=_DEFAULT_LIMIT,
    )
