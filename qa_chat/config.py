"""Strict configuration and file-secret loading for the QA gateway."""

from __future__ import annotations

import base64
import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from qa_chat.release_gate import ReleaseGateValidationError, validate_release_gate


def _read_secret(name: str, *, required: bool = True) -> str:
    direct = os.getenv(name)
    file_name = os.getenv(f"{name}_FILE")
    if direct and file_name:
        raise ValueError(f"{name} and {name}_FILE are mutually exclusive")
    if file_name:
        path = Path(file_name)
        if not path.is_file():
            raise ValueError(f"{name}_FILE does not point to a file")
        if path.stat().st_size > 4096:
            raise ValueError(f"{name}_FILE is unexpectedly large")
        direct = path.read_text(encoding="utf-8").strip()
    if required and not direct:
        raise ValueError(f"{name} or {name}_FILE is required")
    return direct or ""


def _decode_secret(value: str, name: str) -> bytes:
    candidate = value.strip()
    if candidate.startswith("base64:"):
        try:
            decoded = base64.urlsafe_b64decode(candidate[7:] + "===")
        except Exception as exc:  # pragma: no cover - implementation-specific decoder errors
            raise ValueError(f"{name} is not valid URL-safe base64") from exc
    elif re.fullmatch(r"[0-9a-fA-F]{64}", candidate):
        decoded = bytes.fromhex(candidate)
    else:
        decoded = candidate.encode("utf-8")
    if len(decoded) < 32:
        raise ValueError(f"{name} must contain at least 32 bytes")
    return decoded


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        result = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


