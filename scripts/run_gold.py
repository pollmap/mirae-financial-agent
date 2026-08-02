#!/usr/bin/env python3
"""Execute the 40 gold and 10 policy fixtures against the local deterministic path."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.execution.engine import DuckDBEngine  # noqa: E402
from app.planner.deterministic import DeterministicPlanner  # noqa: E402
from app.service import AgentService  # noqa: E402


def assert_declared_subset(actual: object, expected: object, path: str = "plan") -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise AssertionError(f"{path}: expected object")
        for key, value in expected.items():
            if key not in actual:
                raise AssertionError(f"{path}: missing key {key}")
            assert_declared_subset(actual[key], value, f"{path}.{key}")
        return
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise AssertionError(f"{path}: list length mismatch")
        for index, value in enumerate(expected):
            assert_declared_subset(actual[index], value, f"{path}[{index}]")
        return
    if actual != expected:
        raise AssertionError(f"{path}: expected {expected!r}, got {actual!r}")


async def run() -> dict[str, object]:
    database = ROOT / "data" / "serving" / "mirae_agent.duckdb"
    settings = Settings(environment="test", database_path=database, planner_mode="deterministic")
    service = AgentService(settings, DeterministicPlanner(), DuckDBEngine(database))
    results: list[dict[str, str]] = []
    failures: list[str] = []
    plan_subset_checks = 0
    declared_assertions = 0
    for name in ("gold_queries_v0.jsonl", "policy_queries_v0.jsonl"):
        for line in (ROOT / "tests" / name).read_text(encoding="utf-8-sig").splitlines():
            fixture = json.loads(line)
            plan = await service.planner.plan(fixture["question"])
            expected_plan = fixture.get("expected_plan_subset")
            if expected_plan is not None:
                try:
                    assert_declared_subset(plan.model_dump(mode="json"), expected_plan)
                    plan_subset_checks += 1
                except AssertionError as exc:
                    failures.append(f"{fixture['id']}: {exc}")
            declared_assertions += len(fixture.get("expected_assertions", []))
            response = await service.answer(
                question_id=fixture["id"], question=fixture["question"]
            )
            context = json.loads(response.retrieved_context)
            actual = context["answerability"]
            expected = fixture["expected_answerability"]
            results.append({"id": fixture["id"], "expected": expected, "actual": actual})
            if actual != expected:
                failures.append(f"{fixture['id']}: expected {expected}, got {actual}")
    return {
        "status": "ok" if not failures else "failed",
        "total": len(results),
        "plan_subset_checks": plan_subset_checks,
        "declared_assertions": declared_assertions,
        "assertion_note": (
            "This runner checks answerability and declared plan subsets. "
            "The full pytest integration suite checks every declared value/evidence assertion."
        ),
        "answerability_counts": dict(Counter(item["actual"] for item in results)),
        "failures": failures,
    }


def main() -> None:
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
