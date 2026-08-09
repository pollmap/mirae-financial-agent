from __future__ import annotations

import asyncio
import base64
import json
import logging
import sqlite3
from pathlib import Path

import httpx
import pytest

from qa_chat.config import QASettings
from qa_chat.crypto import CipherBox
from qa_chat.privacy import contains_sensitive_input
from tests.qa_chat_backend.helpers import ORIGIN, build_app, mutation_headers, redeem, settings


def test_cipher_rejects_tampering_and_cross_domain_replay() -> None:
    cipher = CipherBox(b"x" * 32)
    envelope = cipher.seal("민감한 질문", aad="message:one")
    assert cipher.open_text(envelope, aad="message:one") == "민감한 질문"
    raw = bytearray(base64.urlsafe_b64decode(envelope))
    raw[-1] ^= 1
    tampered = base64.urlsafe_b64encode(raw).decode()
    with pytest.raises(ValueError):
        cipher.open_text(tampered, aad="message:one")
    with pytest.raises(ValueError):
        cipher.open_text(envelope, aad="message:two")


@pytest.mark.parametrize(
    "value",
    [
        "02-1234-5678",
        "031-123-4567",
        "+82 10-1234-5678",
        "123-456-789012",
        "KR01012345678",
        "KR9001011234567",
    ],
)
def test_pii_disguised_as_product_code_or_separated_number_is_blocked(value: str) -> None:
    assert contains_sensitive_input(value) is True


def test_checksum_valid_isin_remains_allowed() -> None:
    assert contains_sensitive_input("채권 코드 KR101501DA16") is False
    assert contains_sensitive_input("ETF 코드 US78462F1030") is False


def test_config_reads_file_secrets_and_requires_explicit_model_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = tmp_path / "transcript"
    auth = tmp_path / "auth"
    transcript.write_text("74" * 32, encoding="utf-8")
    auth.write_text("61" * 32, encoding="utf-8")
    monkeypatch.setenv("QA_TRANSCRIPT_KEY_FILE", str(transcript))
    monkeypatch.setenv("QA_AUTH_SECRET_FILE", str(auth))
    monkeypatch.setenv("HCX_MODEL_ID", "HCX-LOCKED")
    monkeypatch.setenv("APPROVED_HCX_MODEL_ID", "HCX-LOCKED")
    monkeypatch.setenv("HCX_BASE_URL", "https://hcx.example.test")
    monkeypatch.setenv("APPROVED_HCX_BASE_URL", "https://hcx.example.test")
    monkeypatch.setenv("HCX_BASE_URL", "https://clovastudio.stream.ntruss.com")
    monkeypatch.setenv(
        "APPROVED_HCX_BASE_URL", "https://clovastudio.stream.ntruss.com"
    )
    resolved = QASettings.from_env()
    assert resolved.transcript_key == b"t" * 32
    assert resolved.auth_secret == b"a" * 32
    monkeypatch.delenv("APPROVED_HCX_MODEL_ID")
    with pytest.raises(ValueError, match="required"):
        QASettings.from_env()


def test_fixture_model_requires_explicit_loopback_only_preview(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="QA_ALLOW_FIXTURE_PREVIEW"):
        settings(tmp_path, allow_fixture_preview=False)
    with pytest.raises(ValueError, match="loopback"):
        settings(
            tmp_path,
            allow_fixture_preview=True,
            allowed_origins=("https://192.168.10.20:8443",),
            cookie_secure=True,
        )


