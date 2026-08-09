from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from qa_chat.app import create_app
from qa_chat.config import QASettings
from qa_chat.engine_client import EngineClient
from qa_chat.release_gate import canonical_artifact_sha256

ORIGIN = "http://127.0.0.1:8090"


def settings(tmp_path: Path, **overrides: Any) -> QASettings:
    values: dict[str, Any] = {
        "database_path": tmp_path / "qa.sqlite3",
        "transcript_key": b"t" * 32,
        "auth_secret": b"a" * 32,
        "engine_base_url": "http://engine",
        "allowed_origins": (ORIGIN,),
        "require_origin": True,
        "pilot_chat_enabled": True,
        "allow_fixture_preview": True,
        "model_id": "HCX-TEST",
        "approved_model_id": "HCX-TEST",
        "hcx_base_url": "https://clovastudio.stream.ntruss.com",
        "approved_hcx_base_url": "https://clovastudio.stream.ntruss.com",
        "engine_git_sha": "c" * 40,
        "engine_image_digest": "sha256:" + "e" * 64,
        "data_hash": "sha256:" + "d" * 64,
        "per_tester_per_minute": 100,
        "per_tester_per_day": 100,
        "global_per_minute": 100,
        "global_per_day": 100,
        "pilot_total_limit": 1_000,
    }
    values.update(overrides)
    if "release_gate_file" not in overrides:
        gate = {
            "schema_version": "mirae.qa.release-gate.v1",
            "generated_at_utc": "2026-08-09T00:00:00+00:00",
            "engine_git_sha": values["engine_git_sha"],
            "engine_image_digest": values["engine_image_digest"],
            "data_hash": values["data_hash"],
            "hcx_model_id": values["model_id"],
            "hcx_base_url": values["hcx_base_url"],
            "planner_stage": "two",
            "gates": {
                "smoke_20": {
                    "status": "PASS",
                    "total": 20,
                    "passed": 20,
                    "failed": 0,
                    "suite_sha256": "a" * 64,
                    "report_sha256": "b" * 64,
                    "verified_at_utc": "2026-08-09T00:00:00+00:00",
                },
                "canary_100": {
                    "status": "PASS",
                    "total": 100,
                    "passed": 100,
                    "failed": 0,
                    "suite_sha256": "c" * 64,
                    "report_sha256": "d" * 64,
                    "verified_at_utc": "2026-08-09T00:00:00+00:00",
                },
            },
            "sanitization": {
                "contains_questions": False,
                "contains_prompts": False,
                "contains_answers": False,
                "contains_tokens": False,
                "contains_credentials": False,
            },
        }
        gate["artifact_sha256"] = canonical_artifact_sha256(gate)
        gate_path = tmp_path / "qa_release_gate.json"
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        values["release_gate_file"] = gate_path
    result = QASettings(**values)
    result.validate()
    return result


def product(
    uid: str = "KR_ETP:PREF01N001:KR7000000001",
    name: str = "테스트 ETF",
    code: str = "KR7000000001",
) -> dict[str, Any]:
    return {
        "product_uid": uid,
        "name": name,
        "rank": 1,
        "fields": [
            {
                "evidence_id": f"E-{code}",
                "metric_id": "product.id",
                "source_table_id": "PREF01N001",
                "source_file": "official.xlsx",
                "source_sheet": "Sheet1",
                "source_excel_row": 2,
                "source_field": "pd_itm_no",
                "raw_value": code,
                "normalized_value": code,
                "unit": None,
                "as_of_date": None,
                "as_of_status": "DATASET_SNAPSHOT_ONLY",
                "source_row_hash": "e" * 64,
                "quality_flags": [],
            }
        ],
    }