@dataclass(slots=True)
class QASettings:
    database_path: Path
    transcript_key: bytes
    auth_secret: bytes
    engine_base_url: str = "http://engine:8080"
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8090",
        "http://localhost:8090",
    )
    require_origin: bool = True
    cookie_secure: bool = False
    cookie_name: str = "qa_session"
    csrf_cookie_name: str = "qa_csrf"
    auth_ttl_seconds: int = 1_209_600
    retention_days: int = 14
    clarification_ttl_seconds: int = 900
    question_max_chars: int = 2_000
    pilot_chat_enabled: bool = False
    allow_fixture_preview: bool = False
    release_gate_file: Path | None = None
    model_id: str = ""
    approved_model_id: str = ""
    hcx_base_url: str = ""
    approved_hcx_base_url: str = ""
    engine_git_sha: str = "unknown"
    engine_image_digest: str = "unknown"
    data_hash: str = "unknown"
    planner_stage: str = "two_stage"
    vector_status: str = "disabled"
    engine_timeout_seconds: float = 25.0
    engine_concurrency: int = 3
    per_tester_per_minute: int = 5
    per_tester_per_day: int = 30
    global_per_minute: int = 8
    global_per_day: int = 200
    pilot_total_limit: int = 1_000
    max_sessions_per_tester: int = 50
    max_messages_per_session: int = 1_000
    max_ciphertext_bytes_per_tester: int = 64 * 1024 * 1024
    max_ciphertext_bytes_total: int = 512 * 1024 * 1024
    circuit_failure_threshold: int = 5
    circuit_window_seconds: int = 120
    circuit_open_seconds: int = 60
    feedback_tags: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "WRONG_PRODUCT",
                "WRONG_VALUE",
                "MISSING_CONDITION",
                "BAD_CLARIFICATION",
                "WRONG_COMPARISON",
                "EVIDENCE_MISMATCH",
                "UNSAFE_LANGUAGE",
                "SLOW",
                "OTHER",
            }
        )
    )

    @classmethod
    def from_env(cls) -> QASettings:
        settings = cls(
            database_path=Path(os.getenv("QA_DATABASE_PATH", "/data/qa-chat.sqlite3")),
            transcript_key=_decode_secret(
                _read_secret("QA_TRANSCRIPT_KEY"), "QA_TRANSCRIPT_KEY"
            ),
            auth_secret=_decode_secret(_read_secret("QA_AUTH_SECRET"), "QA_AUTH_SECRET"),
            engine_base_url=os.getenv("QA_ENGINE_BASE_URL", "http://engine:8080").rstrip("/"),
            allowed_origins=tuple(
                item.strip().rstrip("/")
                for item in os.getenv(
                    "QA_ALLOWED_ORIGINS",
                    "http://127.0.0.1:8090,http://localhost:8090",
                ).split(",")
                if item.strip()
            ),
            require_origin=_bool("QA_REQUIRE_ORIGIN", True),
            cookie_secure=_bool("QA_COOKIE_SECURE", False),
            auth_ttl_seconds=_int("QA_AUTH_TTL_SECONDS", 1_209_600, 300, 2_592_000),
            retention_days=_int("QA_RETENTION_DAYS", 14, 1, 30),
            clarification_ttl_seconds=_int(
                "QA_CLARIFICATION_TTL_SECONDS", 900, 60, 3_600
            ),
            question_max_chars=_int("QA_QUESTION_MAX_CHARS", 2_000, 100, 2_000),
            pilot_chat_enabled=_bool("PILOT_CHAT_ENABLED", False),
            allow_fixture_preview=_bool("QA_ALLOW_FIXTURE_PREVIEW", False),
            release_gate_file=(
                Path(value)
                if (value := os.getenv("QA_RELEASE_GATE_FILE", "").strip())
                else None
            ),
            model_id=os.getenv("HCX_MODEL_ID", "").strip(),
            approved_model_id=os.getenv("APPROVED_HCX_MODEL_ID", "").strip(),
            hcx_base_url=os.getenv("HCX_BASE_URL", "").strip().rstrip("/"),
            approved_hcx_base_url=os.getenv("APPROVED_HCX_BASE_URL", "")
            .strip()
            .rstrip("/"),
            engine_git_sha=os.getenv("ENGINE_GIT_SHA", "unknown").strip(),
            engine_image_digest=os.getenv("ENGINE_IMAGE_DIGEST", "unknown").strip(),
            data_hash=os.getenv("DATA_HASH", "unknown").strip(),
            planner_stage=os.getenv("PLANNER_STAGE", "two_stage").strip(),
            vector_status=os.getenv("VECTOR_STATUS", "disabled").strip(),
            engine_timeout_seconds=_float("QA_ENGINE_TIMEOUT_SECONDS", 25.0, 1.0, 120.0),
            engine_concurrency=_int("QA_ENGINE_CONCURRENCY", 3, 1, 20),
            per_tester_per_minute=_int("QA_TESTER_PER_MINUTE", 5, 1, 120),
            per_tester_per_day=_int("QA_TESTER_PER_DAY", 30, 1, 10_000),
            global_per_minute=_int("QA_GLOBAL_PER_MINUTE", 8, 1, 1_000),
            global_per_day=_int("QA_GLOBAL_PER_DAY", 200, 1, 100_000),
            pilot_total_limit=_int("QA_PILOT_TOTAL_LIMIT", 1_000, 1, 1_000_000),
            max_sessions_per_tester=_int("QA_MAX_SESSIONS_PER_TESTER", 50, 1, 1_000),
            max_messages_per_session=_int(
                "QA_MAX_MESSAGES_PER_SESSION", 1_000, 10, 20_000
            ),
            max_ciphertext_bytes_per_tester=_int(
                "QA_MAX_CIPHERTEXT_BYTES_PER_TESTER",
                64 * 1024 * 1024,
                1024 * 1024,
                1024 * 1024 * 1024,
            ),
            max_ciphertext_bytes_total=_int(
                "QA_MAX_CIPHERTEXT_BYTES_TOTAL",
                512 * 1024 * 1024,
                1024 * 1024,
                8 * 1024 * 1024 * 1024,
            ),
            circuit_failure_threshold=_int("QA_CIRCUIT_FAILURE_THRESHOLD", 5, 1, 100),
            circuit_window_seconds=_int("QA_CIRCUIT_WINDOW_SECONDS", 120, 10, 3_600),
            circuit_open_seconds=_int("QA_CIRCUIT_OPEN_SECONDS", 60, 5, 3_600),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if len(self.transcript_key) != 32:
            raise ValueError("QA_TRANSCRIPT_KEY must decode to exactly 32 bytes")
        if len(self.auth_secret) < 32:
            raise ValueError("QA_AUTH_SECRET must contain at least 32 bytes")
        if self.transcript_key == self.auth_secret:
            raise ValueError("transcript and authentication secrets must be distinct")
        if self.auth_ttl_seconds > self.retention_days * 86_400:
            raise ValueError("authentication TTL cannot outlive transcript retention")
        if self.max_ciphertext_bytes_total < self.max_ciphertext_bytes_per_tester:
            raise ValueError("total ciphertext quota cannot be below the per-tester quota")
        if self.max_sessions_per_tester < 1 or self.max_messages_per_session < 2:
            raise ValueError("session and message storage quotas must be positive")
        if self.max_ciphertext_bytes_per_tester < 1:
            raise ValueError("ciphertext storage quotas must be positive")
        if not self.engine_base_url.startswith(("http://", "https://")):
            raise ValueError("QA_ENGINE_BASE_URL must be an absolute HTTP(S) URL")
        if not self.allowed_origins:
            raise ValueError("at least one allowed origin is required")
        for origin in self.allowed_origins:
            parsed_origin = urlsplit(origin)
            if (
                parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.hostname
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise ValueError("allowed origins must be credential-free HTTP(S) origins")
            hostname = parsed_origin.hostname or ""
            try:
                loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                loopback = hostname.casefold() == "localhost"
            if not loopback and (parsed_origin.scheme != "https" or not self.cookie_secure):
                raise ValueError("non-loopback origins require HTTPS and Secure cookies")
        if not self.model_id or not self.approved_model_id:
            raise ValueError("HCX_MODEL_ID and APPROVED_HCX_MODEL_ID are required")
        if self.model_id != self.approved_model_id:
            raise ValueError("HCX_MODEL_ID must equal APPROVED_HCX_MODEL_ID")
        fixture_markers = ("FIXTURE", "TEST", "MOCK", "FAKE")
        if any(marker in self.model_id.upper() for marker in fixture_markers):
            if not self.allow_fixture_preview:
                raise ValueError(
                    "fixture/test HCX model IDs require explicit QA_ALLOW_FIXTURE_PREVIEW"
                )
            for origin in self.allowed_origins:
                parsed_origin = urlsplit(origin)
                hostname = parsed_origin.hostname or ""
                try:
                    loopback = ipaddress.ip_address(hostname).is_loopback
                except ValueError:
                    loopback = hostname.casefold() == "localhost"
                if not loopback:
                    raise ValueError("fixture preview is restricted to loopback origins")
        if not self.hcx_base_url or not self.approved_hcx_base_url:
            raise ValueError("HCX_BASE_URL and APPROVED_HCX_BASE_URL are required")
        if self.hcx_base_url != self.approved_hcx_base_url:
            raise ValueError("HCX_BASE_URL must equal APPROVED_HCX_BASE_URL")
        parsed_hcx_url = urlsplit(self.hcx_base_url)
        if (
            parsed_hcx_url.scheme != "https"
            or not parsed_hcx_url.hostname
            or parsed_hcx_url.username is not None
            or parsed_hcx_url.password is not None
            or parsed_hcx_url.query
            or parsed_hcx_url.fragment
        ):
            raise ValueError("the approved HCX base URL must be a credential-free HTTPS URL")
        if self.planner_stage != "two_stage":
            raise ValueError("the human QA chatbot requires the two-stage planner")

    @property
    def release_metadata_ready(self) -> bool:
        return self.release_gate_error is None

    @property
    def release_gate_error(self) -> str | None:
        try:
            validate_release_gate(
                self.release_gate_file,
                engine_git_sha=self.engine_git_sha,
                engine_image_digest=self.engine_image_digest,
                data_hash=self.data_hash,
                model_id=self.model_id,
                hcx_base_url=self.hcx_base_url,
            )
        except ReleaseGateValidationError as exc:
            return exc.code
        return None
