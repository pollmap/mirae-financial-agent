from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from qa_chat.app import create_app
from qa_chat.models import EngineResponse, MessageRequest
from tests.qa_chat_backend.helpers import (
    ORIGIN,
    build_app,
    context,
    engine_payload,
    mutation_headers,
    redeem,
    settings,
)


def test_circuit_breaker_opens_without_a_fallback_provider(tmp_path: Path) -> None:
    def responder(_: httpx.Request, __: int) -> httpx.Response:
        return httpx.Response(503, json={"error": "fixture outage"})

    async def scenario() -> None:
        app, fake = build_app(
            tmp_path,
            responder=responder,
            setting_overrides={
                "circuit_failure_threshold": 2,
                "circuit_window_seconds": 120,
                "circuit_open_seconds": 60,
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
            responses = []
            for index in range(3):
                responses.append(
                    await client.post(
                        f"/qa/api/v1/sessions/{sid}/messages",
                        json={
                            "client_message_id": f"circuit-message-{index:02d}",
                            "expected_session_version": index,
                            "text": f"장애 확인 질문 {index}",
                        },
                        headers=mutation_headers(csrf),
                    )
                )
            assert [response.status_code for response in responses] == [200, 200, 200]
            assert responses[0].json()["assistant"]["reason_code"] == "ENGINE_UNAVAILABLE"
            assert responses[1].json()["assistant"]["reason_code"] == "ENGINE_UNAVAILABLE"
            assert responses[2].json()["assistant"]["reason_code"] == "HCX_CIRCUIT_OPEN"
            assert len(fake.calls) == 2
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_concurrent_identical_clicks_wait_for_and_return_one_result(tmp_path: Path) -> None:
    class SlowSuccessfulEngine:
        def __init__(self) -> None:
            self.calls = 0

        async def ready(self) -> dict[str, Any]:
            return {
                "status": "ready",
                "data_snapshot_date": "2026-07-11",
                "engine_git_sha": "c" * 40,
                "engine_image_digest": "sha256:" + "e" * 64,
                "data_hash": "sha256:" + "d" * 64,
                "model_id": "HCX-TEST",
                "hcx_base_url": "https://clovastudio.stream.ntruss.com",
                "planner_stage": "two",
            }

        async def answer(self, **request: Any) -> tuple[EngineResponse, dict[str, Any]]:
            self.calls += 1
            await asyncio.sleep(0.15)
            return (
                EngineResponse(
                    question_id=request["question_id"],
                    question=request["question"],
                    retrieved_context="{}",
                    think_trace="",
                    answer="동일 결과",
                ),
                context(),
            )

        async def close(self) -> None:
            return None

    async def scenario() -> None:
        engine = SlowSuccessfulEngine()
        app = create_app(settings(tmp_path), engine_client=engine)  # type: ignore[arg-type]
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
            body = {
                "client_message_id": "concurrent-same-01",
                "expected_session_version": 0,
                "text": "같은 질문",
            }
            first, duplicate = await asyncio.gather(
                *(
                    client.post(
                        f"/qa/api/v1/sessions/{sid}/messages",
                        json=body,
                        headers=mutation_headers(csrf),
                    )
                    for _ in range(2)
                )
            )
            assert first.status_code == duplicate.status_code == 200
            assert first.json() == duplicate.json()
            assert engine.calls == 1
        await engine.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_pending_version_blocks_new_id_and_expired_lease_recovers(tmp_path: Path) -> None:
    async def scenario() -> None:
        first_app, _ = build_app(tmp_path)
        first_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app), base_url=ORIGIN
        )
        csrf, tester = await redeem(first_client, first_app)
        sid = (
            await first_client.post(
                "/qa/api/v1/sessions",
                json={"mode": "free"},
                headers=mutation_headers(csrf),
            )
        ).json()["session"]["id"]
        original = MessageRequest(
            client_message_id="crashed-request-01",
            expected_session_version=0,
            text="중단된 요청",
        )
        canonical = json.dumps(
            original.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        request_hash = first_app.state.repository.hasher.digest(
            "message-request", canonical
        )
        first_app.state.repository.reserve_request(
            sid,
            tester["id"],
            original.client_message_id,
            request_hash,
            0,
            300.0,
        )

        second_app, second_fake = build_app(tmp_path)
        cookies = {
            "qa_session": first_client.cookies.get("qa_session"),
            "qa_csrf": csrf,
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app),
            base_url=ORIGIN,
            cookies=cookies,
        ) as second_client:
            bypass = await second_client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "different-id-0001",
                    "expected_session_version": 0,
                    "text": "중단된 요청",
                },
                headers=mutation_headers(csrf),
            )
            assert bypass.status_code == 409
            assert second_fake.calls == []
            with second_app.state.repository._lock, second_app.state.repository._connection:
                second_app.state.repository._connection.execute(
                    "UPDATE message_requests SET lease_expires_at=0 "
                    "WHERE session_id=? AND client_message_id=?",
                    (sid, original.client_message_id),
                )
            recovered = await second_client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json=original.model_dump(mode="json"),
                headers=mutation_headers(csrf),
            )
            assert recovered.status_code == 200
            assert len(second_fake.calls) == 1

        await first_client.aclose()
        await first_app.state.engine_client.close()
        first_app.state.repository.close()
        await second_app.state.engine_client.close()
        second_app.state.repository.close()

    asyncio.run(scenario())