def context(
    *,
    answerability: str = "FULL",
    products: list[dict[str, Any]] | None = None,
    token: str | None = None,
    conditions: list[dict[str, Any]] | None = None,
    scope: str = "domestic_etp",
) -> dict[str, Any]:
    items = products if products is not None else [product()]
    result: dict[str, Any] = {
        "version": "1.1",
        "execution_id": "fixture",
        "data_snapshot_date": "2026-07-11",
        "answerability": answerability,
        "reason_code": None,
        "result_count": len(items),
        "universe": {
            "scope": scope,
            "raw_count": len(items),
            "serving_count": len(items),
            "eligible_count": len(items),
            "excluded_count": 0,
            "filter_summary": "fixture",
        },
        "items": items,
        "aggregates": [],
        "limitations": [],
        "condition_ledger": conditions or [],
        "retrieval_trace": [
            {
                "channel": "sql",
                "status": "validated",
                "reason": "authoritative evidence",
                "scope": scope,
                "candidate_count": len(items),
                "verified_count": len(items),
                "latency_ms": 1.0,
                "observed_at_utc": "2026-08-09T00:00:00+00:00",
                "data_hash": "d" * 64,
                "fallback_reason": None,
                "evidence_refs": [],
            }
        ],
    }
    if answerability == "NEEDS_CLARIFICATION":
        result["reason_code"] = "MISSING_SCOPE"
        result["result_count"] = 0
        result["items"] = []
        result["clarification"] = {
            "question": "어느 상품군인가요?",
            "missing_slots": ["scope"],
            "options": [
                {"value": "domestic_etp", "label": "국내 ETP"},
                {"value": "overseas_etp", "label": "해외 ETP"},
            ],
            "preserved_plan": {"hidden": "do-not-return"},
            "clarification_token": token,
        }
    return result


def engine_payload(
    question_id: str,
    question: str,
    evidence: dict[str, Any] | None = None,
    *,
    answer: str = "검증된 답변입니다.",
) -> dict[str, str]:
    return {
        "question_id": question_id,
        "question": question,
        "retrieved_context": json.dumps(evidence or context(), ensure_ascii=False),
        "think_trace": "status=verified; private_reasoning=not_included",
        "answer": answer,
    }


class FakeEngine:
    def __init__(
        self,
        runtime_settings: QASettings,
        responder: Callable[[httpx.Request, int], httpx.Response] | None = None,
        ready_payload: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[httpx.Request] = []
        self.responder = responder
        self.runtime_settings = runtime_settings
        self.ready_payload = ready_payload

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health/ready":
                payload = self.ready_payload or {
                    "status": "ready",
                    "data_snapshot_date": "2026-07-11",
                    "engine_git_sha": self.runtime_settings.engine_git_sha,
                    "engine_image_digest": self.runtime_settings.engine_image_digest,
                    "data_hash": self.runtime_settings.data_hash,
                    "model_id": self.runtime_settings.model_id,
                    "hcx_base_url": self.runtime_settings.hcx_base_url,
                    "planner_stage": "two",
                }
                return httpx.Response(
                    200,
                    json=payload,
                    headers={"content-type": "application/json"},
                )
            self.calls.append(request)
            if self.responder:
                return self.responder(request, len(self.calls))
            params = request.url.params
            return httpx.Response(
                200,
                json=engine_payload(params["question_id"], params["question"]),
                headers={"content-type": "application/json"},
            )

        return httpx.MockTransport(handle)


def build_app(
    tmp_path: Path,
    *,
    responder: Callable[[httpx.Request, int], httpx.Response] | None = None,
    ready_payload: dict[str, Any] | None = None,
    setting_overrides: dict[str, Any] | None = None,
):
    resolved = settings(tmp_path, **(setting_overrides or {}))
    fake = FakeEngine(resolved, responder, ready_payload)
    engine = EngineClient("http://engine", transport=fake.transport())
    app = create_app(resolved, engine_client=engine)
    return app, fake


async def redeem(client: httpx.AsyncClient, app: Any) -> tuple[str, dict[str, str]]:
    code = app.state.repository.create_invite(code="fixture-invite-code-123456789")
    response = await client.post(
        "/qa/api/v1/invites/redeem",
        json={"code": code, "consent": True, "consent_version": "v1"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"], response.json()["tester"]


def mutation_headers(csrf: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": csrf}
