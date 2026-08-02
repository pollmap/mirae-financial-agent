from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.clarification import ClarificationCodec, ClarificationTokenError
from app.domain.models import QueryPlan


def test_rank_plan_requires_metric() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(intent="rank", scopes=["bond"])


def test_clarification_requires_missing_slot_and_question() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(intent="clarify", needs_clarification=True)


def test_clarification_intent_and_flag_cannot_disagree() -> None:
    with pytest.raises(ValueError):
        QueryPlan(
            intent="clarify",
            needs_clarification=False,
            clarification_question="어느 시장인가요?",
            missing_slots=["market"],
        )


def test_clarification_token_round_trip_preserves_question() -> None:
    def clock() -> float:
        return 1_700_000_000.0

    codec = ClarificationCodec("a-long-enough-development-signing-key", clock=clock)
    token = codec.encode(
        original_question="수익률 높은 ETF 3개",
        missing_slots=["market", "return_period"],
        preserved_plan={"limit": 3},
    )
    state = codec.decode(token)
    assert state.original_question == "수익률 높은 ETF 3개"
    assert state.missing_slots == ["market", "return_period"]
    assert state.preserved_plan == {"limit": 3}
    resolved_state, follow_up = codec.resolve_follow_up(token, "국내, 1년")
    assert resolved_state == state
    assert follow_up == "수익률 높은 ETF 3개\n추가 조건: 국내, 1년"
    assert codec.compose_follow_up(token, "국내, 1년") == (
        "수익률 높은 ETF 3개\n추가 조건: 국내, 1년"
    )


@pytest.mark.parametrize(
    ("missing_slot", "option_value", "answer_text"),
    [
        ("market", "domestic_etp", "국내"),
        ("scope", "overseas_etp", "해외 ETF·ETN"),
        ("return_period", "1y", "1년 수익률"),
        ("ranking_priority", "domestic_etp.expense_ratio", "보수 우선"),
        ("ranking_priority", "fund.return_1y", "1년 수익률 우선"),
        ("comparison_metric", "overseas_etp.close_price", "종가"),
    ],
)
def test_canonical_clarification_option_values_are_parser_readable(
    missing_slot: str, option_value: str, answer_text: str
) -> None:
    codec = ClarificationCodec("a-long-enough-development-signing-key")
    token = codec.encode(
        original_question="원래 질문",
        missing_slots=[missing_slot],
        preserved_plan={},
    )

    assert codec.compose_follow_up(token, option_value) == (
        f"원래 질문\n추가 조건: {answer_text}"
    )


def test_clarification_token_rejects_tampering_and_expiry() -> None:
    now = [1_700_000_000.0]
    codec = ClarificationCodec(
        "a-long-enough-development-signing-key", ttl_seconds=60, clock=lambda: now[0]
    )
    token = codec.encode(original_question="ETF?", missing_slots=["market"], preserved_plan={})
    with pytest.raises(ClarificationTokenError):
        codec.decode(token[:-1] + ("A" if token[-1] != "A" else "B"))
    now[0] += 61
    with pytest.raises(ClarificationTokenError, match="expired"):
        codec.decode(token)