def test_clarification_expiry_tamper_and_cross_session_use_are_blocked(
    tmp_path: Path,
) -> None:
    token = "encrypted-only-fixture-token"

    def responder(request: httpx.Request, _: int) -> httpx.Response:
        params = request.url.params
        return httpx.Response(
            200,
            json=engine_payload(
                params["question_id"],
                params["question"],
                context(answerability="NEEDS_CLARIFICATION", token=token),
            ),
            headers={"content-type": "application/json"},
        )

    async def scenario() -> None:
        app, fake = build_app(tmp_path, responder=responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            sessions = []
            for _ in range(2):
                sessions.append(
                    (
                        await client.post(
                            "/qa/api/v1/sessions",
                            json={"mode": "free"},
                            headers=mutation_headers(csrf),
                        )
                    ).json()["session"]["id"]
                )
            first = await client.post(
                f"/qa/api/v1/sessions/{sessions[0]}/messages",
                json={
                    "client_message_id": "guard-clarification-01",
                    "expected_session_version": 0,
                    "text": "상품 수를 알려줘",
                },
                headers=mutation_headers(csrf),
            )
            assistant_id = first.json()["assistant"]["id"]

            cross_session = await client.post(
                f"/qa/api/v1/sessions/{sessions[1]}/messages",
                json={
                    "client_message_id": "guard-clarification-02",
                    "expected_session_version": 0,
                    "text": "국내 ETP",
                    "reply_to_message_id": assistant_id,
                    "clarification_option_value": "domestic_etp",
                },
                headers=mutation_headers(csrf),
            )
            assert cross_session.status_code == 409

            tampered = await client.post(
                f"/qa/api/v1/sessions/{sessions[0]}/messages",
                json={
                    "client_message_id": "guard-clarification-03",
                    "expected_session_version": 1,
                    "text": "조작된 선택지",
                    "reply_to_message_id": assistant_id,
                    "clarification_option_value": "tampered_scope",
                },
                headers=mutation_headers(csrf),
            )
            assert tampered.status_code == 409

            # Loading state requires the anonymous tester id, not its auth token.
            tester = app.state.repository.authenticate(client.cookies.get("qa_session"))
            state = app.state.repository.load_state(sessions[0], tester["id"])
            state["pending_clarification"]["expires_at_epoch"] = 0
            envelope = app.state.repository.cipher.seal(
                state, aad=f"session-state:{sessions[0]}"
            )
            with app.state.repository._lock, app.state.repository._connection:
                app.state.repository._connection.execute(
                    "UPDATE encrypted_states SET state_cipher=? WHERE session_id=?",
                    (envelope, sessions[0]),
                )

            expired = await client.post(
                f"/qa/api/v1/sessions/{sessions[0]}/messages",
                json={
                    "client_message_id": "guard-clarification-04",
                    "expected_session_version": 1,
                    "text": "국내 ETP",
                    "reply_to_message_id": assistant_id,
                    "clarification_option_value": "domestic_etp",
                },
                headers=mutation_headers(csrf),
            )
            assert expired.status_code == 200
            assert expired.json()["assistant"]["reason_code"] == "CLARIFICATION_EXPIRED"
            assert len(fake.calls) == 1
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_retention_purge_removes_session_content_and_keeps_audit_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app, _ = build_app(tmp_path)
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
            await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "retention-message-01",
                    "expected_session_version": 0,
                    "text": "보존 기한 확인",
                },
                headers=mutation_headers(csrf),
            )
            with app.state.repository._lock, app.state.repository._connection:
                app.state.repository._connection.execute(
                    "UPDATE chat_sessions SET expires_at=0 WHERE id=?", (sid,)
                )
            assert app.state.repository.purge_expired() == 1
            assert (await client.get(f"/qa/api/v1/sessions/{sid}")).status_code == 404
            with app.state.repository._lock:
                message_count = app.state.repository._connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id=?", (sid,)
                ).fetchone()[0]
                audit_count = app.state.repository._connection.execute(
                    "SELECT COUNT(*) FROM deletion_audit WHERE reason='retention'"
                ).fetchone()[0]
            assert message_count == 0
            assert audit_count == 1
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_gateway_never_aliases_contest_engine_routes_to_the_spa(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, _ = build_app(tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            for path in ("/answer", "/demo", "/docs", "/redoc", "/openapi.json"):
                response = await client.get(path)
                assert response.status_code == 404
                assert response.json()["error"] == "NOT_FOUND"
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_total_deadline_includes_waiting_for_the_engine_semaphore(tmp_path: Path) -> None:
    class SlowEngine:
        def __init__(self) -> None:
            self.calls = 0

        async def ready(self) -> dict[str, str | bool]:
            return {
                "status": "ready",
                "data_snapshot_date": "2026-07-11",
                "engine_git_sha": "c" * 40,
                "engine_image_digest": "sha256:" + "e" * 64,
                "data_hash": "sha256:" + "d" * 64,
                "model_id": "HCX-TEST",
                "hcx_base_url": "https://clovastudio.stream.ntruss.com",
                "planner_stage": "two",
                "identity_verified": True,
            }

        async def answer(self, **_: Any) -> None:
            self.calls += 1
            await asyncio.sleep(5)

        async def close(self) -> None:
            return None

    async def scenario() -> None:
        slow = SlowEngine()
        app = create_app(
            settings(
                tmp_path,
                engine_timeout_seconds=0.05,
                engine_concurrency=1,
                global_per_minute=10,
            ),
            engine_client=slow,  # type: ignore[arg-type]
        )
        clients: list[httpx.AsyncClient] = []
        sessions: list[tuple[httpx.AsyncClient, str, str]] = []
        try:
            for index in range(2):
                client = httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url=ORIGIN
                )
                clients.append(client)
                code = app.state.repository.create_invite(
                    code=f"deadline-invite-{index}-123456789"
                )
                login = await client.post(
                    "/qa/api/v1/invites/redeem",
                    json={"code": code, "consent": True, "consent_version": "v1"},
                    headers={"Origin": ORIGIN},
                )
                csrf = login.json()["csrf_token"]
                sid = (
                    await client.post(
                        "/qa/api/v1/sessions",
                        json={"mode": "free"},
                        headers=mutation_headers(csrf),
                    )
                ).json()["session"]["id"]
                sessions.append((client, csrf, sid))

            started = time.perf_counter()
            responses = await asyncio.gather(
                *(
                    client.post(
                        f"/qa/api/v1/sessions/{sid}/messages",
                        json={
                            "client_message_id": f"deadline-message-{index:02d}",
                            "expected_session_version": 0,
                            "text": "전체 제한시간 확인",
                        },
                        headers=mutation_headers(csrf),
                    )
                    for index, (client, csrf, sid) in enumerate(sessions)
                )
            )
            elapsed = time.perf_counter() - started
            assert elapsed < 0.5
            assert [response.status_code for response in responses] == [200, 200]
            assert all(
                response.json()["assistant"]["reason_code"] == "ENGINE_UNAVAILABLE"
                for response in responses
            )
            assert 1 <= slow.calls <= 2
        finally:
            for client in clients:
                await client.aclose()
            await slow.close()
            app.state.repository.close()

    asyncio.run(scenario())


def test_ten_concurrent_testers_one_hundred_turns_have_only_200_or_429(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app, fake = build_app(
            tmp_path,
            setting_overrides={
                "per_tester_per_minute": 5,
                "per_tester_per_day": 30,
                "global_per_minute": 8,
                "global_per_day": 200,
                "pilot_total_limit": 1_000,
            },
        )
        clients: list[httpx.AsyncClient] = []
        workers: list[tuple[httpx.AsyncClient, str, str]] = []
        try:
            for index in range(10):
                client = httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url=ORIGIN
                )
                clients.append(client)
                code = app.state.repository.create_invite(
                    code=f"fixture-burst-invite-{index:02d}-123456789"
                )
                login = await client.post(
                    "/qa/api/v1/invites/redeem",
                    json={"code": code, "consent": True, "consent_version": "v1"},
                    headers={"Origin": ORIGIN},
                )
                csrf = login.json()["csrf_token"]
                sid = (
                    await client.post(
                        "/qa/api/v1/sessions",
                        json={"mode": "free"},
                        headers=mutation_headers(csrf),
                    )
                ).json()["session"]["id"]
                workers.append((client, csrf, sid))

            async def run_worker(
                worker_index: int, client: httpx.AsyncClient, csrf: str, sid: str
            ) -> list[int]:
                statuses = []
                version = 0
                for turn in range(10):
                    response = await client.post(
                        f"/qa/api/v1/sessions/{sid}/messages",
                        json={
                            "client_message_id": f"burst-{worker_index:02d}-{turn:02d}",
                            "expected_session_version": version,
                            "text": f"동시 부하 질문 {worker_index}-{turn}",
                        },
                        headers=mutation_headers(csrf),
                    )
                    statuses.append(response.status_code)
                    if response.status_code == 200:
                        payload = response.json()
                        assert payload["session_version"] == version + 1
                        assert payload["assistant"]["status"] == "FULL"
                        version += 1
                    else:
                        assert response.status_code == 429
                        assert response.json()["error"] == "RATE_LIMITED"
                return statuses

            batches = await asyncio.gather(
                *(
                    run_worker(index, client, csrf, sid)
                    for index, (client, csrf, sid) in enumerate(workers)
                )
            )
            statuses = [status for batch in batches for status in batch]
            assert len(statuses) == 100
            assert set(statuses) == {200, 429}
            assert statuses.count(200) == 8
            assert statuses.count(429) == 92
            assert len(fake.calls) == 8
        finally:
            for client in clients:
                await client.aclose()
            await app.state.engine_client.close()
            app.state.repository.close()

    asyncio.run(scenario())
