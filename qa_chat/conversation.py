"""Deterministic multi-turn adapter and public evidence sanitizer."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from qa_chat.engine_client import EngineContractError
from qa_chat.models import EngineResponse


@dataclass(slots=True)
class PreparedTurn:
    question: str | None
    clarification_token: str | None = None
    clarification_response: str | None = None
    local_view: dict[str, Any] | None = None
    local_follow_up_text: str | None = None


_ORDINALS = {
    "첫": 0,
    "첫번": 0,
    "첫 번째": 0,
    "첫번째": 0,
    "1번": 0,
    "두 번째": 1,
    "두번째": 1,
    "2번": 1,
    "세 번째": 2,
    "세번째": 2,
    "3번": 2,
    "네 번째": 3,
    "네번째": 3,
    "4번": 3,
    "다섯 번째": 4,
    "다섯번째": 4,
    "5번": 4,
}
_REFERENCE_MARKERS = (
    "그중",
    "그 중",
    "그 상품",
    "이 상품",
    "해당 상품",
    "그럼",
    "그러면",
    "그리고",
    "대신",
    "말고",
    "아니",
)
_STRUCTURAL_FOLLOW_UP_TERMS = (
    "보수",
    "수익률",
    "위험등급",
    "기준일",
    "근거",
    "통화",
    "벤치마크",
    "운용사",
    "발행사",
    "순자산",
    "만기",
    "등급",
    "전략",
)
_EXPLICIT_NEW_UNIVERSE_TERMS = ("ETF", "ETP", "채권", "펀드", "상품군")
REFERENCE_OPTION_LIMIT = 12


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


class ConversationAdapter:
    def __init__(self, *, clarification_ttl_seconds: int, environment: dict[str, str]) -> None:
        self.clarification_ttl_seconds = clarification_ttl_seconds
        self.environment = environment

    def prepare(
        self,
        *,
        text: str,
        reply_to_message_id: str | None,
        clarification_option_value: str | None,
        state: dict[str, Any],
        now_epoch: float,
        assistant_id: str,
    ) -> PreparedTurn:
        pending = state.get("pending_clarification")
        if reply_to_message_id:
            if not isinstance(pending, dict) or pending.get("message_id") != reply_to_message_id:
                raise StaleClarification("clarification is no longer active")
            if pending.get("consumed"):
                raise StaleClarification("clarification was already used")
            if float(pending.get("expires_at_epoch", 0)) <= now_epoch:
                state["pending_clarification"] = None
                return PreparedTurn(
                    question=None,
                    local_view=self.error_view(
                        assistant_id=assistant_id,
                        content="추가 질문이 만료되었습니다. 원래 질문을 다시 입력해 주세요.",
                        reason_code="CLARIFICATION_EXPIRED",
                        retryable=False,
                    ),
                )
            response = clarification_option_value or text
            if clarification_option_value:
                allowed = set(pending.get("option_values") or [])
                if clarification_option_value not in allowed:
                    raise StaleClarification("clarification option is not active")
            pending["consumed"] = True
            if pending.get("kind") == "local_reference":
                products = [
                    item for item in pending.get("products") or [] if isinstance(item, dict)
                ]
                selected = self._resolve_product_selection(response, products)
                original_follow_up = str(pending.get("original_follow_up_text", "")).strip()
                if not isinstance(selected, dict):
                    state["pending_clarification"] = None
                    return PreparedTurn(
                        question=None,
                        local_view=self.local_clarification_view(
                            assistant_id=assistant_id,
                            question=(
                                "입력한 이름이나 코드로 상품을 하나로 확인하지 못했습니다. "
                                "아래 항목을 선택하거나 정확한 상품명·코드를 입력해 주세요."
                            ),
                            products=products,
                            now_epoch=now_epoch,
                        ),
                        local_follow_up_text=original_follow_up,
                    )
                state["pending_clarification"] = None
                if not original_follow_up:
                    raise StaleClarification("local clarification lost the follow-up intent")
                return PreparedTurn(
                    question=self._product_follow_up(selected, original_follow_up)
                )
            return PreparedTurn(
                question=text,
                clarification_token=str(pending["token"]),
                clarification_response=response,
            )

        # A message that is not explicitly tied to an active clarification is
        # a new turn. This is what prevents an old UI button from consuming the
        # newest engine token.
        state["pending_clarification"] = None
        last = state.get("last_completed")
        if not isinstance(last, dict) or not self._references_previous(text):
            state["active_conditions"] = []
            return PreparedTurn(question=text)

        self._apply_explicit_corrections(text, state)
        products = last.get("products") or []
        ordinal = self._ordinal_index(text)
        if ordinal is not None:
            if ordinal >= len(products):
                return PreparedTurn(
                    question=None,
                    local_view=self.local_clarification_view(
                        assistant_id=assistant_id,
                        question="어느 상품을 뜻하는지 상품명이나 코드를 지정해 주세요.",
                        products=products,
                        now_epoch=now_epoch,
                    ),
                )
            return PreparedTurn(question=self._product_follow_up(products[ordinal], text))

        if any(marker in text for marker in ("그 상품", "이 상품", "해당 상품")):
            if len(products) == 1:
                return PreparedTurn(question=self._product_follow_up(products[0], text))
            return PreparedTurn(
                question=None,
                local_view=self.local_clarification_view(
                    assistant_id=assistant_id,
                    question="어느 상품을 뜻하나요?",
                    products=products,
                    now_epoch=now_epoch,
                ),
            )

        if self._is_structural_follow_up(text):
            if len(products) == 1:
                return PreparedTurn(question=self._product_follow_up(products[0], text))
            conditions = self._grounded_condition_texts(state)
            if conditions:
                return PreparedTurn(question=f"{', '.join(conditions)[:800]} 조건에서 {text}")
            if products and len(products) <= REFERENCE_OPTION_LIMIT:
                identifiers = ", ".join(self._product_identifier(item) for item in products)
                candidate = f"직전 결과 상품 {identifiers}에 대해 {text}"
                if len(candidate) <= 1_900:
                    return PreparedTurn(question=candidate)
            return PreparedTurn(
                question=None,
                local_view=self.local_clarification_view(
                    assistant_id=assistant_id,
                    question="어느 상품에 대한 후속 질문인지 선택하거나 이름·코드를 입력해 주세요.",
                    products=products,
                    now_epoch=now_epoch,
                ),
                local_follow_up_text=text,
            )

        conditions = self._grounded_condition_texts(state)
        if conditions:
            bounded = ", ".join(dict.fromkeys(conditions))[:800]
            return PreparedTurn(question=f"{bounded} 조건에서 {text}")
        return PreparedTurn(question=text)

    @staticmethod
    def _references_previous(text: str) -> bool:
        return (
            any(marker in text for marker in _REFERENCE_MARKERS)
            or any(
                ordinal in text for ordinal in _ORDINALS
            )
            or ConversationAdapter._is_structural_follow_up(text)
        )

    @staticmethod
    def _is_structural_follow_up(text: str) -> bool:
        compact = text.strip()
        if not compact or len(compact) > 80:
            return False
        if not any(term in compact for term in _STRUCTURAL_FOLLOW_UP_TERMS):
            return False
        if any(term in compact.upper() for term in _EXPLICIT_NEW_UNIVERSE_TERMS):
            return False
        return (
            compact.endswith(("?", "？"))
            or "도 알려" in compact
            or "도 보여" in compact
            or compact.endswith(("은", "는", "은요", "는요"))
        )

    @staticmethod
    def _grounded_condition_texts(state: dict[str, Any]) -> list[str]:
        conditions = [
            str(item.get("requested_text", "")).strip()
            for item in state.get("active_conditions") or []
            if isinstance(item, dict)
            and item.get("status") == "grounded"
            and str(item.get("requested_text", "")).strip()
        ]
        return list(dict.fromkeys(conditions))

    @staticmethod
    def _ordinal_index(text: str) -> int | None:
        for phrase, index in sorted(_ORDINALS.items(), key=lambda item: len(item[0]), reverse=True):
            if phrase in text:
                return index
        match = re.search(r"(?:그\s*중\s*)?(\d{1,2})\s*번째", text)
        if match:
            return int(match.group(1)) - 1
        return None

    @staticmethod
    def _product_follow_up(product: dict[str, Any], text: str) -> str:
        return f"{ConversationAdapter._product_identifier(product)}에 대해 {text}"

    @staticmethod
    def _product_identifier(product: dict[str, Any]) -> str:
        name = str(product.get("name", ""))[:500]
        code = str(product.get("code", ""))[:100]
        scope = str(product.get("scope", ""))
        labels = {
            "bond": "국내채권 상품코드",
            "domestic_etp": "국내 ETP 종목코드",
            "overseas_etp": "해외 ETP 티커 또는 상품코드",
            "fund": "공모펀드 상품코드",
        }
        if code:
            return f"{labels.get(scope, '상품코드')} {code} ({name})"
        # The engine never receives the gateway-only product_uid. Exact names
        # remain a safe deterministic identifier when code evidence is absent.
        return f"정확한 상품명 {name}"

    @staticmethod
    def _resolve_product_selection(
        response: str, products: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        normalized = " ".join(response.casefold().split())
        exact = []
        for product in products:
            identities = {
                " ".join(str(product.get(key, "")).casefold().split())
                for key in ("product_uid", "code", "name")
                if str(product.get(key, "")).strip()
            }
            if normalized in identities:
                exact.append(product)
        if len(exact) == 1:
            return exact[0]
        partial = [
            product
            for product in products
            if normalized
            and normalized
            in " ".join(str(product.get("name", "")).casefold().split())
        ]
        return partial[0] if len(partial) == 1 else None

    @staticmethod
    def _apply_explicit_corrections(text: str, state: dict[str, Any]) -> None:
        if not any(marker in text for marker in ("말고", "대신", "아니", "정정")):
            state["pending_condition_changes"] = []
            return
        replacements: list[tuple[str, str]] = []
        scope_candidates = [
            (text.rfind("해외"), "해외 ETP"),
            (text.rfind("국내"), "국내 ETP"),
            (text.rfind("채권"), "국내채권"),
            (text.rfind("펀드"), "공모펀드"),
        ]
        scope_position, scope_label = max(scope_candidates)
        if scope_position >= 0:
            replacements.append(("scope", scope_label))
        total_fee_position = text.rfind("총보수")
        fee_position = text.rfind("보수")
        if total_fee_position >= 0 and fee_position == total_fee_position + 1:
            fee_position = -1
        metric_candidates = [
            (total_fee_position, "총보수"),
            (fee_position, "보수"),
            (text.rfind("수익률"), "수익률"),
            (text.rfind("위험등급"), "위험등급"),
            (text.rfind("순자산"), "순자산"),
        ]
        metric_position, metric_label = max(metric_candidates)
        if metric_position >= 0:
            replacements.append(("metric", metric_label))
        active = [item for item in state.get("active_conditions") or [] if isinstance(item, dict)]
        changes = []
        for kind, new_text in replacements:
            removed = [item for item in active if item.get("kind") == kind]
            active = [item for item in active if item.get("kind") != kind]
            changes.append(
                {
                    "kind": kind,
                    "previous": [str(item.get("requested_text", "")) for item in removed],
                    "current": new_text,
                    "reason": "explicit_user_correction",
                }
            )
        state["active_conditions"] = active
        state["pending_condition_changes"] = changes

    def local_clarification_view(
        self,
        *,
        assistant_id: str,
        question: str,
        products: list[dict[str, Any]],
        now_epoch: float,
    ) -> dict[str, Any]:
        options = []
        for item in products[:REFERENCE_OPTION_LIMIT]:
            uid = str(item.get("product_uid", ""))[:300]
            name = str(item.get("name", uid))[:300]
            if uid:
                options.append({"value": uid, "label": name, "description": uid})
        if len(products) > REFERENCE_OPTION_LIMIT:
            question = (
                f"{question} 결과 {len(products)}개 중 화면에는 최대 "
                f"{REFERENCE_OPTION_LIMIT}개만 표시하므로, 나머지는 정확한 이름·코드로 입력해 주세요."
            )
        expires = now_epoch + self.clarification_ttl_seconds
        return {
            "id": assistant_id,
            "status": "NEEDS_CLARIFICATION",
            "content": question,
            "answerability": "NEEDS_CLARIFICATION",
            "reason_code": "AMBIGUOUS_CONVERSATION_REFERENCE",
            "clarification": {
                "id": assistant_id,
                "question": question,
                "options": options,
                "expires_at": _iso(expires),
            },
            "evidence": self.empty_evidence(),
            "environment": self.environment,
        }

    def error_view(
        self,
        *,
        assistant_id: str,
        content: str,
        reason_code: str,
        retryable: bool,
    ) -> dict[str, Any]:
        return {
            "id": assistant_id,
            "status": "RETRYABLE_ERROR" if retryable else "UNAVAILABLE",
            "content": content,
            "answerability": "UNAVAILABLE",
            "reason_code": reason_code,
            "clarification": None,
            "evidence": self.empty_evidence(),
            "environment": self.environment,
        }

    def blocked_view(self, *, assistant_id: str, reason_code: str, content: str) -> dict[str, Any]:
        return {
            "id": assistant_id,
            "status": "SAFE_LIMITED",
            "content": content,
            "answerability": "SAFETY_LIMITED",
            "reason_code": reason_code,
            "clarification": None,
            "evidence": self.empty_evidence(),
            "environment": self.environment,
        }

    def from_engine(
        self,
        *,
        response: EngineResponse,
        context: dict[str, Any],
        assistant_id: str,
        state: dict[str, Any],
        now_epoch: float,
        redaction_tokens: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        answerability = str(context.get("answerability", "UNAVAILABLE"))[:100]
        reason_code = context.get("reason_code")
        if reason_code is not None:
            reason_code = str(reason_code)[:100]
        status = self._public_status(answerability)
        evidence = self._sanitize_evidence(context)
        if answerability == "FULL" and any(
            item.get("status")
            in {"clarification_required", "unavailable", "not_comparable"}
            for item in evidence["condition_ledger"]
        ):
            raise EngineContractError(
                "FULL response contains an unresolved material condition"
            )
        clarification_view = None
        raw_clarification = context.get("clarification")
        secrets = tuple(token for token in redaction_tokens if token)
        if answerability == "NEEDS_CLARIFICATION":
            if not isinstance(raw_clarification, dict):
                raise EngineContractError("clarification evidence is missing")
            token = raw_clarification.get("clarification_token")
            if not isinstance(token, str) or not token:
                raise EngineContractError("stateful clarification token is missing")
            question = str(raw_clarification.get("question", "")).strip()
            if not question:
                raise EngineContractError("clarification question is missing")
            raw_options = raw_clarification.get("options")
            if not isinstance(raw_options, list):
                raise EngineContractError("clarification options are invalid")
            options = []
            for raw in raw_options[:12]:
                if not isinstance(raw, dict):
                    continue
                value = raw.get("value")
                label = raw.get("label")
                if isinstance(value, str) and value and isinstance(label, str) and label:
                    option = {"value": value[:300], "label": label[:300]}
                    description = raw.get("description")
                    if isinstance(description, str) and description:
                        option["description"] = description[:500]
                    options.append(option)
            expires = now_epoch + self.clarification_ttl_seconds
            clarification_view = {
                "id": assistant_id,
                "question": question[:500],
                "options": options,
                "expires_at": _iso(expires),
            }
            state["pending_clarification"] = {
                "kind": "engine",
                "message_id": assistant_id,
                "token": token,
                "expires_at_epoch": expires,
                "consumed": False,
                "option_values": [option["value"] for option in options],
                "original_question": response.question[:2_000],
            }
            secrets = (*secrets, token)
            public_content = self._redact_text(response.answer, secrets)
        else:
            state["pending_clarification"] = None
            state["last_completed"] = {
                "products": [
                    self._state_product(item, evidence.get("scope"))
                    for item in evidence["items"]
                ],
                "snapshot_date": evidence["snapshot_date"],
            }
            state["active_conditions"] = evidence["condition_ledger"]
            public_content = self._redact_text(response.answer, secrets)
        evidence["condition_changes"] = state.pop("pending_condition_changes", [])
        state["turn_count"] = int(state.get("turn_count", 0)) + 1
        view = {
            "id": assistant_id,
            "status": status,
            "content": public_content,
            "answerability": answerability,
            "reason_code": reason_code,
            "clarification": clarification_view,
            "evidence": evidence,
            "environment": self.environment,
        }
        # Answer text can be safely redacted, but a token appearing in evidence,
        # labels, or option values indicates contract drift.  Returning a
        # partially rewritten option could make the next turn consume the wrong
        # engine state, so fail closed instead.
        public_without_content = {key: value for key, value in view.items() if key != "content"}
        if self._contains_secret(public_without_content, secrets):
            raise EngineContractError("clarification token reached the public response")
        return view, state

    @staticmethod
    def _redact_text(value: str, secrets: tuple[str, ...]) -> str:
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[redacted]")
        return redacted

    @classmethod
    def _contains_secret(cls, value: Any, secrets: tuple[str, ...]) -> bool:
        if not secrets:
            return False
        if isinstance(value, str):
            return any(secret in value for secret in secrets)
        if isinstance(value, dict):
            return any(
                cls._contains_secret(key, secrets) or cls._contains_secret(item, secrets)
                for key, item in value.items()
            )
        if isinstance(value, list | tuple):
            return any(cls._contains_secret(item, secrets) for item in value)
        return False

    @staticmethod
    def _public_status(answerability: str) -> str:
        if answerability == "FULL":
            return "FULL"
        if answerability == "NEEDS_CLARIFICATION":
            return "NEEDS_CLARIFICATION"
        if answerability in {
            "PARTIAL_WITH_COVERAGE",
            "INCOMPARABLE",
            "SAFETY_LIMITED",
            "DATA_QUALITY_BLOCKED",
        }:
            return "SAFE_LIMITED"
        return "UNAVAILABLE"

    @staticmethod
    def empty_evidence() -> dict[str, Any]:
        return {
            "snapshot_date": None,
            "result_count": 0,
            "items": [],
            "aggregates": [],
            "limitations": [],
            "condition_ledger": [],
            "retrieval_channels": [],
            "condition_changes": [],
        }

    def _sanitize_evidence(self, context: dict[str, Any]) -> dict[str, Any]:
        items = []
        for raw_item in self._list(context.get("items"), 50):
            if not isinstance(raw_item, dict):
                continue
            fields = []
            for raw_field in self._list(raw_item.get("fields"), 100):
                if not isinstance(raw_field, dict):
                    continue
                fields.append(
                    self._pick(
                        raw_field,
                        {
                            "evidence_id",
                            "metric_id",
                            "source_table_id",
                            "source_file",
                            "source_sheet",
                            "source_excel_row",
                            "source_field",
                            "raw_value",
                            "normalized_value",
                            "unit",
                            "as_of_date",
                            "as_of_status",
                            "source_row_hash",
                            "quality_flags",
                        },
                    )
                )
            items.append(
                {
                    "product_uid": str(raw_item.get("product_uid", ""))[:300],
                    "name": str(raw_item.get("name", ""))[:500],
                    "rank": raw_item.get("rank") if isinstance(raw_item.get("rank"), int) else None,
                    "fields": fields,
                }
            )
        aggregates = [
            self._pick(
                raw,
                {
                    "aggregate_id",
                    "group_key",
                    "value",
                    "unit",
                    "source_table_ids",
                    "source_fields",
                    "source_row_count",
                    "query_hash",
                    "as_of_date",
                },
            )
            for raw in self._list(context.get("aggregates"), 100)
            if isinstance(raw, dict)
        ]
        ledger = [
            self._pick(
                raw,
                {
                    "condition_id",
                    "kind",
                    "requested_text",
                    "status",
                    "grounded_fields",
                    "note",
                },
            )
            for raw in self._list(context.get("condition_ledger"), 40)
            if isinstance(raw, dict)
        ]
        traces = [
            self._pick(
                raw,
                {
                    "channel",
                    "status",
                    "reason",
                    "scope",
                    "candidate_count",
                    "verified_count",
                    "latency_ms",
                    "observed_at_utc",
                    "data_hash",
                    "fallback_reason",
                    "evidence_refs",
                },
            )
            for raw in self._list(context.get("retrieval_trace"), 24)
            if isinstance(raw, dict)
        ]
        limitations = [
            str(value)[:1_000]
            for value in self._list(context.get("limitations"), 30)
            if isinstance(value, str)
        ]
        snapshot = context.get("data_snapshot_date")
        universe = context.get("universe")
        scope = universe.get("scope") if isinstance(universe, dict) else None
        return {
            "snapshot_date": str(snapshot)[:40] if snapshot is not None else None,
            "scope": str(scope)[:40] if scope is not None else None,
            "result_count": context.get("result_count")
            if isinstance(context.get("result_count"), int)
            else 0,
            "items": items,
            "aggregates": aggregates,
            "limitations": limitations,
            "condition_ledger": ledger,
            "retrieval_channels": traces,
            "condition_changes": [],
        }

    @staticmethod
    def _state_product(item: dict[str, Any], scope: Any) -> dict[str, Any]:
        code = ""
        for field in item.get("fields") or []:
            if field.get("metric_id") == "product.id":
                value = field.get("normalized_value")
                if value is None:
                    value = field.get("raw_value")
                if value is not None:
                    code = str(value).strip()[:100]
                    break
        return {
            "product_uid": item.get("product_uid"),
            "name": item.get("name"),
            "code": code,
            "scope": str(scope) if scope in {"bond", "domestic_etp", "overseas_etp", "fund"} else "",
        }

    @staticmethod
    def _list(value: Any, maximum: int) -> list[Any]:
        return value[:maximum] if isinstance(value, list) else []

    @staticmethod
    def _pick(source: dict[str, Any], keys: set[str]) -> dict[str, Any]:
        result = {}
        for key in keys:
            if key in source:
                value = copy.deepcopy(source[key])
                if isinstance(value, str):
                    value = value[:2_000]
                result[key] = value
        return result


class StaleClarification(RuntimeError):
    pass