def test_pii_is_not_sent_or_written_in_plaintext(tmp_path: Path) -> None:
    sensitive = "연락처는 010-1234-5678이고 email person@example.com 입니다"

    async def scenario() -> None:
        app, fake = build_app(tmp_path)
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
                    "client_message_id": "sensitive-msg-0001",
                    "expected_session_version": 0,
                    "text": sensitive,
                },
                headers=mutation_headers(csrf),
            )
            assert response.status_code == 200
            assert response.json()["assistant"]["reason_code"] == "PERSONAL_DATA_DETECTED"
            assert fake.calls == []
            detail = await client.get(f"/qa/api/v1/sessions/{sid}")
            assert sensitive not in detail.text
        raw = b"".join(
            path.read_bytes()
            for path in (tmp_path / "qa.sqlite3", tmp_path / "qa.sqlite3-wal")
            if path.exists()
        )
        assert sensitive.encode("utf-8") not in raw
        assert b"person@example.com" not in raw
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_feedback_exports_are_sanitized_and_delete_is_hard(tmp_path: Path) -> None:
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
            turn = await client.post(
                f"/qa/api/v1/sessions/{sid}/messages",
                json={
                    "client_message_id": "feedback-msg-0001",
                    "expected_session_version": 0,
                    "text": "테스트 질문",
                },
                headers=mutation_headers(csrf),
            )
            message_id = turn.json()["assistant"]["id"]
            missing_problem_tag = await client.put(
                f"/qa/api/v1/messages/{message_id}/feedback",
                json={"verdict": "incorrect", "tags": []},
                headers=mutation_headers(csrf),
            )
            assert missing_problem_tag.status_code == 400
            feedback = await client.put(
                f"/qa/api/v1/messages/{message_id}/feedback",
                json={
                    "verdict": "incorrect",
                    "tags": ["WRONG_VALUE"],
                    "note": "근거 값이 다름",
                },
                headers=mutation_headers(csrf),
            )
            assert feedback.status_code == 200
            exported = await client.get(f"/qa/api/v1/sessions/{sid}/export?format=json")
            assert exported.status_code == 200
            assert exported.json()["privacy"] == {
                "raw_engine_response_included": False,
                "clarification_tokens_included": False,
                "private_trace_included": False,
            }
            serialized = json.dumps(exported.json(), ensure_ascii=False)
            assert "think_trace" not in serialized
            assert '"clarification_token":' not in serialized
            refreshed = await client.get(f"/qa/api/v1/sessions/{sid}")
            assistant_message = next(
                item for item in refreshed.json()["messages"] if item["role"] == "assistant"
            )
            assert assistant_message["assistant"]["feedback"]["verdict"] == "incorrect"
            assert assistant_message["assistant"]["feedback"]["tags"] == ["WRONG_VALUE"]
            markdown = await client.get(f"/qa/api/v1/sessions/{sid}/export?format=markdown")
            assert markdown.status_code == 200
            assert "WRONG_VALUE" in markdown.text
            deleted = await client.delete(
                f"/qa/api/v1/sessions/{sid}", headers=mutation_headers(csrf)
            )
            assert deleted.status_code == 204
            assert (await client.get(f"/qa/api/v1/sessions/{sid}")).status_code == 404
            with sqlite3.connect(tmp_path / "qa.sqlite3") as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id=?", (sid,)
                ).fetchone()[0] == 0
                assert connection.execute("SELECT COUNT(*) FROM deletion_audit").fetchone()[0] == 1
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_origin_and_cross_tester_session_isolation(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, _ = build_app(tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as owner:
            owner_csrf, _ = await redeem(owner, app)
            sid = (
                await owner.post(
                    "/qa/api/v1/sessions",
                    json={"mode": "free"},
                    headers=mutation_headers(owner_csrf),
                )
            ).json()["session"]["id"]
            bad_origin = await owner.post(
                "/qa/api/v1/sessions",
                json={"mode": "free"},
                headers={"Origin": "http://evil.test", "X-CSRF-Token": owner_csrf},
            )
            assert bad_origin.status_code == 403
        second_code = app.state.repository.create_invite(code="second-invite-code-123456789")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as intruder:
            login = await intruder.post(
                "/qa/api/v1/invites/redeem",
                json={"code": second_code, "consent": True, "consent_version": "v1"},
                headers={"Origin": ORIGIN},
            )
            assert login.status_code == 200
            assert (await intruder.get(f"/qa/api/v1/sessions/{sid}")).status_code == 404
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())


def test_operational_logs_and_sqlite_never_contain_chat_or_identifiers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    question = "UNIQUE-PRIVATE-QUESTION 국내 ETF"
    expected_answer = "검증된 답변입니다."

    async def scenario() -> None:
        caplog.set_level(logging.INFO, logger="mirae.qa")
        caplog.set_level(logging.INFO, logger="mirae.qa.turn")
        app, _ = build_app(tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf, _ = await redeem(client, app)
            raw_auth_cookie = client.cookies.get("qa_session")
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
                    "client_message_id": "private-message-0001",
                    "expected_session_version": 0,
                    "text": question,
                },
                headers=mutation_headers(csrf),
            )
            assert response.status_code == 200
            assert expected_answer in response.text
            log_text = caplog.text
            assert question not in log_text
            assert expected_answer not in log_text
            assert sid not in log_text
            assert raw_auth_cookie not in log_text
            raw_database = b"".join(
                path.read_bytes()
                for path in (tmp_path / "qa.sqlite3", tmp_path / "qa.sqlite3-wal")
                if path.exists()
            )
            assert question.encode("utf-8") not in raw_database
            assert expected_answer.encode("utf-8") not in raw_database
            assert sid.encode() in raw_database  # non-sensitive relational key by design
            assert raw_auth_cookie.encode() not in raw_database
        await app.state.engine_client.close()
        app.state.repository.close()

    asyncio.run(scenario())
