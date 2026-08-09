from __future__ import annotations

import json
from pathlib import Path

import pytest

from qa_chat.release_gate import canonical_artifact_sha256
from tests.qa_chat_backend.helpers import settings


def test_release_gate_requires_exact_runtime_identity_and_self_hash(tmp_path: Path) -> None:
    resolved = settings(tmp_path)
    assert resolved.release_metadata_ready is True
    assert resolved.release_gate_error is None

    payload = json.loads(resolved.release_gate_file.read_text(encoding="utf-8"))
    payload["engine_git_sha"] = "f" * 40
    payload["artifact_sha256"] = canonical_artifact_sha256(payload)
    resolved.release_gate_file.write_text(json.dumps(payload), encoding="utf-8")
    assert resolved.release_metadata_ready is False
    assert resolved.release_gate_error == "RELEASE_GATE_RUNTIME_MISMATCH"

    payload["engine_git_sha"] = resolved.engine_git_sha
    resolved.release_gate_file.write_text(json.dumps(payload), encoding="utf-8")
    assert resolved.release_gate_error == "RELEASE_GATE_SELF_HASH_MISMATCH"


def test_release_gate_rejects_failed_or_unsanitized_live_evidence(tmp_path: Path) -> None:
    resolved = settings(tmp_path)
    payload = json.loads(resolved.release_gate_file.read_text(encoding="utf-8"))
    payload["gates"]["canary_100"]["passed"] = 97
    payload["gates"]["canary_100"]["failed"] = 3
    payload["artifact_sha256"] = canonical_artifact_sha256(payload)
    resolved.release_gate_file.write_text(json.dumps(payload), encoding="utf-8")
    assert resolved.release_gate_error == "CANARY_100_NOT_PASSED"

    payload["gates"]["canary_100"]["passed"] = 100
    payload["gates"]["canary_100"]["failed"] = 0
    payload["sanitization"]["contains_questions"] = True
    payload["artifact_sha256"] = canonical_artifact_sha256(payload)
    resolved.release_gate_file.write_text(json.dumps(payload), encoding="utf-8")
    assert resolved.release_gate_error == "UNSAFE_RELEASE_GATE_ARTIFACT"


def test_non_loopback_origins_require_https_and_secure_cookies(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS and Secure"):
        settings(
            tmp_path,
            allowed_origins=("http://192.168.10.20:8443",),
            cookie_secure=False,
        )
    with pytest.raises(ValueError, match="HTTPS and Secure"):
        settings(
            tmp_path,
            allowed_origins=("https://192.168.10.20:8443",),
            cookie_secure=False,
        )
    secured = settings(
        tmp_path,
        allowed_origins=("https://192.168.10.20:8443",),
        cookie_secure=True,
        model_id="HCX-LOCKED",
        approved_model_id="HCX-LOCKED",
        allow_fixture_preview=False,
    )
    assert secured.cookie_secure is True
