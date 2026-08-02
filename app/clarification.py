"""Tamper-evident, short-lived clarification state.

The public evaluation endpoint can return a concrete reverse-question without
requiring state.  This codec additionally supports a follow-up endpoint while
keeping the already-resolved part of the user's question intact.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_SCOPE_ANSWER_TEXT = {
    "bond": "국내채권",
    "domestic_etp": "국내 ETF·ETN",
    "overseas_etp": "해외 ETF·ETN",
    "fund": "공모펀드",
}
_MARKET_ANSWER_TEXT = {
    "domestic_etp": "국내",
    "overseas_etp": "해외",
}
_RETURN_PERIOD_ANSWER_TEXT = {
    "1d": "1일 수익률",
    "1w": "1주 수익률",
    "1m": "1개월 수익률",
    "3m": "3개월 수익률",
    "6m": "6개월 수익률",
    "ytd": "YTD 수익률",
    "18m": "18개월 수익률",
    "1y": "1년 수익률",
    "2y": "2년 수익률",
    "3y": "3년 수익률",
    "5y": "5년 수익률",
}
_METRIC_ANSWER_TEXT = {
    "bond.coupon_rate": "표면금리",
    "bond.buy_yield": "매수수익률",
    "bond.credit_rating": "신용등급",
    "domestic_etp.expense_ratio": "보수",
    "domestic_etp.aum_last": "AUM",
    "domestic_etp.net_assets": "순자산",
    "domestic_etp.nav_last": "NAV",
    "domestic_etp.close_price": "종가",
    "domestic_etp.volume_1d": "거래량",
    "overseas_etp.expense_ratio": "보수",
    "overseas_etp.aum_last": "AUM",
    "overseas_etp.close_price": "종가",
    "overseas_etp.volume_1d": "거래량",
    "fund.expense_ratio": "보수",
    "fund.net_assets": "순자산",
    "fund.risk_grade": "위험등급",
}


class ClarificationTokenError(ValueError):
    """A token is malformed, expired, or has been modified."""


@dataclass(frozen=True)
class ClarificationState:
    original_question: str
    missing_slots: list[str]
    preserved_plan: dict[str, Any]
    issued_at: int


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise ClarificationTokenError("invalid token encoding") from exc


class ClarificationCodec:
    def __init__(
        self,
        signing_key: str,
        *,
        ttl_seconds: int = 900,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(signing_key.encode("utf-8")) < 24:
            raise ValueError("clarification signing key must be at least 24 bytes")
        if not 60 <= ttl_seconds <= 3600:
            raise ValueError("clarification token TTL must be 60..3600 seconds")
        self._key = signing_key.encode("utf-8")
        self._ttl = ttl_seconds
        self._clock = clock

    def encode(
        self,
        *,
        original_question: str,
        missing_slots: list[str],
        preserved_plan: dict[str, Any],
    ) -> str:
        payload = {
            "v": 1,
            "q": original_question,
            "m": missing_slots,
            "p": preserved_plan,
            "iat": int(self._clock()),
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        encoded = _b64encode(raw)
        signature = _b64encode(
            hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{encoded}.{signature}"

    def decode(self, token: str) -> ClarificationState:
        try:
            encoded, signature = token.split(".", 1)
        except ValueError as exc:
            raise ClarificationTokenError("invalid token shape") from exc
        expected = _b64encode(hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ClarificationTokenError("invalid token signature")
        try:
            payload = json.loads(_b64decode(encoded))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ClarificationTokenError("invalid token payload") from exc
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ClarificationTokenError("unsupported token version")
        issued_at = payload.get("iat")
        if not isinstance(issued_at, int) or issued_at > int(self._clock()) + 30:
            raise ClarificationTokenError("invalid token timestamp")
        if int(self._clock()) - issued_at > self._ttl:
            raise ClarificationTokenError("clarification token expired")
        if not isinstance(payload.get("q"), str) or not isinstance(payload.get("m"), list):
            raise ClarificationTokenError("invalid clarification state")
        if not isinstance(payload.get("p"), dict):
            raise ClarificationTokenError("invalid preserved plan")
        return ClarificationState(
            original_question=payload["q"],
            missing_slots=[str(value) for value in payload["m"]],
            preserved_plan=payload["p"],
            issued_at=issued_at,
        )

    def resolve_follow_up(
        self, token: str, user_answer: str
    ) -> tuple[ClarificationState, str]:
        """Decode one signed state and compose the planner-facing follow-up.

        Returning the decoded state together with the composed question is
        intentional: callers must reconcile the newly generated plan with the
        signed ``preserved_plan`` before executing it.  Merely appending the
        answer to the original question would allow a later planner call to
        silently discard already-resolved constraints.
        """

        state = self.decode(token)
        answer = " ".join(user_answer.split())
        if not answer:
            raise ClarificationTokenError("clarification answer is empty")
        slots = set(state.missing_slots)
        if "market" in slots:
            answer = _MARKET_ANSWER_TEXT.get(answer, answer)
        elif "scope" in slots:
            answer = _SCOPE_ANSWER_TEXT.get(answer, answer)
        elif slots & {"return_period", "return_period_priority"}:
            answer = _RETURN_PERIOD_ANSWER_TEXT.get(answer, answer)
        elif "ranking_priority" in slots:
            label = _METRIC_ANSWER_TEXT.get(answer)
            if label is None and ".return_" in answer:
                period = answer.rsplit("return_", 1)[-1]
                period_text = _RETURN_PERIOD_ANSWER_TEXT.get(period)
                if period_text:
                    label = period_text
            if label:
                answer = f"{label} 우선"
        elif "comparison_metric" in slots:
            if answer.startswith(("domestic_etp.return_", "overseas_etp.return_", "fund.return_")):
                period = answer.rsplit("return_", 1)[-1]
                answer = _RETURN_PERIOD_ANSWER_TEXT.get(period, answer)
            else:
                answer = _METRIC_ANSWER_TEXT.get(answer, answer)
        return state, f"{state.original_question}\n추가 조건: {answer}"

    def compose_follow_up(self, token: str, user_answer: str) -> str:
        """Backward-compatible convenience wrapper for text-only callers."""

        _, question = self.resolve_follow_up(token, user_answer)
        return question
