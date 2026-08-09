#!/usr/bin/env python3
"""Fail-closed production/deployment checks without printing secret values."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.execution.engine import DuckDBEngine  # noqa: E402

OFFICIAL_HCX_BASE_URL = "https://clovastudio.stream.ntruss.com"
APPROVED_HCX_MODEL_ID = "HCX-007"
EXPECTED_SNAPSHOT_DATE = "2026-07-11"
EXPECTED_LIVE_HCX_GATE = "HCX_20_QUESTION_ONE_VS_TWO_STAGE"
EXPECTED_LIVE_HCX_E2E_GATE = "HCX_100_QUESTION_TWO_STAGE_E2E"
EXPECTED_LIVE_HCX_EXTENSIVE_GATE = "HCX_EXTENSIVE_1000_DIRECT_PLUS_MULTITURN_E2E"
IMAGE_PATTERN = re.compile(r"^[^\s@]+@sha256:[a-f0-9]{64}$")
PLACEHOLDER_SECRET_MARKERS = (
    "at-least-",
    "change-me",
    "changeme",
    "development-only",
    "example",
    "placeholder",
    "replace-me",
    "replace-with",
    "secret-injected",
    "tbd",
    "todo",
    "your-",
    "your_",
)
PLACEHOLDER_HOST_SUFFIXES = (
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".invalid",
    ".test",
)


def _required_text(environ: Mapping[str, str], name: str, errors: list[str]) -> str:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        errors.append(f"{name} is required")
        return ""
    if raw != raw.strip():
        errors.append(f"{name} must not have surrounding whitespace")
    return raw.strip()


def _required_int(
    environ: Mapping[str, str],
    name: str,
    minimum: int,
    maximum: int,
    errors: list[str],
) -> int | None:
    raw = _required_text(environ, name, errors)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} must be an integer")
        return None
    if not minimum <= value <= maximum:
        errors.append(f"{name} must be between {minimum} and {maximum}")
        return None
    return value


def _required_float(
    environ: Mapping[str, str],
    name: str,
    minimum: float,
    maximum: float,
    errors: list[str],
) -> float | None:
    raw = _required_text(environ, name, errors)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        errors.append(f"{name} must be numeric")
        return None
    if not minimum <= value <= maximum:
        errors.append(f"{name} must be between {minimum:g} and {maximum:g}")
        return None
    return value


def _validate_secret(
    environ: Mapping[str, str],
    name: str,
    minimum_bytes: int,
    errors: list[str],
) -> None:
    value = _required_text(environ, name, errors)
    if not value:
        return
    lowered = value.casefold()
    if (
        any(marker in lowered for marker in PLACEHOLDER_SECRET_MARKERS)
        or value.startswith("<")
        or value.endswith(">")
    ):
        errors.append(f"{name} must not be a placeholder")
    if len(value.encode("utf-8")) < minimum_bytes:
        errors.append(f"{name} must contain at least {minimum_bytes} UTF-8 bytes")


def _validate_public_base_url(
    raw_url: str,
    errors: list[str],
    *,
    allow_local_http: bool,
) -> None:
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        errors.append("PUBLIC_BASE_URL must be a valid URL")
        return
    hostname = (parsed.hostname or "").casefold()
    local_host = hostname in {"127.0.0.1", "localhost", "::1"}
    expected_scheme = "http" if allow_local_http and local_host else "https"
    if parsed.scheme != expected_scheme:
        errors.append("PUBLIC_BASE_URL must use HTTPS (HTTP is allowed only for local checks)")
    if not hostname:
        errors.append("PUBLIC_BASE_URL must include a hostname")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        errors.append("PUBLIC_BASE_URL must not include credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        errors.append("PUBLIC_BASE_URL must not include an application path")
    if not local_host and (
        hostname in {"example.com", "example.org"}
        or hostname.endswith(PLACEHOLDER_HOST_SUFFIXES)
        or "placeholder" in hostname
    ):
        errors.append("PUBLIC_BASE_URL must not use a placeholder hostname")


def _validate_database(database_path: Path, errors: list[str]) -> None:
    if not database_path.is_file() or database_path.stat().st_size == 0:
        errors.append("MIRAE_DATABASE_PATH must reference a non-empty file")
        return
    if not os.access(database_path, os.R_OK):
        errors.append("MIRAE_DATABASE_PATH must be readable")
        return
    try:
        readiness_error = DuckDBEngine(database_path).readiness_error()
    except Exception:  # pragma: no cover - defensive redaction boundary
        errors.append("MIRAE_DATABASE_PATH failed readiness validation")
        return
    if readiness_error is not None:
        errors.append(f"MIRAE_DATABASE_PATH failed readiness validation ({readiness_error})")


def _validate_live_hcx_gate(report_path: Path, errors: list[str]) -> None:
    """Require sanitized evidence that both HCX planner stages passed 20 live cases."""

    if not report_path.is_file() or report_path.stat().st_size == 0:
        errors.append("LIVE_HCX_GATE_REPORT must reference a non-empty JSON file")
        return
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append("LIVE_HCX_GATE_REPORT must be valid UTF-8 JSON")
        return
    if not isinstance(payload, dict):
        errors.append("LIVE_HCX_GATE_REPORT must contain a JSON object")
        return

    required_matches = {
        "status": "PASS",
        "gate": EXPECTED_LIVE_HCX_GATE,
        "model_id": APPROVED_HCX_MODEL_ID,
        "approved_planner_stage": "two",
        "case_count": 20,
        "provider_call_count": 40,
        "both_stage_valid_count": 20,
        "both_stage_match_count": 20,
        "secret_values_recorded": False,
        "questions_recorded": False,
        "plans_recorded": False,
    }
    if any(payload.get(key) != value for key, value in required_matches.items()):
        errors.append("LIVE_HCX_GATE_REPORT did not pass the required sanitized 20-question A/B gate")
    suite_hash = payload.get("question_suite_sha256")
    if not isinstance(suite_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", suite_hash):
        errors.append("LIVE_HCX_GATE_REPORT is missing a valid question-suite hash")
    completed_at = payload.get("completed_at_utc")
    if not isinstance(completed_at, str) or not completed_at.endswith(("Z", "+00:00")):
        errors.append("LIVE_HCX_GATE_REPORT is missing its UTC completion time")


def _validate_live_hcx_e2e_gate(report_path: Path, errors: list[str]) -> None:
    """Require sanitized 100-case live planner-to-evidence verification."""

    if not report_path.is_file() or report_path.stat().st_size == 0:
        errors.append("LIVE_HCX_E2E_GATE_REPORT must reference a non-empty JSON file")
        return
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append("LIVE_HCX_E2E_GATE_REPORT must be valid UTF-8 JSON")
        return
    if not isinstance(payload, dict):
        errors.append("LIVE_HCX_E2E_GATE_REPORT must contain a JSON object")
        return

    required_matches = {
        "status": "PASS",
        "gate": EXPECTED_LIVE_HCX_E2E_GATE,
        "model_id": APPROVED_HCX_MODEL_ID,
        "approved_planner_stage": "two",
        "case_count": 100,
        "minimum_accuracy": 0.98,
        "hcx_planned_case_count": 100,
        "evidence_linked_case_count": 100,
        "cross_scope_refusal_count": 0,
        "secret_values_recorded": False,
        "questions_recorded": False,
        "prompts_recorded": False,
        "plans_recorded": False,
        "answers_recorded": False,
        "product_identifiers_recorded": False,
    }
    if any(payload.get(key) != value for key, value in required_matches.items()):
        errors.append("LIVE_HCX_E2E_GATE_REPORT did not pass the required 100-question E2E gate")
    passed_count = payload.get("passed_count")
    accuracy = payload.get("accuracy")
    if (
        not isinstance(passed_count, int)
        or passed_count < 98
        or not isinstance(accuracy, int | float)
        or float(accuracy) < 0.98
    ):
        errors.append("LIVE_HCX_E2E_GATE_REPORT did not meet the 98% accuracy threshold")
    suite_hash = payload.get("question_suite_sha256")
    if not isinstance(suite_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", suite_hash):
        errors.append("LIVE_HCX_E2E_GATE_REPORT is missing a valid question-suite hash")
    completed_at = payload.get("completed_at_utc")
    if not isinstance(completed_at, str) or not completed_at.endswith(("Z", "+00:00")):
        errors.append("LIVE_HCX_E2E_GATE_REPORT is missing its UTC completion time")
    by_kind = payload.get("by_kind")
    expected_mix = {
        "rank_single": 35,
        "filter_search": 25,
        "count_aggregate": 20,
        "cross_scope": 20,
    }
    if not isinstance(by_kind, dict) or any(
        not isinstance(by_kind.get(kind), dict)
        or by_kind[kind].get("total") != count
        or by_kind[kind].get("passed", 0) < int(count * 0.98)
        for kind, count in expected_mix.items()
    ):
        errors.append("LIVE_HCX_E2E_GATE_REPORT has an invalid category mix or category result")


def _validate_live_hcx_extensive_gate(report_path: Path, errors: list[str]) -> None:
    """Require the 1,000-direct plus multi-turn live HCX release evidence."""

    if not report_path.is_file() or report_path.stat().st_size == 0:
        errors.append("LIVE_HCX_EXTENSIVE_GATE_REPORT must reference a non-empty JSON file")
        return
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append("LIVE_HCX_EXTENSIVE_GATE_REPORT must be valid UTF-8 JSON")
        return
    if not isinstance(payload, dict):
        errors.append("LIVE_HCX_EXTENSIVE_GATE_REPORT must contain a JSON object")
        return

    required_matches = {
        "status": "PASS",
        "gate": EXPECTED_LIVE_HCX_EXTENSIVE_GATE,
        "model_id": APPROVED_HCX_MODEL_ID,
        "approved_planner_stage": "two",
        "direct_case_count": 1_000,
        "direct_semantic_spec_count": 500,
        "direct_surface_count_per_spec": 2,
        "minimum_accuracy": 0.98,
        "direct_hcx_planned_case_count": 1_000,
        "direct_evidence_linked_case_count": 1_000,
        "direct_contract_valid_response_count": 1_000,
        "cross_scope_refusal_count": 0,
        "two_follow_up_flow_count": 100,
        "three_follow_up_flow_count": 100,
        "baseline_flow_passed_count": 200,
        "two_follow_up_flow_passed_count": 100,
        "three_follow_up_flow_passed_count": 100,
        "two_follow_up_final_hcx_count": 100,
        "three_follow_up_final_hcx_count": 100,
        "two_follow_up_final_evidence_count": 100,
        "three_follow_up_final_evidence_count": 100,
        "two_follow_up_signature_match_count": 100,
        "three_follow_up_signature_match_count": 100,
        "flow_contract_valid_response_count": 700,
        "flow_live_api_request_count": 700,
        "live_api_request_count": 1_700,
        "secret_values_recorded": False,
        "questions_recorded": False,
        "prompts_recorded": False,
        "plans_recorded": False,
        "answers_recorded": False,
        "clarification_tokens_recorded": False,
        "product_identifiers_recorded": False,
    }
    if any(payload.get(key) != value for key, value in required_matches.items()):
        errors.append(
            "LIVE_HCX_EXTENSIVE_GATE_REPORT did not pass the required "
            "1,000-direct plus multi-turn HCX gate"
        )
    passed_count = payload.get("direct_passed_count")
    accuracy = payload.get("direct_accuracy")
    if (
        not isinstance(passed_count, int)
        or passed_count < 980
        or not isinstance(accuracy, int | float)
        or float(accuracy) < 0.98
    ):
        errors.append("LIVE_HCX_EXTENSIVE_GATE_REPORT did not meet the 98% direct accuracy threshold")
    suite_hash = payload.get("question_suite_sha256")
    if not isinstance(suite_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", suite_hash):
        errors.append("LIVE_HCX_EXTENSIVE_GATE_REPORT is missing a valid question-suite hash")
    completed_at = payload.get("completed_at_utc")
    if not isinstance(completed_at, str) or not completed_at.endswith(("Z", "+00:00")):
        errors.append("LIVE_HCX_EXTENSIVE_GATE_REPORT is missing its UTC completion time")
    expected_mix = {
        "rank_single": 468,
        "filter_search": 228,
        "count_aggregate": 166,
        "cross_scope": 138,
    }
    by_kind = payload.get("direct_by_kind")
    if not isinstance(by_kind, dict) or any(
        not isinstance(by_kind.get(kind), dict)
        or by_kind[kind].get("total") != count
        or by_kind[kind].get("passed", 0) < int(count * 0.98)
        for kind, count in expected_mix.items()
    ):
        errors.append(
            "LIVE_HCX_EXTENSIVE_GATE_REPORT has an invalid direct category mix or result"
        )


def validate_environment(
    environ: Mapping[str, str],
    *,
    allow_local_http: bool = False,
) -> list[str]:
    """Return redacted production-configuration errors; an empty list means pass."""

    errors: list[str] = []
    if _required_text(environ, "APP_ENV", errors) != "production":
        errors.append("APP_ENV must be production")
    if _required_text(environ, "PLANNER_MODE", errors) != "hcx":
        errors.append("PLANNER_MODE must be hcx")
    if _required_text(environ, "PLANNER_STAGE", errors) != "two":
        errors.append("PLANNER_STAGE must be two")
    if _required_text(environ, "HCX_MODEL_ID", errors) != APPROVED_HCX_MODEL_ID:
        errors.append(f"HCX_MODEL_ID must be {APPROVED_HCX_MODEL_ID}")
    if _required_text(environ, "HCX_BASE_URL", errors).rstrip("/") != OFFICIAL_HCX_BASE_URL:
        errors.append("HCX_BASE_URL must be the approved official HTTPS endpoint")

    _validate_secret(environ, "CLOVA_STUDIO_API_KEY", 20, errors)
    clarification_enabled = _required_text(
        environ, "ENABLE_CLARIFICATION_STATE", errors
    ).casefold()
    if clarification_enabled != "true":
        errors.append("ENABLE_CLARIFICATION_STATE must be true")
    _validate_secret(environ, "CLARIFICATION_SIGNING_KEY", 24, errors)
    if (
        environ.get("CLOVA_STUDIO_API_KEY", "").strip()
        and environ.get("CLOVA_STUDIO_API_KEY", "").strip()
        == environ.get("CLARIFICATION_SIGNING_KEY", "").strip()
    ):
        errors.append("HCX and clarification secrets must be distinct")

    database_raw = _required_text(environ, "MIRAE_DATABASE_PATH", errors)
    if database_raw:
        _validate_database(Path(database_raw), errors)

    live_gate_raw = _required_text(environ, "LIVE_HCX_GATE_REPORT", errors)
    if live_gate_raw:
        _validate_live_hcx_gate(Path(live_gate_raw), errors)
    live_e2e_gate_raw = _required_text(environ, "LIVE_HCX_E2E_GATE_REPORT", errors)
    if live_e2e_gate_raw:
        _validate_live_hcx_e2e_gate(Path(live_e2e_gate_raw), errors)
    live_extensive_gate_raw = _required_text(environ, "LIVE_HCX_EXTENSIVE_GATE_REPORT", errors)
    if live_extensive_gate_raw:
        _validate_live_hcx_extensive_gate(Path(live_extensive_gate_raw), errors)

    image_ref = _required_text(environ, "MIRAE_IMAGE", errors)
    if image_ref and (
        not IMAGE_PATTERN.fullmatch(image_ref)
        or image_ref.endswith("sha256:" + "0" * 64)
        or any(marker in image_ref.casefold() for marker in PLACEHOLDER_SECRET_MARKERS)
    ):
        errors.append("MIRAE_IMAGE must be a non-placeholder immutable @sha256 reference")

    public_base_url = _required_text(environ, "PUBLIC_BASE_URL", errors)
    if public_base_url:
        _validate_public_base_url(public_base_url, errors, allow_local_http=allow_local_http)
    public_hostname = environ.get("PUBLIC_HOSTNAME", "").strip()
    caddy_image = environ.get("CADDY_IMAGE", "").strip()
    if public_hostname or caddy_image:
        if not public_hostname:
            errors.append("PUBLIC_HOSTNAME is required when CADDY_IMAGE is configured")
        if not caddy_image:
            errors.append("CADDY_IMAGE is required when PUBLIC_HOSTNAME is configured")
        elif (
            not IMAGE_PATTERN.fullmatch(caddy_image)
            or caddy_image.endswith("sha256:" + "0" * 64)
            or any(marker in caddy_image.casefold() for marker in PLACEHOLDER_SECRET_MARKERS)
        ):
            errors.append("CADDY_IMAGE must be a non-placeholder immutable @sha256 reference")
        if public_base_url and public_hostname:
            base_hostname = (urlsplit(public_base_url).hostname or "").casefold()
            if public_hostname.casefold() != base_hostname:
                errors.append("PUBLIC_HOSTNAME must match the PUBLIC_BASE_URL hostname")
    if _required_text(environ, "READINESS_PATH", errors) != "/health/ready":
        errors.append("READINESS_PATH must be /health/ready")
    if _required_text(environ, "LIVENESS_PATH", errors) != "/health/live":
        errors.append("LIVENESS_PATH must be /health/live")
    if _required_text(environ, "PUBLIC_ENDPOINT_PATH", errors) != "/answer":
        errors.append("PUBLIC_ENDPOINT_PATH must be /answer")
    if _required_text(environ, "PUBLIC_ENDPOINT_METHOD", errors).upper() != "GET":
        errors.append("PUBLIC_ENDPOINT_METHOD must be GET")
    if _required_text(environ, "ACCESS_LOG_ENABLED", errors).casefold() != "false":
        errors.append("ACCESS_LOG_ENABLED must be false")
    if _required_int(environ, "WEB_CONCURRENCY", 1, 1, errors) != 1:
        errors.append("WEB_CONCURRENCY must be exactly 1")

    timeout = _required_float(environ, "HCX_TIMEOUT_SECONDS", 1, 60, errors)
    total_deadline = _required_float(environ, "HCX_TOTAL_DEADLINE_SECONDS", 1, 120, errors)
    if timeout is not None and total_deadline is not None and total_deadline < timeout:
        errors.append("HCX_TOTAL_DEADLINE_SECONDS must be at least HCX_TIMEOUT_SECONDS")
    _required_int(environ, "HCX_MAX_RETRIES", 1, 3, errors)
    _required_int(environ, "HCX_MAX_CONCURRENCY", 1, 4, errors)
    _required_int(environ, "DB_MAX_CONCURRENCY", 1, 16, errors)
    _required_int(environ, "HCX_QPM_LIMIT", 1, 60, errors)
    _required_int(environ, "HCX_TPM_BUDGET", 1_024, 10_000_000, errors)
    _required_int(environ, "MONTHLY_COST_CAP_KRW", 1_000, 100_000_000, errors)
    return errors


def check_http_readiness(
    public_base_url: str,
    *,
    timeout_seconds: float = 5,
) -> list[str]:
    """Check liveness/readiness without sending a question or spending HCX tokens."""

    errors: list[str] = []
    expectations = {
        "/health/live": {"status": "ok"},
        "/health/ready": {
            "status": "ready",
            "data_snapshot_date": EXPECTED_SNAPSHOT_DATE,
        },
    }
    base = public_base_url.rstrip("/")
    for path, expected in expectations.items():
        request = urllib.request.Request(
            f"{base}{path}",
            headers={"Accept": "application/json", "User-Agent": "mirae-production-preflight/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read(8_193)
                if len(body) > 8_192:
                    raise ValueError("health response too large")
                payload = json.loads(body.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, urllib.error.HTTPError):
            errors.append(f"{path} HTTP check failed")
            continue
        if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
            errors.append(f"{path} returned an unexpected readiness contract")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate production env, immutable DB/image, budgets, and optional HTTP readiness."
    )
    parser.add_argument(
        "--check-http",
        action="store_true",
        help="also call PUBLIC_BASE_URL health endpoints (does not call HCX or /answer)",
    )
    parser.add_argument(
        "--allow-local-http",
        action="store_true",
        help="permit HTTP only for localhost readiness checks; never use for public release",
    )
    parser.add_argument("--http-timeout", type=float, default=5.0)
    args = parser.parse_args()
    if not 0 < args.http_timeout <= 30:
        raise SystemExit("--http-timeout must be greater than 0 and at most 30 seconds")

    errors = validate_environment(os.environ, allow_local_http=args.allow_local_http)
    if args.check_http and not errors:
        errors.extend(
            check_http_readiness(
                os.environ["PUBLIC_BASE_URL"],
                timeout_seconds=args.http_timeout,
            )
        )
    if errors:
        print("production preflight FAILED (secret values redacted):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    checks = "configuration + DB"
    if args.check_http:
        checks += " + HTTP live/ready"
    print(f"production preflight PASS: {checks}; secrets were not printed")


if __name__ == "__main__":
    main()
