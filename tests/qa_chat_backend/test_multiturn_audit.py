from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from tests.qa_chat_backend.helpers import (
    ORIGIN,
    build_app,
    context,
    engine_payload,
    mutation_headers,
    product,
    redeem,
)


def _engine_response(request: httpx.Request, evidence: dict) -> httpx.Response:
    params = request.url.params
    return httpx.Response(
        200,
        json=engine_payload(params["question_id"], params["question"], evidence),
        headers={"content-type": "application/json"},
    )


def test_ordinary_structural_follow_ups_keep_exact_product_for_four_turns(
    tmp_path: Path,
) -> None:
    item = product(
        "KR_ETP:PREF01N001:GATEWAY-ONLY-UID",
        "연속성 테스트 ETF",
        "KR7333333333",
    )

    def responder(request: httpx.Request, _: int) -> httpx.Response:
        return _engine_response(request, context(products=[item]))

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
            questions = [
                "연속성 테스트 ETF를 알려줘",
                "보수도 알려줘",
                "기준일은?",
                "근거도 알려줘",
            ]
            for index, question in enumerate(questions):
                response = await client.post(
                    f"/qa/api/v1/sessions/{sid}/messages",
                    json={
                        "client_message_id": f"structural-follow-{index:02d}",
                        "expected_session_version": index,
                        "text": question,
                    },
                    headers=mutation_headers(csrf),
                )
                assert response.status_code == 200
                assert response.json()["assistant"]["status"] == "FULL"
            assert len(fake.calls) == 4
            for request in fake.calls[1:]:
                sent = request.url.params["question"]
                assert "국내 ETP 종목코드 KR7333333333" in sent
                assert "GATEWAY-ONLY-UID" not in sent
            assert "보수도 알려줘" in fake.calls[1].url.params["question"]
            assert "기준일은?" in fake.calls[2].url.params["question"]
            assert "근거도 알려줘" in fake.calls[3].url.params["question"]
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_all_ambiguous_results_are_offered_and_free_text_retries_without_hcx(
    tmp_path: Path,
) -> None:
    products = [
        product(
            f"KR_ETP:PREF01N001:INTERNAL-{index}",
            f"선택 ETF {index}",
            f"KR7{index:010d}",
        )
        for index in range(1, 7)
    ]

    def responder(request: httpx.Request, call: int) -> httpx.Response:
        evidence = context(products=products if call == 1 else [products[5]])
        return _engine_response(request, evidence)

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
                    "client_message_id": "free-select-turn-01",
                    "expected_session_version": 0,
                    "text": "후보 여섯 개를 보여줘",
                },
                headers=mutation_headers(csrf),
            )
            ambiguous = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "free-select-turn-02",
                    "expected_session_version": 1,
                    "text": "그 상품 보수는?",
                },
                headers=mutation_headers(csrf),
            )
            first_clarification = ambiguous.json()["assistant"]["clarification"]
            assert len(first_clarification["options"]) == 6
            assert len(fake.calls) == 1

            unresolved = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "free-select-turn-03",
                    "expected_session_version": 2,
                    "text": "존재하지 않는 상품",
                    "reply_to_message_id": first_clarification["id"],
                },
                headers=mutation_headers(csrf),
            )
            assert unresolved.status_code == 200
            assert unresolved.json()["assistant"]["status"] == "NEEDS_CLARIFICATION"
            assert len(unresolved.json()["assistant"]["clarification"]["options"]) == 6
            assert len(fake.calls) == 1

            retry_id = unresolved.json()["assistant"]["clarification"]["id"]
            resolved = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "free-select-turn-04",
                    "expected_session_version": 3,
                    "text": "KR70000000006",
                    "reply_to_message_id": retry_id,
                },
                headers=mutation_headers(csrf),
            )
            assert resolved.status_code == 200
            assert len(fake.calls) == 2
            sent = fake.calls[-1].url.params["question"]
            assert "KR70000000006" in sent
            assert "그 상품 보수는?" in sent
            assert "INTERNAL-6" not in sent
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_cross_scope_structural_follow_up_keeps_conditions_and_is_not_refused(
    tmp_path: Path,
) -> None:
    conditions = [
        {
            "condition_id": "scope_bond",
            "kind": "scope",
            "requested_text": "국내채권",
            "status": "grounded",
            "grounded_fields": ["product.scope"],
            "note": None,
        },
        {
            "condition_id": "scope_overseas",
            "kind": "scope",
            "requested_text": "해외 ETP",
            "status": "grounded",
            "grounded_fields": ["product.scope"],
            "note": None,
        },
    ]
    items = [
        product("BOND:PRBD01N001:BOND-INTERNAL", "국내채권 A", "KR7111111111"),
        product("OVERSEAS:PREF02N001:ETF-INTERNAL", "해외 ETF B", "US7222222222"),
    ]

    def responder(request: httpx.Request, call: int) -> httpx.Response:
        evidence = context(
            answerability="FULL" if call == 1 else "INCOMPARABLE",
            products=items,
            conditions=conditions,
            scope="multi",
        )
        return _engine_response(request, evidence)

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
                    "client_message_id": "cross-scope-follow-01",
                    "expected_session_version": 0,
                    "text": "국내채권과 해외 ETP를 비교해줘",
                },
                headers=mutation_headers(csrf),
            )
            follow_up = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "cross-scope-follow-02",
                    "expected_session_version": 1,
                    "text": "기준일은?",
                },
                headers=mutation_headers(csrf),
            )
            assert follow_up.status_code == 200
            assert follow_up.json()["assistant"]["status"] == "SAFE_LIMITED"
            assert follow_up.json()["assistant"]["answerability"] == "INCOMPARABLE"
            sent = fake.calls[-1].url.params["question"]
            assert "국내채권" in sent and "해외 ETP" in sent
            assert "기준일은?" in sent
            assert len(fake.calls) == 2
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_feedback_pii_is_rejected_and_invites_are_always_one_time(tmp_path: Path) -> None:
    sensitive_note = "연락처 010-9876-5432로 확인해 주세요"

    async def scenario() -> None:
        app, _ = build_app(tmp_path)
        with pytest.raises(ValueError, match="invite"):
            app.state.repository.create_invite(max_uses=2)
        one_time = app.state.repository.create_invite(code="one-time-invite-123456789")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            login = await client.post(
                "/qa/api/v1/invites/redeem",
                json={"code": one_time, "consent": True, "consent_version": "v1"},
                headers={"Origin": ORIGIN},
            )
            assert login.status_code == 200
            second_use = await client.post(
                "/qa/api/v1/invites/redeem",
                json={"code": one_time, "consent": True, "consent_version": "v1"},
                headers={"Origin": ORIGIN},
            )
            assert second_use.status_code == 400
            csrf = login.json()["csrf_token"]
            sid = (
                await client.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(csrf),
                )
            ).json()["session"]["id"]
            turn = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "feedback-pii-turn-01",
                    "expected_session_version": 0,
                    "text": "테스트 질문",
                },
                headers=mutation_headers(csrf),
            )
            message_id = turn.json()["assistant"]["id"]
            blocked = await client.put(
                f"/qa/api/v1/messages/{message_id}/feedback",
                json={
                    "verdict": "incorrect",
                    "tags": ["OTHER"],
                    "note": sensitive_note,
                },
                headers=mutation_headers(csrf),
            )
            assert blocked.status_code == 400
        raw = b"".join(
            path.read_bytes()
            for path in (tmp_path / "qa.sqlite3", tmp_path / "qa.sqlite3-wal")
            if path.exists()
        )
        assert sensitive_note.encode("utf-8") not in raw
        with app.state.repository._lock:  # noqa: SLF001 - storage assertion
            assert app.state.repository._connection.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM feedback"
            ).fetchone()[0] == 0
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())
