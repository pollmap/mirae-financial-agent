"""Paraphrase-invariance (metamorphic) checker.

Template variants that share a ``spec.group`` key are semantically identical
questions phrased differently (same scope, metric, direction, n, filters).
A correct service must return the *identical ordered product_uid list* — and
the same answerability — for every member of a group.  Any divergence is a
paraphrase-sensitivity bug, independent of whether the answer itself is right.

Usage::

    ./.venv/Scripts/python.exe -m eval.metamorphic --limit 20   # first 20 groups
    ./.venv/Scripts/python.exe -m eval.metamorphic              # all groups
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from eval.templates import generate

DEFAULT_DATABASE = Path("data") / "serving" / "mirae_agent.duckdb"
DEFAULT_OUT = Path("artifacts") / "metamorphic_report.json"
MAX_VIOLATIONS = 50


def build_groups(questions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Collect paraphrase groups (insertion-ordered, deterministic)."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in questions:
        group = entry["spec"].get("group")
        if group:
            groups.setdefault(str(group), []).append(entry)
    return {key: members for key, members in groups.items() if len(members) >= 2}


async def run(
    database_path: Path | str = DEFAULT_DATABASE,
    *,
    limit: int | None = None,
    out_path: Path | str = DEFAULT_OUT,
) -> dict[str, Any]:
    """Check every paraphrase group; ``limit`` bounds the number of groups."""

    database_path = Path(database_path)
    if not database_path.is_file():
        raise FileNotFoundError(
            f"serving database not found: {database_path} "
            "(build it with scripts/build_data.py first)"
        )

    # Imported here so eval.templates stays importable without app.
    from app.config import Settings
    from app.execution.engine import DuckDBEngine
    from app.planner.deterministic import DeterministicPlanner
    from app.service import AgentService

    settings = Settings(
        environment="test", database_path=database_path, planner_mode="deterministic"
    )
    service = AgentService(settings, DeterministicPlanner(), DuckDBEngine(database_path))

    groups = build_groups(generate())
    group_keys = list(groups)
    if limit is not None:
        group_keys = group_keys[: max(limit, 0)]

    invariant = 0
    violations: list[dict[str, Any]] = []
    for key in group_keys:
        observations: list[dict[str, Any]] = []
        for entry in groups[key]:
            try:
                response = await service.answer(
                    question_id=str(entry["id"]), question=str(entry["question"])
                )
                context = json.loads(response.retrieved_context)
                uids = [
                    str(item.get("product_uid"))
                    for item in context.get("items") or []
                    if isinstance(item, dict)
                ]
                observations.append(
                    {
                        "id": entry["id"],
                        "question": entry["question"],
                        "answerability": context.get("answerability"),
                        "product_uids": uids,
                    }
                )
            except Exception as exc:  # a crash on one paraphrase is itself a violation
                observations.append(
                    {
                        "id": entry["id"],
                        "question": entry["question"],
                        "answerability": f"ERROR:{type(exc).__name__}",
                        "product_uids": [],
                    }
                )
        signatures = {
            (str(obs["answerability"]), tuple(obs["product_uids"])) for obs in observations
        }
        if len(signatures) == 1:
            invariant += 1
        elif len(violations) < MAX_VIOLATIONS:
            violations.append(
                {
                    "group": key,
                    "members": [
                        {
                            "id": obs["id"],
                            "question": obs["question"],
                            "answerability": obs["answerability"],
                            "product_uids": obs["product_uids"][:5],
                            "result_size": len(obs["product_uids"]),
                        }
                        for obs in observations
                    ],
                }
            )

    report: dict[str, Any] = {
        "database_path": str(database_path),
        "groups_total": len(groups),
        "groups_evaluated": len(group_keys),
        "groups_invariant": invariant,
        "invariance_rate": round(invariant / len(group_keys), 4) if group_keys else None,
        "violations": violations,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check that paraphrased questions return identical results."
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE),
        help="path to the serving DuckDB file (default: data/serving/mirae_agent.duckdb)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N groups"
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="report path (default: artifacts/metamorphic_report.json)",
    )
    args = parser.parse_args()
    report = asyncio.run(run(Path(args.database), limit=args.limit, out_path=Path(args.out)))
    print(
        json.dumps(
            {
                "groups_evaluated": report["groups_evaluated"],
                "groups_invariant": report["groups_invariant"],
                "invariance_rate": report["invariance_rate"],
                "violation_count": len(report["violations"]),
                "report": str(args.out),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
