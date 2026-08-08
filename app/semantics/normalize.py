"""Mechanical party-name normalization shared by the ETL KG stage and runtime.

Never a semantic merge: two names join the same node only when identical
after whitespace/corporate-suffix/casefold normalization.
"""

from __future__ import annotations

import re

_CORPORATE_SUFFIXES = re.compile(
    r"(?:㈜|\(주\)|주식회사|\bco\.?,?\s*ltd\.?|\binc\.?|\bllc\.?|\bltd\.?)",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")


def normalize_party(name: str) -> str:
    text = _CORPORATE_SUFFIXES.sub(" ", name)
    return _WHITESPACE.sub(" ", text).strip().casefold()
