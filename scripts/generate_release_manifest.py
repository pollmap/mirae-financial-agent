#!/usr/bin/env python3
"""Generate a reproducibility/freeze manifest without recording secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.execution.engine import DuckDBEngine  # noqa: E402
from app.planner.schema import HCX_QUERY_PLAN_SCHEMA, QUERY_PLANNER_SYSTEM_PROMPT  # noqa: E402

ZERO_40 = "0" * 40
ZERO_64 = "0" * 64
GIT_SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
FINAL_REPORT_STATUSES = {"PASS", "RELEASE_READY", "ALL_GATES_PASS"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_test_report(path: Path) -> tuple[dict[str, object], tuple[int, int, int]]:
    """Parse the machine report and return its authoritative pytest counts."""

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"--test-report is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise SystemExit("--test-report root must be a JSON object")
    summary = report.get("pytest_summary")
    if not isinstance(summary, dict):
        raise SystemExit("--test-report must contain a pytest_summary object")
    try:
        counts = (
            int(summary["passed"]),
            int(summary["failed"]),
            int(summary.get("skipped", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            "--test-report pytest_summary requires integer passed/failed/skipped"
        ) from exc
    if any(value < 0 for value in counts):
        raise SystemExit("--test-report pytest counts cannot be negative")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise SystemExit("--test-report must contain a non-empty checks array")
    failed_checks = [
        str(item.get("command", item.get("name", "unknown")))
        for item in checks
        if not isinstance(item, dict) or item.get("status") != "PASS"
    ]
    if failed_checks:
        raise SystemExit(
            "--test-report contains a non-PASS executed check: " + ", ".join(failed_checks)
        )
    return report, counts


def _verify_git_head(expected_sha: str) -> None:
    try:
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("--final requires an accessible Git repository and HEAD") from exc
    if actual != expected_sha:
        raise SystemExit(f"--git-sha does not match repository HEAD ({actual})")


def _verify_database(path: Path) -> None:
    readiness_error = DuckDBEngine(path).readiness_error()
    if readiness_error is not None:
        raise SystemExit(f"--serving-database failed readiness validation: {readiness_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "release_manifest.generated.json")
    parser.add_argument("--release-id", default="prebrief-mvp-0.2.0")
    parser.add_argument("--freeze-at", default="2026-09-05T00:00:00+09:00")
    parser.add_argument("--git-sha", default=ZERO_40)
    parser.add_argument("--image-digest", default=f"sha256:{ZERO_64}")
    parser.add_argument("--passed", type=int, default=0)
    parser.add_argument("--failed", type=int, default=0)
    parser.add_argument("--skipped", type=int, default=0)
    parser.add_argument("--test-report", type=Path)
    parser.add_argument(
        "--image-ref",
        help=(
            "Immutable registry reference in name@sha256:<digest> form. Required for FINAL "
            "and cross-checked against --image-digest. The runbook independently verifies it."
        ),
    )
    parser.add_argument(
        "--serving-database",
        type=Path,
        default=ROOT / "data" / "serving" / "mirae_agent.duckdb",
        help=(
            "Database artifact to hash. For a FINAL manifest, pass the database "
            "copied out of the immutable container image rather than the host build."
        ),
    )
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    if not GIT_SHA_PATTERN.fullmatch(args.git_sha):
        raise SystemExit("--git-sha must be exactly 40 lowercase hexadecimal characters")
    if not IMAGE_DIGEST_PATTERN.fullmatch(args.image_digest):
        raise SystemExit("--image-digest must match sha256:<64 lowercase hexadecimal characters>")
    if args.final and (args.git_sha == ZERO_40 or args.image_digest == f"sha256:{ZERO_64}"):
        raise SystemExit("--final requires non-placeholder --git-sha and --image-digest")
    if args.failed:
        raise SystemExit("release manifest cannot be generated with failed tests")
    if args.test_report and not args.test_report.is_file():
        raise SystemExit("--test-report does not exist")
    if not args.serving_database.is_file() or args.serving_database.stat().st_size == 0:
        raise SystemExit("--serving-database must be a non-empty file")
    if args.final and (args.test_report is None or args.passed <= 0):
        raise SystemExit("--final requires --test-report and --passed greater than zero")
    if args.final and (
        not args.image_ref or not args.image_ref.endswith(f"@{args.image_digest}")
    ):
        raise SystemExit(
            "--final requires --image-ref ending in @<the exact --image-digest>"
        )

    report: dict[str, object] | None = None
    if args.test_report:
        report, report_counts = _load_test_report(args.test_report)
        cli_counts = (args.passed, args.failed, args.skipped)
        if cli_counts != report_counts:
            raise SystemExit(
                "CLI test counts do not match --test-report pytest_summary: "
                f"cli={cli_counts}, report={report_counts}"
            )
    if args.final:
        assert report is not None
        if report.get("status") not in FINAL_REPORT_STATUSES:
            raise SystemExit(
                "--final requires test-report status PASS, RELEASE_READY, or ALL_GATES_PASS"
            )
        external_gates = report.get("external_gates")
        if not isinstance(external_gates, list) or not external_gates:
            raise SystemExit("--final requires a non-empty external_gates array")
        if any(
            not isinstance(item, dict) or item.get("status") != "PASS"
            for item in external_gates
        ):
            raise SystemExit("--final requires every external gate to have status PASS")
        _verify_git_head(args.git_sha)

    _verify_database(args.serving_database)

    safe_config = {
        "app_env": os.getenv("APP_ENV", "production").strip().lower(),
        "planner_mode": os.getenv("PLANNER_MODE", "hcx").strip().lower(),
        "hcx_model_id": os.getenv("HCX_MODEL_ID", "HCX-007").strip(),
        "hcx_base_url": os.getenv(
            "HCX_BASE_URL", "https://clovastudio.stream.ntruss.com"
        ).rstrip("/"),
        "hcx_timeout_seconds": float(os.getenv("HCX_TIMEOUT_SECONDS", "12")),
        "hcx_total_deadline_seconds": float(os.getenv("HCX_TOTAL_DEADLINE_SECONDS", "25")),
        "hcx_max_retries": int(os.getenv("HCX_MAX_RETRIES", "2")),
        "hcx_max_concurrency": int(os.getenv("HCX_MAX_CONCURRENCY", "3")),
        "hcx_qpm_limit": int(os.getenv("HCX_QPM_LIMIT", "60")),
        "hcx_tpm_budget": int(os.getenv("HCX_TPM_BUDGET", "60000")),
        "monthly_cost_cap_krw": int(os.getenv("MONTHLY_COST_CAP_KRW", "50000")),
        "db_max_concurrency": int(os.getenv("DB_MAX_CONCURRENCY", "8")),
        "web_concurrency": int(os.getenv("WEB_CONCURRENCY", "1")),
        "access_log_enabled": os.getenv("ACCESS_LOG_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        "question_max_chars": int(os.getenv("QUESTION_MAX_CHARS", "2000")),
        "default_result_limit": int(os.getenv("DEFAULT_RESULT_LIMIT", "10")),
        "max_result_limit": int(os.getenv("MAX_RESULT_LIMIT", "50")),
        "clarification_state_enabled": os.getenv(
            "ENABLE_CLARIFICATION_STATE", "true"
        ).strip().lower()
        in {"1", "true", "yes", "on"},
        "secret_values_recorded": False,
    }
    if args.final and (
        safe_config["app_env"] != "production"
        or safe_config["planner_mode"] != "hcx"
        or safe_config["hcx_model_id"] != "HCX-007"
        or safe_config["hcx_base_url"] != "https://clovastudio.stream.ntruss.com"
    ):
        raise SystemExit("--final requires the approved production HCX configuration")
    config_digest = hashlib.sha256(
        json.dumps(safe_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    report_hash = sha256(args.test_report) if args.test_report else ZERO_64
    manifest = {
        "release_id": args.release_id,
        "release_status": "FINAL" if args.final else "DRAFT",
        "built_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        "freeze_at": args.freeze_at,
        "git_sha": args.git_sha,
        "container_image_digest": args.image_digest,
        "source_sha256": {
            "official_task_pdf": sha256(ROOT / "inputs" / "official_task.pdf"),
            "official_data_zip": sha256(ROOT / "inputs" / "official_data.zip"),
        },
        "data_model_version": "canonical-v1",
        "etl_version": "etl-v1",
        "serving_database_sha256": sha256(args.serving_database),
        "metric_registry_sha256": sha256(ROOT / "registry" / "metric_policy_v1.csv"),
        "canonical_registry_sha256": sha256(ROOT / "registry" / "canonical_fields_v1.csv"),
        "query_plan_schema_sha256": sha256(ROOT / "contracts" / "query-plan-v1.schema.json"),
        "planner_prompt_sha256": sha256_text(QUERY_PLANNER_SYSTEM_PROMPT),
        "hcx_runtime_schema_sha256": sha256_text(
            json.dumps(HCX_QUERY_PLAN_SCHEMA, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
        "evidence_bundle_schema_sha256": sha256(
            ROOT / "contracts" / "evidence-bundle-v1.schema.json"
        ),
        "etl_source_sha256": sha256_files(
            [ROOT / "etl" / "build.py", ROOT / "etl" / "source.py"]
        ),
        "application_source_sha256": sha256_files(list((ROOT / "app").rglob("*.py"))),
        "runtime_lock_sha256": sha256(ROOT / "requirements-runtime.lock"),
        "build_lock_sha256": sha256(ROOT / "requirements-build.lock"),
        "renderer_sha256": sha256(ROOT / "app" / "rendering.py"),
        "safety_policy_sha256": sha256(ROOT / "app" / "safety.py"),
        "prompt_version": "hcx-query-planner-v1.2",
        "renderer_version": "evidence-renderer-v1.2",
        "safety_policy_version": "financial-safety-v1.2",
        "hcx_model_id": str(safe_config["hcx_model_id"]),
        "config_digest": f"sha256:{config_digest}",
        "test_summary": {
            "passed": args.passed,
            "failed": args.failed,
            "skipped": args.skipped,
            "report_sha256": report_hash,
        },
        "post_freeze_policy": "NO_COMMIT_PUSH_DEPLOY_OR_ARTIFACT_CHANGES",
    }
    schema = json.loads(
        (ROOT / "contracts" / "release-manifest.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(manifest, schema, format_checker=jsonschema.FormatChecker())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(args.output), "final": args.final}, indent=2))


if __name__ == "__main__":
    main()
