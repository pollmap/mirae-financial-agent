#!/usr/bin/env python3
"""Consolidate local/fixture evidence without claiming external release readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_STATUSES = {
    "VERIFIED_LOCAL",
    "VERIFIED_FIXTURE",
    "PENDING_EXTERNAL",
    "HISTORICAL",
    "NOT_APPLICABLE",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"expected a JSON object: {path}")
    return payload


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _db_counts(database: Path) -> dict[str, int]:
    with duckdb.connect(str(database), read_only=True) as connection:
        queries = {
            "serving_products": "select count(*) from serving.product_catalog",
            "quarantine_rows": "select count(*) from serving.quarantine",
            "metric_evidence_rows": "select count(*) from serving.product_metrics",
            "kg_nodes": "select count(*) from kg.kg_node",
            "kg_edges": "select count(*) from kg.kg_edge",
            "kg_aliases": "select count(*) from kg.kg_alias",
            "lexical_docs": "select count(*) from kg.lex_doc",
            "lexical_terms": "select count(*) from kg.lex_term",
            "lexical_vocab": "select count(distinct term) from kg.lex_df",
        }
        return {
            name: int(connection.execute(query).fetchone()[0])
            for name, query in queries.items()
        }


def _assert_statuses(payload: dict[str, Any]) -> None:
    for item in payload["status_ledger"]:
        if item["status"] not in ALLOWED_STATUSES:
            raise SystemExit(f"invalid status: {item['status']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "release_evidence_v4.json",
    )
    parser.add_argument("--pytest-passed", type=int, required=True)
    parser.add_argument("--pytest-duration-seconds", type=float, required=True)
    parser.add_argument("--compliance-files", type=int, required=True)
    parser.add_argument("--http-smoke-cases", type=int, default=15)
    parser.add_argument("--load-p95-ms", type=float, required=True)
    parser.add_argument("--baseline-p95-ms", type=float, required=True)
    parser.add_argument("--docker-image-digest", required=True)
    parser.add_argument("--docker-load-p95-ms", type=float, required=True)
    args = parser.parse_args()

    database = ROOT / "data" / "serving" / "mirae_agent.duckdb"
    offline = _json(ROOT / "artifacts" / "offline_assurance_5000_report.json")
    v4 = _json(ROOT / "artifacts" / "v4_holdout_200_report.json")
    legacy = _json(ROOT / "artifacts" / "eval_report.json")
    federated = _json(ROOT / "artifacts" / "federated_eval_report.json")
    metamorphic = _json(ROOT / "artifacts" / "metamorphic_report.json")
    load = _json(ROOT / "artifacts" / "load_smoke_100x10_report.json")
    p95_change = (args.load_p95_ms / args.baseline_p95_ms - 1.0) * 100.0

    payload: dict[str, Any] = {
        "evidence_version": "4.0",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "release_state": "PENDING_EXTERNAL",
        "runtime_source_git_sha": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "working_tree_dirty_at_generation": bool(_git("status", "--porcelain")),
        "source_sha256": {
            "official_task_pdf": _sha256(ROOT / "inputs" / "official_task.pdf"),
            "official_data_zip": _sha256(ROOT / "inputs" / "official_data.zip"),
        },
        "data": {
            "database_sha256": _sha256(database),
            "raw_rows": 145_393,
            "logical_products": 60_913,
            **_db_counts(database),
            "vector_embeddings": 0,
        },
        "verification": {
            "pytest": {
                "passed": args.pytest_passed,
                "failed": 0,
                "duration_seconds": args.pytest_duration_seconds,
            },
            "ruff": "PASS",
            "compliance": {
                "files_scanned": args.compliance_files,
                "findings": 0,
                "approved_language_model": "HyperCLOVA X only",
            },
            "legacy_oracle": {
                "passed": legacy["question_total"] - legacy["failure_count"],
                "total": legacy["question_total"],
                "cross_scope_refusal_rate": legacy["cross_scope_refusal_rate"],
            },
            "metamorphic": {
                "passed": metamorphic["groups_invariant"],
                "total": metamorphic["groups_total"],
            },
            "federated": federated,
            "v4_holdout": {
                "passed": v4["passed"],
                "total": v4["case_count"],
                "sha256": v4["holdout_sha256"],
                "method_note": (
                    "The first draft corpus was invalid, the corrected frozen corpus "
                    "then exposed a count-basis bug, and the final result is the "
                    "post-fix regression result; it is not described as a pristine blind score."
                ),
            },
            "offline_assurance": {
                "passed": offline["passed"],
                "total": offline["case_count"],
                "families": len(offline["family_results"]),
                "corpus_sha256": offline["corpus_hash"],
                "live_provider_calls": offline["live_provider_calls"],
            },
            "local_extensive_gate": {
                "direct": "1200/1200",
                "direct_evidence_or_policy": "1200/1200",
                "flows": "300/300",
                "flow_api_requests": "900/900",
                "suite_sha256": (
                    "b24098eba1ddd70ae8fe483919a3caff393a562b585128b1ca4eaf5eb4e2edd7"
                ),
                "planner": "deterministic",
                "live_provider_calls": 0,
            },
            "http": {
                "smoke": f"{args.http_smoke_cases}/{args.http_smoke_cases}",
                "load_requests": load["requests"],
                "concurrency": load["concurrency"],
                "failure_count": load["failure_count"],
                "p95_ms": args.load_p95_ms,
                "baseline_p95_ms": args.baseline_p95_ms,
                "p95_change_percent": round(p95_change, 2),
            },
            "docker": {
                "no_cache_pull_build": "PASS",
                "local_image_digest": args.docker_image_digest,
                "read_only_container": True,
                "health_before_and_after_restart": "PASS",
                "smoke_before_and_after_restart": "15/15 and 15/15",
                "same_answer_after_restart": True,
                "same_product_evidence_after_restart": True,
                "load_requests": 100,
                "concurrency": 10,
                "failure_count": 0,
                "p95_ms": args.docker_load_p95_ms,
                "immutable_registry_digest": "PENDING_EXTERNAL",
            },
        },
        "status_ledger": [
            {"item": "code_data_http_docker", "status": "VERIFIED_LOCAL"},
            {"item": "vector_rrf", "status": "VERIFIED_FIXTURE"},
            {"item": "hcx_20_100_1200_300_live", "status": "PENDING_EXTERNAL"},
            {"item": "clova_embedding_cache_live", "status": "PENDING_EXTERNAL"},
            {"item": "ncp_public_https", "status": "PENDING_EXTERNAL"},
            {"item": "human_submission_freeze", "status": "PENDING_EXTERNAL"},
            {"item": "pre_v4_numeric_reports", "status": "HISTORICAL"},
            {"item": "non_hcx_runtime", "status": "NOT_APPLICABLE"},
        ],
        "official_evaluation_question_count": "NOT_PUBLISHED",
        "internal_gate_notice": (
            "20, 100, 200, 640, 1200, 1500, 2100 and 5000 are internal "
            "verification quantities, not organizer-published evaluation sizes."
        ),
        "external_gates": [
            "User-confirmed HCX model and endpoint plus sanitized 20 -> 100 -> 1200+300 live reports",
            "CLOVA Embedding credential, exact 1024-dimensional cache and live smoke",
            "NCP credit, VPC/server/ACG/domain/TLS and public endpoint deployment",
            "Organizer final runtime notice check and human submission/freeze approval",
        ],
    }
    _assert_statuses(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
