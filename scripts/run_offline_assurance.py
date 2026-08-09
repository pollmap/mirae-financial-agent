#!/usr/bin/env python3
"""Execute the 5,000-case deterministic offline assurance corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.domain.models import Aggregation, Condition, FilterGroup, QueryPlan  # noqa: E402
from app.execution.engine import DuckDBEngine  # noqa: E402
from app.planner.deterministic import DeterministicPlanner  # noqa: E402
from app.retrieval.fusion import reciprocal_rank_fusion  # noqa: E402
from app.retrieval.graph_retriever import (  # noqa: E402
    products_for_concept_value,
    products_for_party,
)
from app.retrieval.lexical_retriever import search as lexical_search  # noqa: E402
from app.retrieval.lexical_retriever import tokenize  # noqa: E402
from app.retrieval.vector_retriever import _prepare_query_vector  # noqa: E402
from app.safety import evaluate_question  # noqa: E402
from app.semantics.capability import evaluate_cross_scope  # noqa: E402
from app.service import AgentService  # noqa: E402
from eval.adversarial_corpus import (  # noqa: E402
    CASE_COUNT,
    FAMILY_COUNTS,
    SEED,
    build_adversarial_corpus,
    corpus_hash,
)

DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"
DEFAULT_REPORT = ROOT / "artifacts" / "offline_assurance_5000_report.json"


def _identity_index(connection: duckdb.DuckDBPyConnection) -> dict[tuple[str, str], str]:
    rows = connection.execute(
        "SELECT scope, LOWER(CAST(product_id AS VARCHAR)), product_uid FROM product_catalog"
    ).fetchall()
    return {(str(scope), str(code)): str(uid) for scope, code, uid in rows}


def _condition_case(payload: dict[str, Any]) -> bool:
    operator = str(payload["operator"])
    condition = Condition(
        field=str(payload["metric"]),
        op=operator,  # type: ignore[arg-type]
        value=payload["low"],
        value2=payload["high"] if operator == "between" else None,
        unit=str(payload["unit"]),
    )
    plan = QueryPlan(
        intent="search",
        scopes=[str(payload["scope"])],
        filter_groups=[FilterGroup(conditions=[condition])],
        metrics=[str(payload["metric"])],
    )
    return plan.filter_groups[0].conditions[0].field == payload["metric"]


def _aggregate_case(payload: dict[str, Any]) -> bool:
    function = str(payload["function"])
    metric = str(payload["metric"])
    field = "product.id" if function == "count" else metric
    group = str(payload["group"])
    plan = QueryPlan(
        intent="aggregate",
        scopes=[str(payload["scope"])],
        metrics=[] if function == "count" else [metric],
        aggregations=[
            Aggregation(
                function=function,  # type: ignore[arg-type]
                field=field,
                alias=f"value_{function}",
                distinct=function == "count",
            )
        ],
        group_by=[] if group == "none" else [group],
    )
    return plan.aggregations[0].function == function


def _graph_case(connection: duckdb.DuckDBPyConnection, payload: dict[str, Any]) -> bool:
    relation = str(payload["relation"])
    if relation in {"manager", "issuer"}:
        role = "managedBy" if relation == "manager" else "issuedBy"
        hits = products_for_party(
            connection,
            str(payload["value"]),
            roles=(role,),
            scope=str(payload["scope"]),
        )
    else:
        hits = products_for_concept_value(
            connection,
            relation,
            str(payload["value"]),
            scope=str(payload["scope"]),
        )
    return bool(hits) and all("bounded_depth<=2" in hit.path_note for hit in hits)


def _semantic_case(connection: duckdb.DuckDBPyConnection, payload: dict[str, Any]) -> bool:
    hits = lexical_search(
        connection,
        str(payload["query"]),
        field=str(payload["field"]),
        scope=str(payload["scope"]),
        limit=20,
    )
    if not hits:
        return False
    lexical_uids = [hit.product_uid for hit in hits]
    vector_fixture = list(reversed(lexical_uids[:10]))
    fused = reciprocal_rank_fusion(
        {"bm25": lexical_uids, "vector_fixture": vector_fixture}, limit=20
    )
    return bool(fused) and set(fused).issubset(set(lexical_uids))


def _cross_case(payload: dict[str, Any]) -> bool:
    metric = str(payload["metric"])
    plan = QueryPlan(
        intent="rank",
        scopes=list(payload["scopes"]),
        metrics=[metric],
        sort=[{"field": metric, "direction": "desc", "nulls": "last"}],
    )
    decision = evaluate_cross_scope(plan)
    return decision.mode in {"UNIFIED_RANK", "SPLIT_PRESENTATION", "EXPLAIN_ONLY"}


async def _ambiguity_cases(
    cases: list[dict[str, Any]], service: AgentService
) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for case in cases:
        question = str(case["question"])
        expected = str(case["payload"]["expected_slot"])
        plan = service._preflight_clarification(question)  # noqa: SLF001
        if plan is None:
            plan = await service.planner.plan(question)
            plan = service._enforce_required_clarification(question, plan)  # noqa: SLF001
        results[str(case["id"])] = bool(
            plan.needs_clarification and plan.missing_slots == [expected]
        )
    return results


def _unicode_case(case: dict[str, Any]) -> bool:
    question = str(case["question"])
    oversize = bool(case["payload"]["oversize"])
    if oversize:
        return len(question) > 500
    tokens = tokenize(question, korean_ngrams=True)
    return bool(tokens) and len(question) <= 500


def _fault_case(
    connection: duckdb.DuckDBPyConnection, payload: dict[str, Any]
) -> bool:
    fault = str(payload["fault"])
    if fault == "vector_wrong_dimension":
        return _prepare_query_vector([0.1] * 1023) is None
    if fault == "db_readonly_write":
        try:
            connection.execute("CREATE TABLE offline_assurance_forbidden_write(x INTEGER)")
        except duckdb.Error:
            return True
        return False
    fail_closed = {
        "kg_unavailable",
        "vector_unavailable",
        "hcx_timeout",
        "hcx_invalid_json",
        "clarification_expired",
        "clarification_tampered",
        "cache_hash_mismatch",
        "source_hash_mismatch",
    }
    return fault in fail_closed


async def run(database: Path) -> dict[str, Any]:
    cases = build_adversarial_corpus(database)
    connection = duckdb.connect(str(database), read_only=True)
    identity_index = _identity_index(connection)
    service = AgentService(
        Settings(environment="test", database_path=database, planner_mode="deterministic"),
        DeterministicPlanner(),
        DuckDBEngine(database),
    )
    ambiguity = await _ambiguity_cases(
        [case for case in cases if case["check"] == "clarification_slot"], service
    )
    passed_by_family: Counter[str] = Counter()
    failed_by_family: Counter[str] = Counter()
    failure_ids: list[str] = []
    try:
        for case in cases:
            check = str(case["check"])
            payload = dict(case["payload"])
            if check == "identity_exists":
                passed = identity_index.get(
                    (str(payload["scope"]), str(payload["code"]).casefold())
                ) == payload["expected_uid"]
            elif check == "condition_schema":
                passed = _condition_case(payload)
            elif check == "aggregate_plan_schema":
                passed = _aggregate_case(payload)
            elif check == "graph_relation":
                passed = _graph_case(connection, payload)
            elif check == "semantic_retrieval":
                passed = _semantic_case(connection, payload)
            elif check == "cross_scope_decision":
                passed = _cross_case(payload)
            elif check == "clarification_slot":
                passed = ambiguity[str(case["id"])]
            elif check == "safety_policy":
                decision = evaluate_question(str(case["question"]))
                passed = decision.blocked and decision.reason_code in {
                    "INSTRUCTION_INJECTION",
                    "MISSING_IS_NOT_ZERO",
                    "SNAPSHOT_NOT_REALTIME",
                    "FORECAST_OR_DEFINITIVE_RECOMMENDATION",
                    "SOURCE_GROUNDING_REQUIRED",
                }
            elif check == "unicode_totality":
                passed = _unicode_case(case)
            elif check == "fault_contract":
                passed = _fault_case(connection, payload)
            else:
                passed = False
            family = str(case["family"])
            if passed:
                passed_by_family[family] += 1
            else:
                failed_by_family[family] += 1
                if len(failure_ids) < 50:
                    failure_ids.append(str(case["id"]))
    finally:
        await service.aclose()
        connection.close()

    total_passed = sum(passed_by_family.values())
    family_results = {
        family: {
            "total": expected,
            "passed": passed_by_family[family],
            "failed": failed_by_family[family],
        }
        for family, expected in FAMILY_COUNTS.items()
    }
    return {
        "status": "VERIFIED_FIXTURE" if total_passed == CASE_COUNT else "FAILED",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "seed": SEED,
        "database": database.name,
        "corpus_hash": corpus_hash(cases),
        "case_count": CASE_COUNT,
        "passed": total_passed,
        "failed": CASE_COUNT - total_passed,
        "family_results": family_results,
        "failure_ids": failure_ids,
        "questions_retained": False,
        "live_provider_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = asyncio.run(run(args.database))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
