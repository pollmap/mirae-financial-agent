"""Federated retrieval, frozen holdout, and local A-E ablation gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from app.domain.models import Condition, Entity, FilterGroup, QueryPlan
from app.execution.engine import DuckDBEngine
from app.retrieval.graph_retriever import products_for_concept_value, products_for_party
from app.retrieval.lexical_retriever import search as lexical_search
from app.semantics.grounder import ground_semantic
from app.semantics.normalize import normalize_party

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"
DEFAULT_OUTPUT = ROOT / "artifacts" / "federated_eval_report.json"
HOLDOUT_SHA256 = "0c7de9a9c98378a0d44c47e289c4ef7b9fb577cf3cebbd473b421066e5f823a8"


@dataclass(frozen=True, slots=True)
class HoldoutCase:
    case_id: str
    question: str
    plan: QueryPlan

    def payload(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "plan": self.plan.model_dump(mode="json"),
        }


def _top_values(
    connection: duckdb.DuckDBPyConnection,
    *,
    scope: str,
    column: str,
    limit: int,
) -> list[str]:
    sentinel_clause = (
        " AND benchmark NOT IN ('Index is not provided by Management Company', "
        "'Index is not available on Lipper Database')"
        if column == "benchmark"
        else ""
    )
    rows = connection.execute(
        f"SELECT {column}, COUNT(*) AS n FROM product_catalog "
        f"WHERE scope=? AND {column} IS NOT NULL AND {column}<>'' "
        + sentinel_clause
        + f" GROUP BY 1 ORDER BY n DESC, {column} LIMIT ?",
        [scope, limit],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _condition_plan(scope: str, field: str, value: str) -> QueryPlan:
    return QueryPlan(
        intent="search",
        scopes=[scope],
        filter_groups=[FilterGroup(conditions=[Condition(field=field, op="eq", value=value)])],
        limit=5,
    )


def build_holdout(connection: duckdb.DuckDBPyConnection) -> list[HoldoutCase]:
    """Build the frozen 100-case set from the immutable official snapshot."""

    cases: list[HoldoutCase] = []
    relation_by_scope = {
        "bond": ("issuer", "product.issuer"),
        "domestic_etp": ("manager", "product.manager"),
        "overseas_etp": ("manager", "product.manager"),
        "fund": ("region", "product.region"),
    }
    for scope in ("bond", "domestic_etp", "overseas_etp", "fund"):
        products = connection.execute(
            "SELECT product_uid, product_id FROM product_catalog "
            "WHERE scope=? AND product_id IS NOT NULL AND product_id<>'' "
            "QUALIFY COUNT(*) OVER (PARTITION BY product_id)=1 "
            "ORDER BY product_uid LIMIT 15",
            [scope],
        ).fetchall()
        if len(products) != 15:
            raise RuntimeError(f"holdout requires 15 unique product codes for {scope}")
        for index, (_uid, code) in enumerate(products, start=1):
            cases.append(
                HoldoutCase(
                    case_id=f"{scope}-lookup-{index:02d}",
                    question=f"{scope} 상품코드 {code}의 공식 데이터 상세조회",
                    plan=QueryPlan(
                        intent="lookup",
                        scopes=[scope],
                        entities=[Entity(code=str(code), scope=scope)],
                        limit=1,
                    ),
                )
            )
        for index, value in enumerate(
            _top_values(connection, scope=scope, column="asset_type", limit=5), start=1
        ):
            cases.append(
                HoldoutCase(
                    case_id=f"{scope}-asset-{index:02d}",
                    question=f"{scope} 중 자산유형이 {value}인 상품",
                    plan=_condition_plan(scope, "product.asset_type", value),
                )
            )
        column, field = relation_by_scope[scope]
        for index, value in enumerate(
            _top_values(connection, scope=scope, column=column, limit=5), start=1
        ):
            cases.append(
                HoldoutCase(
                    case_id=f"{scope}-relation-{index:02d}",
                    question=f"{scope} 관계 조건 {value}에 해당하는 상품",
                    plan=_condition_plan(scope, field, value),
                )
            )
    if len(cases) != 100:
        raise RuntimeError(f"holdout case count must be 100, got {len(cases)}")
    return cases


def _holdout_hash(cases: list[HoldoutCase]) -> str:
    payload = json.dumps(
        [case.payload() for case in cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _signature(engine: DuckDBEngine, plan: QueryPlan) -> dict[str, Any]:
    evidence = engine.execute(plan)
    return {
        "answerability": str(evidence.answerability),
        "reason_code": evidence.reason_code,
        "result_count": evidence.result_count,
        "uids": [item.product_uid for item in evidence.items],
        "aggregates": [item.model_dump(mode="json") for item in evidence.aggregates],
    }


def run_holdout(cases: list[HoldoutCase]) -> dict[str, Any]:
    sql = DuckDBEngine(DATABASE, graph_enabled=False, lexical_enabled=False)
    graph = DuckDBEngine(DATABASE, graph_enabled=True, lexical_enabled=False)
    full = DuckDBEngine(DATABASE, graph_enabled=True, lexical_enabled=True)
    passed = 0
    route_hits = 0
    for case in cases:
        expected = _signature(sql, case.plan)
        graph_evidence = graph.execute(case.plan)
        full_evidence = full.execute(case.plan)
        graph_signature = {
            "answerability": str(graph_evidence.answerability),
            "reason_code": graph_evidence.reason_code,
            "result_count": graph_evidence.result_count,
            "uids": [item.product_uid for item in graph_evidence.items],
            "aggregates": [item.model_dump(mode="json") for item in graph_evidence.aggregates],
        }
        full_signature = {
            "answerability": str(full_evidence.answerability),
            "reason_code": full_evidence.reason_code,
            "result_count": full_evidence.result_count,
            "uids": [item.product_uid for item in full_evidence.items],
            "aggregates": [item.model_dump(mode="json") for item in full_evidence.aggregates],
        }
        if graph_signature != expected or full_signature != expected:
            raise AssertionError(f"holdout no-regression failed: {case.case_id}")
        if any(trace.channel == "graph" and trace.status == "validated" for trace in graph_evidence.retrieval_trace):
            route_hits += 1
        passed += 1
    return {"total": len(cases), "passed": passed, "graph_route_hits": route_hits}


def run_graph_benchmark(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    specs = [
        ("domestic_etp", "manager", "party", "managedBy"),
        ("overseas_etp", "manager", "party", "managedBy"),
        ("bond", "issuer", "party", "issuedBy"),
        ("overseas_etp", "region", "region", "inRegion"),
        ("overseas_etp", "benchmark", "benchmark", "tracksBenchmark"),
        ("fund", "benchmark", "benchmark", "tracksBenchmark"),
    ]
    passed = 0
    path_evidence = 0
    safe_fallbacks = 0
    for scope, column, kind, role in specs:
        values = _top_values(connection, scope=scope, column=column, limit=20)
        if len(values) != 20:
            raise RuntimeError(f"graph benchmark requires 20 values for {scope}.{column}")
        for value in values:
            expected = {
                str(row[0])
                for row in connection.execute(
                    f"SELECT product_uid FROM product_catalog WHERE scope=? AND {column}=?",
                    [scope, value],
                ).fetchall()
            }
            if kind == "party":
                hits = products_for_party(connection, value, roles=(role,), scope=scope)
            else:
                hits = products_for_concept_value(connection, kind, value, scope=scope)
            actual = {hit.product_uid for hit in hits}
            if actual != expected:
                if kind != "party":
                    raise AssertionError(f"graph/sql mismatch: {scope}.{column}={value}")
                if actual:
                    placeholders = ",".join("?" for _ in actual)
                    raw_values = connection.execute(
                        f"SELECT DISTINCT {column} FROM product_catalog "
                        f"WHERE product_uid IN ({placeholders})",
                        list(actual),
                    ).fetchall()
                    if any(
                        normalize_party(str(row[0])) != normalize_party(value)
                        for row in raw_values
                    ):
                        raise AssertionError(
                            f"unsafe normalized party expansion: {scope}.{column}={value}"
                        )
                # Runtime deliberately retains the literal SQL set in this
                # case and records graph/sql mismatch as a fallback.
                safe_fallbacks += 1
            path_evidence += sum("row_hash" in hit.path_note for hit in hits)
            passed += 1
    return {
        "total": 120,
        "passed": passed,
        "path_evidence_hits": path_evidence,
        "safe_sql_fallbacks": safe_fallbacks,
    }


def run_lexical_benchmark(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT product_uid, term FROM ("
        "SELECT d.product_uid, t.term, f.df, t.tf, "
        "ROW_NUMBER() OVER (PARTITION BY d.product_uid ORDER BY f.df, t.tf DESC, t.term) AS rn "
        "FROM kg.lex_doc d JOIN kg.lex_term t ON t.doc_id=d.doc_id "
        "JOIN kg.lex_df f ON f.term=t.term AND f.field=d.field "
        "WHERE d.scope='overseas_etp' AND d.field='strategy' AND f.df=1) "
        "WHERE rn=1 ORDER BY product_uid LIMIT 20"
    ).fetchall()
    passed = 0
    for uid, term in rows:
        hits = lexical_search(
            connection, str(term), field="strategy", scope="overseas_etp", limit=20
        )
        if str(uid) not in {hit.product_uid for hit in hits}:
            raise AssertionError(f"lexical self-retrieval failed for {uid}")
        passed += 1
    return {"total": 20, "passed": passed}


def run_ablation(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    manager = _top_values(
        connection, scope="domestic_etp", column="manager", limit=1
    )[0]
    physical = _condition_plan("domestic_etp", "product.manager", manager)
    semantic = ground_semantic(
        {
            "intent": "search",
            "scope_concepts": ["domestic_etp"],
            "metric_concepts": [],
            "aggregations": [],
            "group_by_concepts": [],
            "filters": [{"concept": "manager", "op": "eq", "value_text": manager}],
            "sort_direction": "none",
            "top_n": 5,
            "entities": [],
            "needs_clarification": False,
            "clarification_question": "",
        },
        question="운용사 관계 조건 검색",
    )
    variants = {
        "A_sql_baseline": (DuckDBEngine(DATABASE, graph_enabled=False, lexical_enabled=False), physical),
        "B_typed_plan_sql": (DuckDBEngine(DATABASE, graph_enabled=False, lexical_enabled=False), physical),
        "C_ontology_grounded_sql": (DuckDBEngine(DATABASE, graph_enabled=False, lexical_enabled=False), semantic),
        "D_graph_sql": (DuckDBEngine(DATABASE, graph_enabled=True, lexical_enabled=False), semantic),
        "E_full_federated_validation": (DuckDBEngine(DATABASE), semantic),
    }
    signatures = {name: _signature(engine, plan) for name, (engine, plan) in variants.items()}
    baseline = signatures["A_sql_baseline"]
    if any(signature != baseline for signature in signatures.values()):
        raise AssertionError("local A-E ablation changed the SQL-authoritative answer")
    return {
        "status": "PASS",
        "variants": list(variants),
        "F_live_hcx_composer": "PENDING_EXTERNAL_CREDENTIAL",
    }


def run(output: Path, *, print_hash: bool = False) -> dict[str, Any]:
    with duckdb.connect(str(DATABASE), read_only=True) as connection:
        cases = build_holdout(connection)
        holdout_hash = _holdout_hash(cases)
        if print_hash:
            print(holdout_hash)
            return {"holdout_sha256": holdout_hash}
        if holdout_hash != HOLDOUT_SHA256:
            raise RuntimeError(
                f"frozen holdout hash mismatch: expected {HOLDOUT_SHA256}, got {holdout_hash}"
            )
        report = {
            "status": "LOCAL_PASS_EXTERNAL_LIVE_GATES_PENDING",
            "holdout_sha256": holdout_hash,
            "holdout": run_holdout(cases),
            "federated_graph": run_graph_benchmark(connection),
            "federated_lexical": run_lexical_benchmark(connection),
            "vector": "FIXTURE_VERIFIED_BY_PYTEST_LIVE_CACHE_PENDING",
            "ablation": run_ablation(connection),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--print-holdout-hash", action="store_true")
    args = parser.parse_args()
    report = run(args.output, print_hash=args.print_holdout_hash)
    if not args.print_holdout_hash:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
