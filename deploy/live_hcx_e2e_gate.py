#!/usr/bin/env python3
"""Run the credential-gated 100-case HyperCLOVA X two-stage E2E gate.

This is intentionally stricter than ``live_hcx_plan_smoke.py``.  It sends a
stratified, non-private subset of the local 640-question suite through the
actual HCX two-stage planner, then scores the resulting evidence against the
independent SQL oracle.  The persisted report contains only counts, category
totals, and a suite digest: never questions, prompts, plans, answers, product
identifiers, or secret/header values.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.execution.engine import DuckDBEngine  # noqa: E402
from app.planner.service import build_planner  # noqa: E402
from app.service import AgentService  # noqa: E402
from eval.oracle import Oracle  # noqa: E402
from eval.run_eval import fill_runtime_slots, score_response  # noqa: E402
from eval.templates import generate  # noqa: E402

DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"
OFFICIAL_BASE_URL = "https://clovastudio.stream.ntruss.com"
APPROVED_MODEL_ID = "HCX-007"
CASE_TARGETS = {
    "rank_single": 35,
    "filter_search": 25,
    "count_aggregate": 20,
    "cross_scope": 20,
}
QUESTION_COUNT = sum(CASE_TARGETS.values())
MINIMUM_ACCURACY = 0.98


def _select_cases(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select a deterministic, interleaved 100-case semantic-planning suite."""

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        kind = str(question["kind"])
        if kind in CASE_TARGETS:
            buckets[kind].append(question)
    if any(len(buckets[kind]) < target for kind, target in CASE_TARGETS.items()):
        raise RuntimeError("live HCX E2E suite cannot satisfy its required category mix")

    selected: list[dict[str, Any]] = []
    offsets = {kind: 0 for kind in CASE_TARGETS}
    while any(offsets[kind] < target for kind, target in CASE_TARGETS.items()):
        for kind, target in CASE_TARGETS.items():
            if offsets[kind] < target:
                selected.append(buckets[kind][offsets[kind]])
                offsets[kind] += 1
    if len(selected) != QUESTION_COUNT:
        raise AssertionError("unexpected live HCX E2E case count")
    return selected


def _suite_hash(cases: list[dict[str, Any]]) -> str:
    """Digest the private-in-report test inputs without persisting them."""

    canonical = [
        {"id": str(case["id"]), "question": str(case["question"]), "spec": case["spec"]}
        for case in cases
    ]
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _items_have_evidence(context: dict[str, Any]) -> bool:
    items = context.get("items") or []
    return all(
        isinstance(item, dict)
        and bool(item.get("source_row_hash"))
        and bool(item.get("source_file"))
        for item in items
    )


