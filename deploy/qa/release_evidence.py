"""Generate and validate the sanitized human-QA release gate artifact.

The artifact binds the exact engine/data/image/model metadata to the existing
20-question planner smoke and 100-question live E2E reports. It never copies a
question, prompt, plan, answer, product identifier, token, or credential.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = "mirae.qa.release-gate.v1"
HEX_64 = re.compile(r"[a-f0-9]{64}")
SHA256_ID = re.compile(r"sha256:[a-f0-9]{64}")
GIT_SHA = re.compile(r"[a-f0-9]{40}")
HCX_MODEL = re.compile(r"HCX-[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

TOP_LEVEL_KEYS = {
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
GATE_KEYS = {
    "status",
    "total",
    "passed",
    "failed",
    "suite_sha256",
    "report_sha256",
    "verified_at_utc",
}
SANITIZATION_KEYS = {
    "contains_questions",
    "contains_prompts",
    "contains_answers",
    "contains_tokens",
    "contains_credentials",
}

SMOKE_REPORT_KEYS = {
    "status",
    "gate",
    "model_id",
    "approved_planner_stage",
    "completed_at_utc",
    "question_suite_sha256",
    "case_count",
    "provider_call_count",
    "both_stage_valid_count",
    "both_stage_match_count",
    "cases",
    "usage",
    "secret_values_recorded",
    "questions_recorded",
    "plans_recorded",
}
CANARY_REPORT_KEYS = {
    "status",
    "gate",
    "model_id",
    "approved_planner_stage",
    "completed_at_utc",
    "question_suite_sha256",
    "case_count",
    "minimum_accuracy",
    "passed_count",
    "accuracy",
    "hcx_planned_case_count",
    "evidence_linked_case_count",
    "cross_scope_refusal_count",
    "by_kind",
    "secret_values_recorded",
    "questions_recorded",
    "prompts_recorded",
    "plans_recorded",
    "answers_recorded",
    "product_identifiers_recorded",
}


class EvidenceError(ValueError):
    """A release report or artifact failed strict validation."""


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise EvidenceError(f"{label} must be a non-empty file")
    if path.stat().st_size > 2_000_000:
        raise EvidenceError(f"{label} exceeds the 2 MB safety limit")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{label} must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise EvidenceError(f"{label} must contain a JSON object")
    return payload


def _require_exact_keys(payload: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - payload.keys())
    extra = sorted(payload.keys() - expected)
    if missing or extra:
        raise EvidenceError(f"{label} keys differ from schema; missing={missing}, extra={extra}")


def _is_int(value: Any) -> bool:
    return type(value) is int


def _validate_utc(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")):
        raise EvidenceError(f"{label} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{label} must be an ISO-8601 UTC timestamp") from exc
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise EvidenceError(f"{label} must use UTC")
    return value


def _validate_hex(
    value: Any,
    pattern: re.Pattern[str],
    label: str,
    *,
    reject_zero: bool = False,
) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise EvidenceError(f"{label} has an invalid format")
    if reject_zero and set(value.removeprefix("sha256:")) == {"0"}:
        raise EvidenceError(f"{label} cannot be an all-zero placeholder")
    return value


def _validate_hcx_url(value: Any) -> str:
    if not isinstance(value, str) or value != value.rstrip("/"):
        raise EvidenceError("hcx_base_url must be a canonical URL without a trailing slash")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise EvidenceError("hcx_base_url must be a non-secret HTTPS URL")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_artifact_sha256(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("artifact_sha256", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_usage(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {"stage_one", "stage_two"}:
        raise EvidenceError("20-question usage must contain stage_one and stage_two")
    for stage, values in payload.items():
        if not isinstance(values, dict):
            raise EvidenceError(f"20-question usage.{stage} must be an object")
        for key, value in values.items():
            if not isinstance(key, str) or re.fullmatch(r"[a-z0-9_]+", key) is None:
                raise EvidenceError(f"20-question usage.{stage} has an invalid metric key")
            if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
                raise EvidenceError(f"20-question usage.{stage}.{key} must be non-negative")


def _validate_smoke_report(
    payload: dict[str, Any], *, model_id: str, report_path: Path
) -> dict[str, Any]:
    _require_exact_keys(payload, SMOKE_REPORT_KEYS, "20-question report")
    required = {
        "status": "PASS",
        "gate": "HCX_20_QUESTION_ONE_VS_TWO_STAGE",
        "model_id": model_id,
        "approved_planner_stage": "two",
        "case_count": 20,
        "provider_call_count": 40,
        "both_stage_valid_count": 20,
        "both_stage_match_count": 20,
        "secret_values_recorded": False,
        "questions_recorded": False,
        "plans_recorded": False,
    }
    if any(payload.get(key) != value for key, value in required.items()):
        raise EvidenceError("20-question report did not pass its strict sanitized gate")
    suite_hash = _validate_hex(
        payload["question_suite_sha256"],
        HEX_64,
        "20-question suite hash",
        reject_zero=True,
    )
    verified_at = _validate_utc(payload["completed_at_utc"], "20-question completion")
    cases = payload["cases"]
    if not isinstance(cases, list) or len(cases) != 20:
        raise EvidenceError("20-question report must contain 20 sanitized case summaries")
    case_ids: set[str] = set()
    for item in cases:
        if not isinstance(item, dict):
            raise EvidenceError("20-question case summaries must be objects")
        _require_exact_keys(
            item,
            {"case_id", "stage_one_valid", "stage_two_valid", "canonical_match"},
            "20-question case summary",
        )
        case_id = item["case_id"]
        if not isinstance(case_id, str) or re.fullmatch(r"LIVE-[0-9]{2}", case_id) is None:
            raise EvidenceError("20-question case_id has an invalid format")
        if case_id in case_ids:
            raise EvidenceError("20-question report contains a duplicate case_id")
        case_ids.add(case_id)
        if any(item[key] is not True for key in ("stage_one_valid", "stage_two_valid", "canonical_match")):
            raise EvidenceError("20-question case summary contains a failed case")
    _validate_usage(payload["usage"])
    return {
        "status": "PASS",
        "total": 20,
        "passed": 20,
        "failed": 0,
        "suite_sha256": suite_hash,
        "report_sha256": _sha256_file(report_path),
        "verified_at_utc": verified_at,
    }


def _validate_canary_report(
    payload: dict[str, Any], *, model_id: str, report_path: Path
) -> dict[str, Any]:
    _require_exact_keys(payload, CANARY_REPORT_KEYS, "100-question report")
    required = {
        "status": "PASS",
        "gate": "HCX_100_QUESTION_TWO_STAGE_E2E",
        "model_id": model_id,
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
    if any(payload.get(key) != value for key, value in required.items()):
        raise EvidenceError("100-question report did not pass its strict sanitized gate")
    passed = payload["passed_count"]
    accuracy = payload["accuracy"]
    if (
        not _is_int(passed)
        or not 98 <= passed <= 100
        or not isinstance(accuracy, int | float)
        or isinstance(accuracy, bool)
        or float(accuracy) < 0.98
        or round(passed / 100, 4) != float(accuracy)
    ):
        raise EvidenceError("100-question report did not meet its accuracy gate")
    expected_mix = {
        "rank_single": 35,
        "filter_search": 25,
        "count_aggregate": 20,
        "cross_scope": 20,
    }
    by_kind = payload["by_kind"]
    if not isinstance(by_kind, dict) or set(by_kind) != set(expected_mix):
        raise EvidenceError("100-question by_kind categories differ from the frozen suite")
    for kind, total in expected_mix.items():
        item = by_kind[kind]
        if not isinstance(item, dict) or set(item) != {"total", "passed"}:
            raise EvidenceError(f"100-question by_kind.{kind} has an invalid schema")
        if item["total"] != total or not _is_int(item["passed"]):
            raise EvidenceError(f"100-question by_kind.{kind} has invalid counts")
        if not 0 <= item["passed"] <= total:
            raise EvidenceError(f"100-question by_kind.{kind} passed count is out of range")
    if sum(by_kind[kind]["passed"] for kind in expected_mix) != passed:
        raise EvidenceError("100-question by_kind passed counts do not reconcile")
    suite_hash = _validate_hex(
        payload["question_suite_sha256"],
        HEX_64,
        "100-question suite hash",
        reject_zero=True,
    )
    verified_at = _validate_utc(payload["completed_at_utc"], "100-question completion")
    return {
        "status": "PASS",
        "total": 100,
        "passed": passed,
        "failed": 100 - passed,
        "suite_sha256": suite_hash,
        "report_sha256": _sha256_file(report_path),
        "verified_at_utc": verified_at,
    }


def build_artifact(
    *,
    smoke_report_path: Path,
    canary_report_path: Path,
    engine_git_sha: str,
    engine_image_digest: str,
    data_hash: str,
    hcx_model_id: str,
    hcx_base_url: str,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    _validate_hex(engine_git_sha, GIT_SHA, "engine_git_sha", reject_zero=True)
    _validate_hex(
        engine_image_digest,
        SHA256_ID,
        "engine_image_digest",
        reject_zero=True,
    )
    _validate_hex(data_hash, SHA256_ID, "data_hash", reject_zero=True)
    _validate_hex(hcx_model_id, HCX_MODEL, "hcx_model_id")
    _validate_hcx_url(hcx_base_url)
    smoke_payload = _load_json(smoke_report_path, label="20-question report")
    canary_payload = _load_json(canary_report_path, label="100-question report")
    smoke_gate = _validate_smoke_report(
        smoke_payload, model_id=hcx_model_id, report_path=smoke_report_path
    )
    canary_gate = _validate_canary_report(
        canary_payload, model_id=hcx_model_id, report_path=canary_report_path
    )
    generated = generated_at_utc or datetime.now(UTC).isoformat()
    _validate_utc(generated, "generated_at_utc")
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated,
        "engine_git_sha": engine_git_sha,
        "engine_image_digest": engine_image_digest,
        "data_hash": data_hash,
        "hcx_model_id": hcx_model_id,
        "hcx_base_url": hcx_base_url,
        "planner_stage": "two",
        "gates": {"smoke_20": smoke_gate, "canary_100": canary_gate},
        "sanitization": {key: False for key in sorted(SANITIZATION_KEYS)},
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    validate_artifact(artifact)
    return artifact


def validate_artifact(
    payload: dict[str, Any],
    *,
    expected_engine_git_sha: str | None = None,
    expected_engine_image_digest: str | None = None,
    expected_data_hash: str | None = None,
    expected_hcx_model_id: str | None = None,
    expected_hcx_base_url: str | None = None,
) -> None:
    _require_exact_keys(payload, TOP_LEVEL_KEYS, "QA release gate artifact")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise EvidenceError("unsupported QA release gate schema_version")
    _validate_utc(payload["generated_at_utc"], "generated_at_utc")
    _validate_hex(
        payload["engine_git_sha"], GIT_SHA, "engine_git_sha", reject_zero=True
    )
    _validate_hex(
        payload["engine_image_digest"],
        SHA256_ID,
        "engine_image_digest",
        reject_zero=True,
    )
    _validate_hex(payload["data_hash"], SHA256_ID, "data_hash", reject_zero=True)
    _validate_hex(payload["hcx_model_id"], HCX_MODEL, "hcx_model_id")
    _validate_hcx_url(payload["hcx_base_url"])
    if payload["planner_stage"] != "two":
        raise EvidenceError("planner_stage must be two")
    sanitization = payload["sanitization"]
    if not isinstance(sanitization, dict):
        raise EvidenceError("sanitization must be an object")
    _require_exact_keys(sanitization, SANITIZATION_KEYS, "sanitization")
    if any(value is not False for value in sanitization.values()):
        raise EvidenceError("QA release gate artifact is not sanitized")
    gates = payload["gates"]
    if not isinstance(gates, dict) or set(gates) != {"smoke_20", "canary_100"}:
        raise EvidenceError("gates must contain smoke_20 and canary_100")
    for name, total, minimum_passed in (("smoke_20", 20, 20), ("canary_100", 100, 98)):
        gate = gates[name]
        if not isinstance(gate, dict):
            raise EvidenceError(f"{name} must be an object")
        _require_exact_keys(gate, GATE_KEYS, name)
        if gate["status"] != "PASS" or gate["total"] != total:
            raise EvidenceError(f"{name} did not pass")
        passed = gate["passed"]
        failed = gate["failed"]
        if (
            not _is_int(passed)
            or not _is_int(failed)
            or passed < minimum_passed
            or passed > total
            or failed != total - passed
        ):
            raise EvidenceError(f"{name} counts do not reconcile")
        _validate_hex(
            gate["suite_sha256"],
            HEX_64,
            f"{name}.suite_sha256",
            reject_zero=True,
        )
        _validate_hex(
            gate["report_sha256"],
            HEX_64,
            f"{name}.report_sha256",
            reject_zero=True,
        )
        _validate_utc(gate["verified_at_utc"], f"{name}.verified_at_utc")
    artifact_digest = _validate_hex(
        payload["artifact_sha256"],
        HEX_64,
        "artifact_sha256",
        reject_zero=True,
    )
    if artifact_digest != canonical_artifact_sha256(payload):
        raise EvidenceError("artifact_sha256 does not match canonical artifact content")
    expectations = {
        "engine_git_sha": expected_engine_git_sha,
        "engine_image_digest": expected_engine_image_digest,
        "data_hash": expected_data_hash,
        "hcx_model_id": expected_hcx_model_id,
        "hcx_base_url": expected_hcx_base_url,
    }
    for key, expected in expectations.items():
        if expected is not None and payload[key] != expected:
            raise EvidenceError(f"{key} does not match the expected deployment value")


def load_and_validate_artifact(
    path: Path,
    **expectations: str | None,
) -> dict[str, Any]:
    payload = _load_json(path, label="QA release gate artifact")
    validate_artifact(payload, **expectations)
    return payload


def _write_artifact(path: Path, payload: dict[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise EvidenceError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="validate live reports and bind them")
    generate.add_argument("--smoke-report", type=Path, required=True)
    generate.add_argument("--canary-report", type=Path, required=True)
    generate.add_argument("--engine-git-sha", required=True)
    generate.add_argument("--engine-image-digest", required=True)
    generate.add_argument("--data-hash", required=True)
    generate.add_argument("--hcx-model-id", required=True)
    generate.add_argument("--hcx-base-url", required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--force", action="store_true")

    validate = subparsers.add_parser("validate", help="validate an existing bound artifact")
    validate.add_argument("artifact", type=Path)
    validate.add_argument("--expect-engine-git-sha")
    validate.add_argument("--expect-engine-image-digest")
    validate.add_argument("--expect-data-hash")
    validate.add_argument("--expect-hcx-model-id")
    validate.add_argument("--expect-hcx-base-url")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate":
            payload = build_artifact(
                smoke_report_path=args.smoke_report,
                canary_report_path=args.canary_report,
                engine_git_sha=args.engine_git_sha,
                engine_image_digest=args.engine_image_digest,
                data_hash=args.data_hash,
                hcx_model_id=args.hcx_model_id,
                hcx_base_url=args.hcx_base_url,
            )
            _write_artifact(args.output, payload, force=args.force)
            artifact_path = args.output
        else:
            payload = load_and_validate_artifact(
                args.artifact,
                expected_engine_git_sha=args.expect_engine_git_sha,
                expected_engine_image_digest=args.expect_engine_image_digest,
                expected_data_hash=args.expect_data_hash,
                expected_hcx_model_id=args.expect_hcx_model_id,
                expected_hcx_base_url=args.expect_hcx_base_url,
            )
            artifact_path = args.artifact
    except EvidenceError as exc:
        print(f"QA_RELEASE_GATE_FAIL: {exc}", file=sys.stderr)
        return 2
    summary = {
        "status": "PASS",
        "artifact": str(artifact_path.resolve()),
        "artifact_sha256": payload["artifact_sha256"],
        "engine_git_sha": payload["engine_git_sha"],
        "engine_image_digest": payload["engine_image_digest"],
        "data_hash": payload["data_hash"],
        "hcx_model_id": payload["hcx_model_id"],
        "smoke_20": payload["gates"]["smoke_20"]["status"],
        "canary_100": payload["gates"]["canary_100"]["status"],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
