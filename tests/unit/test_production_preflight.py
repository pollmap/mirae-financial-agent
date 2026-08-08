from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.production_preflight import check_http_readiness, validate_environment

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"
LIVE_GATE_REPORT = ROOT / "tests" / "fixtures" / "live_hcx_gate_pass.json"


def _valid_environment() -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "PLANNER_MODE": "hcx",
        "PLANNER_STAGE": "two",
        "MIRAE_DATABASE_PATH": str(DATABASE),
        "LIVE_HCX_GATE_REPORT": str(LIVE_GATE_REPORT),
        "MIRAE_IMAGE": "registry.contest-team.kr/mirae-agent@sha256:" + "a" * 64,
        "PUBLIC_BASE_URL": "https://agent.contest-team.kr",
        "CLOVA_STUDIO_API_KEY": "ck_live_" + "a" * 32,
        "HCX_MODEL_ID": "HCX-007",
        "HCX_BASE_URL": "https://clovastudio.stream.ntruss.com",
        "HCX_TIMEOUT_SECONDS": "12",
        "HCX_TOTAL_DEADLINE_SECONDS": "25",
        "HCX_MAX_RETRIES": "2",
        "HCX_MAX_CONCURRENCY": "3",
        "HCX_QPM_LIMIT": "30",
        "HCX_TPM_BUDGET": "60000",
        "MONTHLY_COST_CAP_KRW": "50000",
        "DB_MAX_CONCURRENCY": "8",
        "CLARIFICATION_SIGNING_KEY": "signing_" + "b" * 32,
        "ENABLE_CLARIFICATION_STATE": "true",
        "WEB_CONCURRENCY": "1",
        "ACCESS_LOG_ENABLED": "false",
        "READINESS_PATH": "/health/ready",
        "LIVENESS_PATH": "/health/live",
        "PUBLIC_ENDPOINT_PATH": "/answer",
        "PUBLIC_ENDPOINT_METHOD": "GET",
    }


def test_valid_production_environment_and_database_pass() -> None:
    assert validate_environment(_valid_environment()) == []


def test_preflight_rejects_placeholders_wrong_hcx_contract_and_unsafe_process_config() -> None:
    environ = _valid_environment()
    environ.update(
        {
            "CLOVA_STUDIO_API_KEY": "replace-with-real-secret",
            "CLARIFICATION_SIGNING_KEY": "development-only-rotate-before-production",
            "HCX_MODEL_ID": "another-model",
            "HCX_BASE_URL": "https://example.com",
            "PLANNER_STAGE": "one",
            "PUBLIC_BASE_URL": "https://agent.example.com",
            "MIRAE_IMAGE": "registry.example.com/agent@sha256:" + "f" * 64,
            "WEB_CONCURRENCY": "2",
            "ACCESS_LOG_ENABLED": "true",
        }
    )

    errors = validate_environment(environ)

    assert any("CLOVA_STUDIO_API_KEY must not be a placeholder" in error for error in errors)
    assert any("CLARIFICATION_SIGNING_KEY must not be a placeholder" in error for error in errors)
    assert any("HCX_MODEL_ID" in error for error in errors)
    assert any("HCX_BASE_URL" in error for error in errors)
    assert any("PLANNER_STAGE" in error for error in errors)
    assert any("PUBLIC_BASE_URL must not use a placeholder" in error for error in errors)
    assert any("MIRAE_IMAGE" in error for error in errors)
    assert any("WEB_CONCURRENCY" in error for error in errors)
    assert any("ACCESS_LOG_ENABLED" in error for error in errors)


def test_preflight_rejects_short_or_reused_secrets_without_disclosing_them() -> None:
    short = _valid_environment()
    short["CLOVA_STUDIO_API_KEY"] = "a" * 19
    short["CLARIFICATION_SIGNING_KEY"] = "b" * 23
    short_errors = validate_environment(short)
    assert any("CLOVA_STUDIO_API_KEY must contain at least 20" in error for error in short_errors)
    assert any(
        "CLARIFICATION_SIGNING_KEY must contain at least 24" in error for error in short_errors
    )

    reused = _valid_environment()
    reused["CLOVA_STUDIO_API_KEY"] = reused["CLARIFICATION_SIGNING_KEY"]
    assert "HCX and clarification secrets must be distinct" in validate_environment(reused)


def test_preflight_validates_optional_caddy_hostname_and_digest_as_one_set() -> None:
    environ = _valid_environment()
    environ["PUBLIC_HOSTNAME"] = "different.contest-team.kr"
    environ["CADDY_IMAGE"] = "registry.contest-team.kr/caddy@sha256:" + "0" * 64
    errors = validate_environment(environ)
    assert any("CADDY_IMAGE" in error for error in errors)
    assert any("PUBLIC_HOSTNAME must match" in error for error in errors)

    environ["PUBLIC_HOSTNAME"] = "agent.contest-team.kr"
    environ["CADDY_IMAGE"] = "registry.contest-team.kr/caddy@sha256:" + "c" * 64
    assert validate_environment(environ) == []


def test_preflight_rejects_unready_database_and_invalid_budgets(tmp_path: Path) -> None:
    database = tmp_path / "invalid.duckdb"
    database.write_bytes(b"not a valid database")
    environ = _valid_environment()
    environ.update(
        {
            "MIRAE_DATABASE_PATH": str(database),
            "HCX_QPM_LIMIT": "0",
            "HCX_TPM_BUDGET": "unlimited",
            "MONTHLY_COST_CAP_KRW": "0",
        }
    )

    errors = validate_environment(environ)

    assert any("readiness validation" in error for error in errors)
    assert any("HCX_QPM_LIMIT" in error for error in errors)
    assert any("HCX_TPM_BUDGET" in error for error in errors)
    assert any("MONTHLY_COST_CAP_KRW" in error for error in errors)


def test_preflight_rejects_missing_or_failed_live_hcx_gate(tmp_path: Path) -> None:
    missing = _valid_environment()
    missing["LIVE_HCX_GATE_REPORT"] = str(tmp_path / "missing.json")
    assert any("non-empty JSON" in error for error in validate_environment(missing))

    failed_report = tmp_path / "failed.json"
    payload = json.loads(LIVE_GATE_REPORT.read_text(encoding="utf-8"))
    payload["both_stage_match_count"] = 19
    failed_report.write_text(json.dumps(payload), encoding="utf-8")
    failed = _valid_environment()
    failed["LIVE_HCX_GATE_REPORT"] = str(failed_report)
    assert any("20-question A/B gate" in error for error in validate_environment(failed))


def test_cli_never_echoes_a_rejected_secret() -> None:
    environ = {**os.environ, **_valid_environment()}
    rejected_secret = "shh-private-value"
    environ["CLOVA_STUDIO_API_KEY"] = rejected_secret
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "production_preflight.py")],
        cwd=ROOT,
        env=environ,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert rejected_secret not in result.stdout
    assert rejected_secret not in result.stderr
    assert "secret values redacted" in result.stderr


@contextmanager
def _health_server(*, ready: bool = True) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health/live":
                payload = {"status": "ok"}
                status = 200
            elif self.path == "/health/ready" and ready:
                payload = {"status": "ready", "data_snapshot_date": "2026-07-11"}
                status = 200
            else:
                payload = {"status": "not_ready"}
                status = 503
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_readiness_contract_passes_and_fails_closed() -> None:
    with _health_server() as base_url:
        assert check_http_readiness(base_url) == []
    with _health_server(ready=False) as base_url:
        errors = check_http_readiness(base_url)
    assert errors == ["/health/ready HTTP check failed"]
