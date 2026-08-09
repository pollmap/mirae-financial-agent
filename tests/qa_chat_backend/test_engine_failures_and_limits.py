from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from qa_chat.engine_client import EngineClient, EngineContractError
from tests.qa_chat_backend.helpers import (
    ORIGIN,
    build_app,
    context,
    engine_payload,
    mutation_headers,
    redeem,
)


@pytest.mark.parametrize(
    ("engine_response", "reason_code", "status"),
    [
        (httpx.Response(503, json={"error": "down"}), "ENGINE_UNAVAILABLE", "RETRYABLE_ERROR"),
        (httpx.Response(429, json={"error": "slow"}), "ENGINE_UNAVAILABLE", "RETRYABLE_ERROR"),
        (
            httpx.Response(400, json={"error": "invalid"}),
            "ENGINE_REJECTED_REQUEST",
            "UNAVAILABLE",
        ),
        (
            httpx.Response(200, json={"unexpected": "shape"}),
            "ENGINE_SCHEMA_DRIFT",
            "UNAVAILABLE",
        ),
        (
            httpx.Response(200, text="not-json", headers={"content-type": "application/json"}),
            "ENGINE_SCHEMA_DRIFT",
            "UNAVAILABLE",
        ),
    ],
)
def test_engine_failures_become_controlled_assistant_states(
    tmp_path: Path,
    engine_response: httpx.Response,
    reason_code: str,
    status: str,
) -> None:
    def responder(_: httpx.Request, __: int) -> httpx.Response:
        return engine_response

    async def scenario() -> None:
        app, _ = build_app(tmp_path, responder=responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=ORIGIN,
        ) as client:
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            response = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "failure-message-01",
                    "expected_session_version": 0,
                    "text": "엔진 오류 테스트",
                },
                headers=mutation_headers(csrf),
            )
            assert response.status_code == 200
            assert response.json()["assistant"]["reason_code"] == reason_code
            assert response.json()["assistant"]["status"] == status
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_public_view_fails_closed_when_token_reaches_nested_evidence(tmp_path: Path) -> None:
    token = "signed-engine-token-nested-leak"

    def responder(request: httpx.Request, _: int) -> httpx.Response:
        evidence = context(answerability="NEEDS_CLARIFICATION", token=token)
        evidence["limitations"] = [f"diagnostic={token}"]
        return httpx.Response(
            200,
            json=engine_payload(
                request.url.params["question_id"],
                request.url.params["question"],
                evidence,
                answer="추가 확인이 필요합니다.",
            ),
            headers={"content-type": "application/json"},
        )

    async def scenario() -> None:
        app, _ = build_app(tmp_path, responder=responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            response = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "nested-token-0001",
                    "expected_session_version": 0,
                    "text": "상품 범위를 확인해 줘",
                },
                headers=mutation_headers(csrf),
            )
            assert response.status_code == 200
            assert response.json()["assistant"]["status"] == "UNAVAILABLE"
            assert response.json()["assistant"]["reason_code"] == "ENGINE_SCHEMA_DRIFT"
            assert token not in response.text
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_full_is_rejected_when_material_condition_is_unresolved(tmp_path: Path) -> None:
    def responder(request: httpx.Request, _: int) -> httpx.Response:
        evidence = context(
            conditions=[
                {
                    "condition_id": "risk_grade",
                    "kind": "risk_grade",
                    "requested_text": "위험등급 1등급",
                    "status": "clarification_required",
                    "grounded_fields": [],
                    "note": "scope required",
                }
            ]
        )
        return httpx.Response(
            200,
            json=engine_payload(
                request.url.params["question_id"],
                request.url.params["question"],
                evidence,
                answer="10개입니다.",
            ),
            headers={"content-type": "application/json"},
        )

    async def scenario() -> None:
        app, _ = build_app(tmp_path, responder=responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            response = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "invalid-full-0001",
                    "expected_session_version": 0,
                    "text": "위험등급 1등급 공모펀드 개수",
                },
                headers=mutation_headers(csrf),
            )
            assert response.status_code == 200
            assert response.json()["assistant"]["status"] == "UNAVAILABLE"
            assert response.json()["assistant"]["reason_code"] == "ENGINE_SCHEMA_DRIFT"
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_runtime_identity_mismatch_blocks_status_and_turns(tmp_path: Path) -> None:
    ready_payload = {
        "status": "ready",
        "data_snapshot_date": "2026-07-11",
        "engine_git_sha": "c" * 40,
        "engine_image_digest": "sha256:" + "e" * 64,
        "data_hash": "sha256:" + "d" * 64,
        "model_id": "HCX-DIFFERENT",
        "hcx_base_url": "https://clovastudio.stream.ntruss.com",
        "planner_stage": "two",
    }

    async def scenario() -> None:
        app, fake = build_app(tmp_path, ready_payload=ready_payload)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            status = (await client.get("/qa/api/v1/status")).json()
            assert status["status"] == "DEGRADED"
            assert status["engine"]["reason"] == "ENGINE_RELEASE_IDENTITY_MISMATCH"
            assert status["engine"]["mismatch_fields"] == ["model_id"]
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            blocked = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "identity-block-01",
                    "expected_session_version": 0,
                    "text": "상품 수",
                },
                headers=mutation_headers(csrf),
            )
            assert blocked.status_code == 423
            assert fake.calls == []
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_engine_timeout_becomes_controlled_retryable_state(tmp_path: Path) -> None:
    def responder(request: httpx.Request, _: int) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout", request=request)

    async def scenario() -> None:
        app, fake = build_app(tmp_path, responder=responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=ORIGIN,
        ) as client:
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            response = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "timeout-message-01",
                    "expected_session_version": 0,
                    "text": "타임아웃 테스트",
                },
                headers=mutation_headers(csrf),
            )
            assert response.status_code == 200
            assert response.json()["assistant"]["status"] == "RETRYABLE_ERROR"
            assert response.json()["assistant"]["reason_code"] == "ENGINE_UNAVAILABLE"
            assert len(fake.calls) == 1
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_engine_protocol_error_becomes_controlled_retryable_state(tmp_path: Path) -> None:
    def responder(request: httpx.Request, _: int) -> httpx.Response:
        raise httpx.RemoteProtocolError("fixture protocol failure", request=request)

    async def scenario() -> None:
        app, _ = build_app(tmp_path, responder=responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=ORIGIN,
        ) as client:
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            response = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "protocol-message-01",
                    "expected_session_version": 0,
                    "text": "프로토콜 오류 테스트",
                },
                headers=mutation_headers(csrf),
            )
            assert response.status_code == 200
            assert response.json()["assistant"]["status"] == "RETRYABLE_ERROR"
            assert response.json()["assistant"]["reason_code"] == "ENGINE_UNAVAILABLE"
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_oversized_engine_answer_is_rejected_before_persistence(tmp_path: Path) -> None:
    def responder(request: httpx.Request, _: int) -> httpx.Response:
        params = request.url.params
        return httpx.Response(
            200,
            json=engine_payload(
                params["question_id"], params["question"], answer="가" * 30_001
            ),
            headers={"content-type": "application/json"},
        )

    async def scenario() -> None:
        app, fake = build_app(tmp_path, responder=responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=ORIGIN,
        ) as client:
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            body = {
                "client_message_id": "oversized-answer-01",
                "expected_session_version": 0,
                "text": "응답 길이 경계 테스트",
            }
            first = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json=body,
                headers=mutation_headers(csrf),
            )
            assert first.status_code == 200
            assert first.json()["assistant"]["status"] == "UNAVAILABLE"
            assert first.json()["assistant"]["reason_code"] == "ENGINE_SCHEMA_DRIFT"
            duplicate = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json=body,
                headers=mutation_headers(csrf),
            )
            assert duplicate.json() == first.json()
            assert len(fake.calls) == 1
            assert "가" * 100 not in first.text
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_strict_engine_client_rejects_extra_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        payload = engine_payload(params["question_id"], params["question"])
        payload["extra"] = "not allowed"
        return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    async def scenario() -> None:
        client = EngineClient("http://engine", transport=httpx.MockTransport(handler))
        with pytest.raises(EngineContractError):
            await client.answer(question_id="Q-1", question="질문")
        await client.close()

    asyncio.run(scenario())