async def _run(report_path: Path) -> dict[str, object]:
    key = os.getenv("CLOVA_STUDIO_API_KEY", "").strip()
    model_id = os.getenv("HCX_MODEL_ID", APPROVED_MODEL_ID).strip()
    base_url = os.getenv("HCX_BASE_URL", OFFICIAL_BASE_URL).rstrip("/")
    if len(key.encode("utf-8")) < 20:
        raise SystemExit("CLOVA_STUDIO_API_KEY is missing or too short (value redacted)")
    if model_id != APPROVED_MODEL_ID:
        raise SystemExit(f"HCX_MODEL_ID must be {APPROVED_MODEL_ID}")
    if base_url != OFFICIAL_BASE_URL:
        raise SystemExit("HCX_BASE_URL must be the approved official HTTPS endpoint")
    if not DATABASE.is_file():
        raise SystemExit("serving database is missing; rebuild official data before live gate")

    settings = Settings(
        environment="test",
        database_path=DATABASE,
        planner_mode="hcx",
        planner_stage="two",
        clova_studio_api_key=key,
        hcx_model_id=model_id,
        hcx_base_url=base_url,
        hcx_timeout_seconds=float(os.getenv("HCX_TIMEOUT_SECONDS", "12")),
        hcx_total_deadline_seconds=float(os.getenv("HCX_TOTAL_DEADLINE_SECONDS", "25")),
        hcx_max_retries=int(os.getenv("HCX_MAX_RETRIES", "2")),
        hcx_max_concurrency=1,
        hcx_qpm_limit=int(os.getenv("HCX_QPM_LIMIT", "60")),
        hcx_tpm_budget=int(os.getenv("HCX_TPM_BUDGET", "60000")),
    )
    planner = build_planner(settings)
    service = AgentService(settings, planner, DuckDBEngine(DATABASE))
    failures = 0
    evidence_linked = 0
    hcx_planned = 0
    cross_scope_refusals = 0
    by_kind: dict[str, Counter[str]] = defaultdict(Counter)

    try:
        with Oracle(DATABASE) as oracle:
            all_cases = fill_runtime_slots(generate(), oracle.sample_codes(per_scope=16))
            cases = _select_cases(all_cases)
            suite_hash = _suite_hash(cases)
            for case in cases:
                kind = str(case["kind"])
                expected = oracle.expected(dict(case["spec"]))
                try:
                    response = await service.answer(
                        question_id=str(case["id"]), question=str(case["question"])
                    )
                    context = json.loads(response.retrieved_context)
                    scored = score_response(
                        dict(case["spec"]),
                        expected,
                        response.answer + " " + response.retrieved_context,
                        context,
                    )
                    hcx_used = "planner=HCX-007" in response.think_trace
                    passed = bool(scored["passed"]) and hcx_used
                    if hcx_used:
                        hcx_planned += 1
                    if _items_have_evidence(context):
                        evidence_linked += 1
                    if kind == "cross_scope" and str(context.get("answerability")) == "INCOMPARABLE":
                        cross_scope_refusals += 1
                except Exception:
                    # Error details can contain provider response fragments.
                    # Keep only aggregate failure counts in the retained report.
                    passed = False
                by_kind[kind]["total"] += 1
                if passed:
                    by_kind[kind]["passed"] += 1
                else:
                    failures += 1
    finally:
        await service.aclose()

    passed_count = QUESTION_COUNT - failures
    accuracy = passed_count / QUESTION_COUNT
    status = "PASS" if (
        accuracy >= MINIMUM_ACCURACY
        and evidence_linked == QUESTION_COUNT
        and hcx_planned == QUESTION_COUNT
        and cross_scope_refusals == 0
    ) else "FAIL"
    report: dict[str, object] = {
        "status": status,
        "gate": "HCX_100_QUESTION_TWO_STAGE_E2E",
        "model_id": model_id,
        "approved_planner_stage": "two",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "question_suite_sha256": suite_hash,
        "case_count": QUESTION_COUNT,
        "minimum_accuracy": MINIMUM_ACCURACY,
        "passed_count": passed_count,
        "accuracy": round(accuracy, 4),
        "hcx_planned_case_count": hcx_planned,
        "evidence_linked_case_count": evidence_linked,
        "cross_scope_refusal_count": cross_scope_refusals,
        "by_kind": {
            kind: {"total": counts["total"], "passed": counts["passed"]}
            for kind, counts in sorted(by_kind.items())
        },
        "secret_values_recorded": False,
        "questions_recorded": False,
        "prompts_recorded": False,
        "plans_recorded": False,
        "answers_recorded": False,
        "product_identifiers_recorded": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Make 100 explicit live HCX two-stage E2E calls; this can consume quota."
    )
    parser.add_argument(
        "--confirm-live-calls",
        type=int,
        metavar="COUNT",
        help=f"must equal {QUESTION_COUNT}; requests can consume paid quota",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "live_hcx_e2e_gate_report.json",
    )
    args = parser.parse_args()
    if args.confirm_live_calls != QUESTION_COUNT:
        raise SystemExit(f"refusing live HCX E2E gate without --confirm-live-calls {QUESTION_COUNT}")
    result = asyncio.run(_run(args.output.resolve()))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
