"""Storage-boundary detection for common personal identifiers."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(
    r"(?<!\d)(?:(?:\+?82)[ -]?)?(?:0?1[016789]|0?2|0?[3-6]\d)"
    r"[ -]?\d{3,4}[ -]?\d{4}(?!\d)"
)
_RRN = re.compile(r"(?<!\d)\d{6}[ -]?[1-8]\d{6}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){15,19}(?!\d)")
_ACCOUNT = re.compile(r"(?<![A-Z0-9])(?:\d[ -]?){9,13}\d(?![A-Z0-9])")
_ISIN_CANDIDATE = re.compile(r"(?i)\b[A-Z]{2}[A-Z0-9]{9}\d\b")


def _valid_isin(value: str) -> bool:
    """Validate the ISO 6166/Luhn check digit before exempting a product code."""

    normalized = value.upper()
    expanded = "".join(
        str(ord(character) - ord("A") + 10) if character.isalpha() else character
        for character in normalized
    )
    total = 0
    for index, character in enumerate(reversed(expanded)):
        digit = int(character) * (2 if index % 2 else 1)
        total += digit // 10 + digit % 10
    return total % 10 == 0


def contains_sensitive_input(text: str) -> bool:
    """Block common PII while allowing official-looking product identifiers."""

    scrubbed = _ISIN_CANDIDATE.sub(
        lambda match: "" if _valid_isin(match.group(0)) else match.group(0),
        text,
    )
    return any(pattern.search(scrubbed) for pattern in (_EMAIL, _PHONE, _RRN, _CARD, _ACCOUNT))
