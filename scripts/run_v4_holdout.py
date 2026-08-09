#!/usr/bin/env python3
"""Run the frozen 200-case v4 holdout without retaining questions."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402
from deploy.live_hcx_extensive_e2e_gate import (  # noqa: E402
    _deterministic_settings,
    _response_context,
    _score_direct_case,
)
from eval.oracle import Oracle  # noqa: E402
from eval.v4_holdout import (  # noqa: E402
    FROZEN_AFTER,
    FROZEN_SHA256,
    HOLDOUT_COUNT,
    build_v4_holdout,
)

DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"
DEFAULT_OUTPUT = ROOT / "artifacts" / "v4_holdout_200_report.json"


async def run(database: Path) -> dict[str, object]:
    cases = build_v4_holdout(database)
    app = create_app(_deterministic_settings())
    passed = 0
    linked = 0
    contract_valid = 0
    by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    failure_ids: list[str] = []
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://holdout"
        ) as client:
            with Oracle(database) as oracle:
                for case in cases:
                    kind = str(case["kind"])
                    by_kind[kind]["total"] += 1
                    response = await client.get(
                        "/answer",
                        params={"question_id": str(case["id"]), "question": str(case["question"])},
                    )
                    context, valid = _response_context(response)
                    contract_valid += int(valid)
                    case_passed = False
                    case_linked = False
                    if context is not None:
                        case_passed, case_linked = _score_direct_case(
                            oracle, case, response, context
                        )
                    passed += int(case_passed)
                    linked += int(case_linked)
                    if case_passed:
                        by_kind[kind]["passed"] += 1
                    elif len(failure_ids) < 30:
                        failure_ids.append(str(case["id"]))
    finally:
        await app.state.service.aclose()

    return {
        "status": "VERIFIED_FIXTURE" if passed >= 196 and linked == 200 else "FAILED",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "frozen_after": FROZEN_AFTER,
        "holdout_sha256": FROZEN_SHA256,
        "case_count": HOLDOUT_COUNT,
        "passed": passed,
        "accuracy": round(passed / HOLDOUT_COUNT, 4),
        "evidence_or_policy_linked": linked,
        "contract_valid": contract_valid,
        "by_kind": {kind: dict(counts) for kind, counts in sorted(by_kind.items())},
        "failure_ids": failure_ids,
        "questions_retained": False,
        "live_provider_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = asyncio.run(run(args.database))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    if report["status"] != "VERIFIED_FIXTURE":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
