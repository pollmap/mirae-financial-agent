"""Strict, non-secret proof required before the human pilot can send questions."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class ReleaseGateValidationError(ValueError):
    """A stable code explains why the external human-pilot gate is closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_HEX64 = re.compile(r"[a-f0-9]{64}")
_SHA256 = re.compile(r"sha256:[a-f0-9]{64}")
_GIT_SHA = re.compile(r"[a-f0-9]{40}")
_MODEL = re.compile(r"HCX-[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_TOP_LEVEL_KEYS = {
    "schema_version",
    "generated_at_utc",
    "engine_git_sha",
    "engine_image_digest",
    "data_hash",
    "hcx_model_id",
    "hcx_base_url",
    "planner_stage",
    "gates",
    "sanitization",
    "artifact_sha256",
}
_GATE_KEYS = {
    "status",
    "total",
    "passed",
    "failed",
    "suite_sha256",
    "report_sha256",
    "verified_at_utc",
}
_SANITIZATION = {
    "contains_questions": False,
    "contains_prompts": False,
    "contains_answers": False,
    "contains_tokens": False,
    "contains_credentials": False,
}


def _fail(code: str) -> None:
    raise ReleaseGateValidationError(code)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("INVALID_RELEASE_GATE_SCHEMA")
        result[key] = value
    return result


def _valid_utc(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.utcoffset().total_seconds() == 0
    )


def _valid_https_endpoint(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def canonical_artifact_sha256(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    encoded = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_release_gate(
    path: Path | None,
    *,
    engine_git_sha: str,
    engine_image_digest: str,
    data_hash: str,
    model_id: str,
    hcx_base_url: str,
) -> dict[str, Any]:
    """Validate exact runtime identity plus sanitized 20/100 live-gate evidence."""

    if path is None:
        _fail("MISSING_RELEASE_GATE_FILE")
    try:
        if not path.is_file() or not 1 <= path.stat().st_size <= 64_000:
            _fail("INVALID_RELEASE_GATE_FILE")
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except ReleaseGateValidationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("INVALID_RELEASE_GATE_FILE")
    if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_KEYS:
        _fail("INVALID_RELEASE_GATE_SCHEMA")
    if payload.get("schema_version") != "mirae.qa.release-gate.v1":
        _fail("INVALID_RELEASE_GATE_SCHEMA")
    if not _valid_utc(payload.get("generated_at_utc")):
        _fail("INVALID_RELEASE_GATE_TIME")

    expected_identity = {
        "engine_git_sha": engine_git_sha,
        "engine_image_digest": engine_image_digest,
        "data_hash": data_hash,
        "hcx_model_id": model_id,
        "hcx_base_url": hcx_base_url.rstrip("/"),
        "planner_stage": "two",
    }
    if any(payload.get(key) != value for key, value in expected_identity.items()):
        _fail("RELEASE_GATE_RUNTIME_MISMATCH")
    if not _GIT_SHA.fullmatch(engine_git_sha) or engine_git_sha == "0" * 40:
        _fail("INVALID_ENGINE_GIT_SHA")
    if (
        not _SHA256.fullmatch(engine_image_digest)
        or engine_image_digest == "sha256:" + "0" * 64
    ):
        _fail("INVALID_ENGINE_IMAGE_DIGEST")
    if not _SHA256.fullmatch(data_hash) or data_hash == "sha256:" + "0" * 64:
        _fail("INVALID_DATA_HASH")
    if not _MODEL.fullmatch(model_id):
        _fail("INVALID_HCX_MODEL_ID")
    if not _valid_https_endpoint(hcx_base_url):
        _fail("INVALID_HCX_BASE_URL")

    gates = payload.get("gates")
    if not isinstance(gates, dict) or set(gates) != {"smoke_20", "canary_100"}:
        _fail("INVALID_RELEASE_GATE_SCHEMA")
    for name, total, minimum_passed in (
        ("smoke_20", 20, 20),
        ("canary_100", 100, 98),
    ):
        gate = gates.get(name)
        if not isinstance(gate, dict) or set(gate) != _GATE_KEYS:
            _fail("INVALID_RELEASE_GATE_SCHEMA")
        if (
            gate.get("status") != "PASS" or gate.get("total") != total
        ):
            _fail(f"{name.upper()}_NOT_PASSED")
        passed = gate.get("passed")
        failed = gate.get("failed")
        if (
            type(passed) is not int
            or type(failed) is not int
            or not minimum_passed <= passed <= total
            or failed != total - passed
            or not _valid_utc(gate.get("verified_at_utc"))
        ):
            _fail(f"{name.upper()}_NOT_PASSED")
        for field in ("suite_sha256", "report_sha256"):
            digest = gate.get(field)
            if not isinstance(digest, str) or not _HEX64.fullmatch(digest) or digest == "0" * 64:
                _fail("INVALID_RELEASE_GATE_DIGEST")

    if payload.get("sanitization") != _SANITIZATION:
        _fail("UNSAFE_RELEASE_GATE_ARTIFACT")
    stated_hash = payload.get("artifact_sha256")
    if (
        not isinstance(stated_hash, str)
        or not _HEX64.fullmatch(stated_hash)
        or stated_hash == "0" * 64
        or stated_hash != canonical_artifact_sha256(payload)
    ):
        _fail("RELEASE_GATE_SELF_HASH_MISMATCH")
    return payload
