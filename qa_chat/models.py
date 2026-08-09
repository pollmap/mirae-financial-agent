"""Strict browser and immutable engine-boundary models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RedeemInviteRequest(StrictModel):
    code: str = Field(min_length=16, max_length=256)
    consent: Literal[True]
    consent_version: Literal["v1"] = "v1"


class CreateSessionRequest(StrictModel):
    mode: Literal["guided", "free"] = "free"
    scenario_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")

    @model_validator(mode="after")
    def guided_requires_scenario(self) -> CreateSessionRequest:
        if self.mode == "guided" and not self.scenario_id:
            raise ValueError("guided sessions require scenario_id")
        if self.mode == "free" and self.scenario_id:
            raise ValueError("free sessions cannot set scenario_id")
        return self


class MessageRequest(StrictModel):
    client_message_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
    expected_session_version: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2_000)
    reply_to_message_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    clarification_option_value: str | None = Field(default=None, max_length=300)

    @field_validator("text")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message text cannot be blank")
        return value

    @model_validator(mode="after")
    def option_requires_reply(self) -> MessageRequest:
        if self.clarification_option_value and not self.reply_to_message_id:
            raise ValueError("clarification options require reply_to_message_id")
        return self


class FeedbackRequest(StrictModel):
    verdict: Literal["accurate", "partly_accurate", "incorrect", "uncertain"]
    tags: list[str] = Field(default_factory=list, max_length=9)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("feedback tags must be unique")
        return value


class EngineResponse(StrictModel):
    question_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2_000)
    retrieved_context: str = Field(min_length=2, max_length=500_000)
    think_trace: str = Field(max_length=100_000)
    answer: str = Field(max_length=30_000)


class ClarificationOptionView(StrictModel):
    value: str = Field(min_length=1, max_length=300)
    label: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=500)


class ClarificationView(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    question: str = Field(min_length=1, max_length=500)
    options: list[ClarificationOptionView] = Field(default_factory=list, max_length=12)
    expires_at: str


class AssistantView(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal[
        "FULL",
        "NEEDS_CLARIFICATION",
        "SAFE_LIMITED",
        "UNAVAILABLE",
        "RETRYABLE_ERROR",
    ]
    content: str = Field(max_length=30_000)
    answerability: str = Field(max_length=100)
    reason_code: str | None = Field(default=None, max_length=100)
    clarification: ClarificationView | None = None
    evidence: dict[str, Any]
    environment: dict[str, str]


class TurnResponse(StrictModel):
    session_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    session_version: int = Field(ge=1)
    turn_id: int = Field(ge=1)
    assistant: AssistantView