def test_persistent_request_budget_returns_429(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, fake = build_app(
            tmp_path,
            setting_overrides={
                "per_tester_per_minute": 1,
                "per_tester_per_day": 10,
                "global_per_minute": 10,
                "global_per_day": 10,
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            first = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "budget-message-01",
                    "expected_session_version": 0,
                    "text": "첫 질문",
                },
                headers=mutation_headers(csrf),
            )
            assert first.status_code == 200
            second = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "budget-message-02",
                    "expected_session_version": 1,
                    "text": "둘째 질문",
                },
                headers=mutation_headers(csrf),
            )
            assert second.status_code == 429
            assert second.headers["retry-after"] == "60"
            assert len(fake.calls) == 1
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_session_and_message_storage_caps_return_429_without_extra_engine_call(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app, fake = build_app(
            tmp_path,
            setting_overrides={
                "max_sessions_per_tester": 1,
                "max_messages_per_session": 2,
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            created = await client.post(
                "/qa/api/v1/sessions",
                json={"mode": "free"},
                headers=mutation_headers(csrf),
            )
            sid = created.json()["session"]["id"]
            extra_session = await client.post(
                "/qa/api/v1/sessions",
                json={"mode": "free"},
                headers=mutation_headers(csrf),
            )
            assert extra_session.status_code == 429
            first = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "storage-cap-msg-01",
                    "expected_session_version": 0,
                    "text": "첫 질문",
                },
                headers=mutation_headers(csrf),
            )
            assert first.status_code == 200
            capped = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "storage-cap-msg-02",
                    "expected_session_version": 1,
                    "text": "두 번째 질문",
                },
                headers=mutation_headers(csrf),
            )
            assert capped.status_code == 429
            assert len(fake.calls) == 1
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_ciphertext_quota_caps_local_blocked_turn_without_hcx(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, fake = build_app(
            tmp_path,
            setting_overrides={
                "max_ciphertext_bytes_per_tester": 1_000,
                "max_ciphertext_bytes_total": 1_000,
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            blocked = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "cipher-cap-msg-01",
                    "expected_session_version": 0,
                    "text": "연락처는 010-1234-5678",
                },
                headers=mutation_headers(csrf),
            )
            assert blocked.status_code == 429
            assert fake.calls == []
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_pilot_total_survives_daily_usage_purge(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, fake = build_app(
            tmp_path,
            setting_overrides={
                "per_tester_per_minute": 10,
                "per_tester_per_day": 10,
                "global_per_minute": 10,
                "global_per_day": 10,
                "pilot_total_limit": 1,
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            first = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "pilot-total-message-01",
                    "expected_session_version": 0,
                    "text": "첫 파일럿 질문",
                },
                headers=mutation_headers(csrf),
            )
            assert first.status_code == 200
            with app.state.repository._lock, app.state.repository._connection:
                app.state.repository._connection.execute(
                    "UPDATE usage_ledger SET attempted_at=0"
                )
            app.state.repository.purge_expired()
            second = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "pilot-total-message-02",
                    "expected_session_version": 1,
                    "text": "둘째 파일럿 질문",
                },
                headers=mutation_headers(csrf),
            )
            assert second.status_code == 429
            assert len(fake.calls) == 1
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_status_and_release_gate_block_new_turns(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, fake = build_app(
            tmp_path,
            setting_overrides={
                "release_gate_file": None,
                "engine_git_sha": "unknown",
                "engine_image_digest": "unknown",
                "data_hash": "unknown",
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            status = await client.get("/qa/api/v1/status")
            assert status.status_code == 200
            assert status.json()["status"] == "DEGRADED"
            assert status.json()["ready"] is False
            csrf, _ = await redeem(client, app)
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            blocked = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "gate-message-0001",
                    "expected_session_version": 0,
                    "text": "질문",
                },
                headers=mutation_headers(csrf),
            )
            assert blocked.status_code == 423
            assert blocked.json()["error"] == "RELEASE_GATE_PENDING"
            assert fake.calls == []
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())
