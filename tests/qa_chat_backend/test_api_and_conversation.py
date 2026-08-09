from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from tests.qa_chat_backend.helpers import (
    ORIGIN,
    build_app,
    context,
    engine_payload,
    mutation_headers,
    product,
    redeem,
)


def test_auth_csrf_session_idempotency_and_version_conflict(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, fake = build_app(tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            rejected = await client.post(
                "/qa/api/v1/sessions", json={"mode": "free"}, headers={"Origin": ORIGIN}
            )
            assert rejected.status_code == 403
            created = await client.post(
                "/qa/api/v1/sessions",
                json={"mode": "free"},
                headers=mutation_headers(csrf),
            )
            assert created.status_code == 200
            session = created.json()["session"]
            assert session["session_version"] == 0
            body = {
                "client_message_id": "client-message-0001",
                "expected_session_version": 0,
                "text": "국내 ETF를 알려줘",
            }
            first = await client.post(
                f"/qa/api/v1/sessions/{session['id']}/messages",
                json=body,
                headers=mutation_headers(csrf),
            )
            assert first.status_code == 200
            assert first.json()["session_version"] == 1
            assert first.json()["assistant"]["evidence"]["retrieval_channels"][0][
                "verified_count"
            ] == 1
            duplicate = await client.post(
                f"/qa/api/v1/sessions/{session['id']}/messages",
                json=body,
                headers=mutation_headers(csrf),
            )
            assert duplicate.json() == first.json()
            assert len(fake.calls) == 1
            changed = await client.post(
                f"/qa/api/v1/sessions/{session['id']}/messages",
                json={**body, "text": "다른 질문"},
                headers=mutation_headers(csrf),
            )
            assert changed.status_code == 409
            stale = await client.post(
                f"/qa/api/v1/sessions/{session['id']}/messages",
                json={
                    "client_message_id": "client-message-0002",
                    "expected_session_version": 0,
                    "text": "새 질문",
                },
                headers=mutation_headers(csrf),
            )
            assert stale.status_code == 409
            detail = await client.get(f"/qa/api/v1/sessions/{session['id']}")
            assert detail.status_code == 200
            assert detail.json()["session"]["session_version"] == 1
            assert [item["role"] for item in detail.json()["messages"]] == [
                "user",
                "assistant",
            ]
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_engine_clarification_token_is_hidden_single_use_and_old_button_is_stale(
    tmp_path: Path,
) -> None:
    secret_token = "signed-engine-token-MUST-NOT-LEAK"

    def responder(request: httpx.Request, call: int) -> httpx.Response:
        params = request.url.params
        evidence = context(
            answerability="NEEDS_CLARIFICATION" if call in {1, 2} else "FULL",
            token=f"{secret_token}-{call}",
        )
        answer = (
            f"추가 질문입니다. {secret_token}-{call}"
            if call in {1, 2}
            else f"완료 {secret_token}-2"
        )
        return httpx.Response(
            200,
            json=engine_payload(
                params["question_id"], params["question"], evidence, answer=answer
            ),
            headers={"content-type": "application/json"},
        )

    async def scenario() -> None:
        app, fake = build_app(tmp_path, responder=responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            session = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]
            first = await client.post(
                f"/qa/api/v1/sessions/{session['id']}/messages",
                json={
                    "client_message_id": "clarify-message-01",
                    "expected_session_version": 0,
                    "text": "상품 수를 알려줘",
                },
                headers=mutation_headers(csrf),
            )
            first_payload = first.json()
            old_id = first_payload["assistant"]["clarification"]["id"]
            assert first_payload["assistant"]["status"] == "NEEDS_CLARIFICATION"
            assert secret_token not in first.text
            assert "[redacted]" in first_payload["assistant"]["content"]
            second = await client.post(
                f"/qa/api/v1/sessions/{session['id']}/messages",
                json={
                    "client_message_id": "clarify-message-02",
                    "expected_session_version": 1,
                    "text": "완전히 새 질문",
                },
                headers=mutation_headers(csrf),
            )
            new_id = second.json()["assistant"]["clarification"]["id"]
            assert new_id != old_id
            stale = await client.post(
                f"/qa/api/v1/sessions/{session['id']}/messages",
                json={
                    "client_message_id": "clarify-message-03",
                    "expected_session_version": 2,
                    "text": "국내 ETP",
                    "reply_to_message_id": old_id,
                    "clarification_option_value": "domestic_etp",
                },
                headers=mutation_headers(csrf),
            )
            assert stale.status_code == 409
            assert len(fake.calls) == 2
            valid = await client.post(
                f"/qa/api/v1/sessions/{session['id']}/messages",
                json={
                    "client_message_id": "clarify-message-04",
                    "expected_session_version": 2,
                    "text": "국내 ETP",
                    "reply_to_message_id": new_id,
                    "clarification_option_value": "domestic_etp",
                },
                headers=mutation_headers(csrf),
            )
            assert valid.status_code == 200
            assert len(fake.calls) == 3
            assert fake.calls[-1].url.params["clarification_token"].startswith(secret_token)
            assert secret_token not in valid.text
            assert "[redacted]" in valid.json()["assistant"]["content"]
            spent = await client.post(
                f"/qa/api/v1/sessions/{session['id']}/messages",
                json={
                    "client_message_id": "clarify-message-05",
                    "expected_session_version": 3,
                    "text": "국내 ETP",
                    "reply_to_message_id": new_id,
                    "clarification_option_value": "domestic_etp",
                },
                headers=mutation_headers(csrf),
            )
            assert spent.status_code == 409
            assert len(fake.calls) == 3
        raw = b"".join(
            path.read_bytes()
            for path in (tmp_path / "qa.sqlite3", tmp_path / "qa.sqlite3-wal")
            if path.exists()
        )
        assert secret_token.encode() not in raw
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_local_reference_clarification_uses_source_code_never_internal_uid(
    tmp_path: Path,
) -> None:
    first_products = [
        product("KR_ETP:PREF01N001:INTERNAL-ONE", "첫 ETF", "KR7111111111"),
        product("KR_ETP:PREF01N001:INTERNAL-TWO", "둘 ETF", "KR7222222222"),
    ]

    def responder(request: httpx.Request, call: int) -> httpx.Response:
        params = request.url.params
        evidence = context(products=first_products if call == 1 else [first_products[1]])
        return httpx.Response(
            200,
            json=engine_payload(params["question_id"], params["question"], evidence),
            headers={"content-type": "application/json"},
        )

    async def scenario() -> None:
        app, fake = build_app(tmp_path, responder=responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            session_id = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            await client.post(
                f"/qa/api/v1/sessions/{session_id}/messages",
                json={
                    "client_message_id": "reference-msg-01",
                    "expected_session_version": 0,
                    "text": "ETF 두 개 알려줘",
                },
                headers=mutation_headers(csrf),
            )
            local = await client.post(
                f"/qa/api/v1/sessions/{session_id}/messages",
                json={
                    "client_message_id": "reference-msg-02",
                    "expected_session_version": 1,
                    "text": "그 상품 보수는?",
                },
                headers=mutation_headers(csrf),
            )
            assert local.json()["assistant"]["reason_code"] == "AMBIGUOUS_CONVERSATION_REFERENCE"
            assert len(fake.calls) == 1
            assistant = local.json()["assistant"]
            selected = assistant["clarification"]["options"][1]
            resolved = await client.post(
                f"/qa/api/v1/sessions/{session_id}/messages",
                json={
                    "client_message_id": "reference-msg-03",
                    "expected_session_version": 2,
                    "text": "둘 ETF의 보수",
                    "reply_to_message_id": assistant["id"],
                    "clarification_option_value": selected["value"],
                },
                headers=mutation_headers(csrf),
            )
            assert resolved.status_code == 200
            assert len(fake.calls) == 2
            sent = fake.calls[-1].url.params["question"]
            assert "국내 ETP 종목코드 KR7222222222" in sent
            assert "KR_ETP:PREF01N001:INTERNAL-TWO" not in sent
            assert "그 상품 보수는?" in sent
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_explicit_scope_and_metric_corrections_are_visible(tmp_path: Path) -> None:
    old_conditions = [
        {
            "condition_id": "scope_1",
            "kind": "scope",
            "requested_text": "국내 ETP",
            "status": "grounded",
            "grounded_fields": ["product.scope"],
            "note": None,
        },
        {
            "condition_id": "metric_1",
            "kind": "metric",
            "requested_text": "1년 수익률",
            "status": "grounded",
            "grounded_fields": ["return.1y"],
            "note": None,
        },
    ]

    def responder(request: httpx.Request, call: int) -> httpx.Response:
        params = request.url.params
        evidence = context(conditions=old_conditions if call == 1 else [], scope="overseas_etp")
        return httpx.Response(
            200,
            json=engine_payload(params["question_id"], params["question"], evidence),
            headers={"content-type": "application/json"},
        )

    async def scenario() -> None:
        app, fake = build_app(tmp_path, responder=responder)
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
                    "client_message_id": "correction-msg-01",
                    "expected_session_version": 0,
                    "text": "국내 ETP 1년 수익률",
                },
                headers=mutation_headers(csrf),
            )
            corrected = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "correction-msg-02",
                    "expected_session_version": 1,
                    "text": "국내 말고 해외 ETP로, 수익률 말고 보수",
                },
                headers=mutation_headers(csrf),
            )
            changes = corrected.json()["assistant"]["evidence"]["condition_changes"]
            assert {item["kind"] for item in changes} == {"scope", "metric"}
            scope_change = next(item for item in changes if item["kind"] == "scope")
            assert scope_change["previous"] == ["국내 ETP"]
            assert scope_change["current"] == "해외 ETP"
            metric_change = next(item for item in changes if item["kind"] == "metric")
            assert metric_change["previous"] == ["1년 수익률"]
            assert metric_change["current"] == "보수"
            sent = fake.calls[-1].url.params["question"]
            assert "국내 ETP 조건에서" not in sent
            assert "1년 수익률 조건에서" not in sent
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_restart_preserves_auth_session_pending_state_and_feedback(tmp_path: Path) -> None:
    token = "restart-only-encrypted-token"

    def first_responder(request: httpx.Request, _: int) -> httpx.Response:
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

    def second_responder(request: httpx.Request, _: int) -> httpx.Response:
        params = request.url.params
        return httpx.Response(
            200,
            json=engine_payload(params["question_id"], params["question"], context()),
            headers={"content-type": "application/json"},
        )

    async def scenario() -> None:
        first_app, _ = build_app(tmp_path, responder=first_responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, first_app)
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
                    "client_message_id": "restart-message-01",
                    "expected_session_version": 0,
                    "text": "상품 수",
                },
                headers=mutation_headers(csrf),
            )
            clarification_id = first.json()["assistant"]["id"]
            saved = await client.put(
                f"/qa/api/v1/messages/{clarification_id}/feedback",
                json={
                    "verdict": "partly_accurate",
                    "tags": ["BAD_CLARIFICATION"],
                },
                headers=mutation_headers(csrf),
            )
            assert saved.status_code == 200
            session_cookie = client.cookies.get("qa_session")
            csrf_cookie = client.cookies.get("qa_csrf")
        await first_app.state.engine_client.close()
        first_app.state.repository.close()

        second_app, second_fake = build_app(tmp_path, responder=second_responder)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app), base_url=ORIGIN
        ) as resumed:
            resumed.cookies.set("qa_session", session_cookie)
            resumed.cookies.set("qa_csrf", csrf_cookie)
            detail = await resumed.get(f"/qa/api/v1/sessions/{sid}")
            assert detail.status_code == 200
            assistant_message = detail.json()["messages"][1]
            assert assistant_message["assistant"]["feedback"]["tags"] == [
                "BAD_CLARIFICATION"
            ]
            answer = await resumed.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "restart-message-02",
                    "expected_session_version": 1,
                    "text": "국내 ETP",
                    "reply_to_message_id": clarification_id,
                    "clarification_option_value": "domestic_etp",
                },
                headers=mutation_headers(csrf_cookie),
            )
            assert answer.status_code == 200
            assert len(second_fake.calls) == 1
            assert second_fake.calls[0].url.params["clarification_token"] == token
            assert token not in answer.text
        await second_app.state.engine_client.close()
        second_app.state.repository.close()

    asyncio.run(scenario())
