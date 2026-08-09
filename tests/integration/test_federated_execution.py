from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from app.domain.models import Condition, FilterGroup, QueryPlan
from app.execution.engine import DuckDBEngine
from app.retrieval.lexical_retriever import LexicalHit
from app.retrieval.vector_retriever import VectorHit

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"


def _search_plan(field: str, value: str, *, scope: str) -> QueryPlan:
    return QueryPlan(
        intent="search",
        scopes=[scope],
        filter_groups=[
            FilterGroup(conditions=[Condition(field=field, op="contains", value=value)])
        ],
        limit=3,
    )


def test_graph_party_and_concept_candidates_are_sql_validated() -> None:
    engine = DuckDBEngine(DATABASE)
    with engine._connect() as connection:  # noqa: SLF001 - integration contract
        manager = str(
            connection.execute(
                "SELECT manager FROM product_catalog WHERE scope='domestic_etp' "
                "AND manager IS NOT NULL GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1"
            ).fetchone()[0]
        )
        region = str(
            connection.execute(
                "SELECT region FROM product_catalog WHERE scope='domestic_etp' "
                "AND region IS NOT NULL GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1"
            ).fetchone()[0]
        )

    for field, value in (("product.manager", manager), ("product.region", region)):
        plan = QueryPlan(
            intent="search",
            scopes=["domestic_etp"],
            filter_groups=[
                FilterGroup(conditions=[Condition(field=field, op="eq", value=value)])
            ],
            limit=3,
        )
        evidence = engine.execute(plan)
        graph = next(item for item in evidence.retrieval_trace if item.channel == "graph")
        assert graph.status == "validated"
        assert graph.candidate_count > 0
        assert graph.verified_count == graph.candidate_count
        assert evidence.result_count == 3
        assert all(item.fields for item in evidence.items)


def test_theme_bm25_replaces_only_zero_result_text_filter_and_keeps_evidence() -> None:
    engine = DuckDBEngine(DATABASE)
    plan = _search_plan("product.strategy", "quality factors", scope="overseas_etp")
    evidence = engine.execute(plan)
    assert evidence.result_count == 3
    assert any(
        trace.channel == "lexical" and trace.status == "used"
        for trace in evidence.retrieval_trace
    )
    assert all(
        any(field.metric_id == "product.strategy" for field in item.fields)
        for item in evidence.items
    )


def test_vector_and_lexical_channels_are_rrf_fused_with_fixtures(monkeypatch) -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE product_catalog(product_uid VARCHAR, scope VARCHAR, strategy VARCHAR)"
    )
    connection.executemany(
        "INSERT INTO product_catalog VALUES (?, 'overseas_etp', ?)",
        [("P1", "alpha"), ("P2", "beta")],
    )

    def lexical(*_args, **_kwargs):
        return [LexicalHit("P1", 2.0), LexicalHit("P2", 1.0)]

    def vector(*_args, **_kwargs):
        return [VectorHit("P2", 0.99), VectorHit("P1", 0.8)]

    monkeypatch.setattr("app.retrieval.lexical_retriever.search", lexical)
    monkeypatch.setattr("app.retrieval.vector_retriever.search", vector)
    engine = DuckDBEngine(
        DATABASE,
        vector_enabled=True,
        query_embedder=object(),
    )
    plan = _search_plan("product.strategy", "missing theme", scope="overseas_etp")
    retrieval = engine._prepare_federated(connection, plan)  # noqa: SLF001
    assert set(retrieval.candidate_uids or ()) == {"P1", "P2"}
    assert {trace.channel for trace in retrieval.trace} >= {"lexical", "vector", "sql"}
    assert all(trace.status == "used" for trace in retrieval.trace if trace.channel != "sql")


def test_vector_unavailable_is_an_explicit_bm25_fallback(monkeypatch) -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE product_catalog(product_uid VARCHAR, scope VARCHAR, benchmark VARCHAR)"
    )
    connection.execute("INSERT INTO product_catalog VALUES ('P1', 'overseas_etp', 'Index A')")
    monkeypatch.setattr(
        "app.retrieval.lexical_retriever.search",
        lambda *_args, **_kwargs: [LexicalHit("P1", 1.0)],
    )
    monkeypatch.setattr(
        "app.retrieval.vector_retriever.search", lambda *_args, **_kwargs: None
    )
    engine = DuckDBEngine(DATABASE, vector_enabled=True, query_embedder=object())
    plan = _search_plan("product.benchmark", "missing index", scope="overseas_etp")
    retrieval = engine._prepare_federated(connection, plan)  # noqa: SLF001
    vector_trace = next(trace for trace in retrieval.trace if trace.channel == "vector")
    assert vector_trace.status == "unavailable"
    assert "BM25" in (vector_trace.fallback_reason or "")
    assert retrieval.candidate_uids == ("P1",)


def test_missing_kg_tables_fall_back_to_sql_without_losing_candidates() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE product_catalog(product_uid VARCHAR, scope VARCHAR, manager VARCHAR)"
    )
    connection.execute("INSERT INTO product_catalog VALUES ('P1', 'domestic_etp', 'Manager A')")
    plan = QueryPlan(
        intent="search",
        scopes=["domestic_etp"],
        filter_groups=[
            FilterGroup(
                conditions=[Condition(field="product.manager", op="eq", value="Manager A")]
            )
        ],
        limit=3,
    )
    retrieval = DuckDBEngine(DATABASE)._prepare_federated(connection, plan)  # noqa: SLF001
    graph_trace = next(trace for trace in retrieval.trace if trace.channel == "graph")
    assert graph_trace.status == "fallback"
    assert graph_trace.fallback_reason == "graph/sql candidate mismatch 0/1; SQL retained"
    assert retrieval.candidate_uids is None


def test_serving_database_connection_is_read_only() -> None:
    engine = DuckDBEngine(DATABASE)
    with engine._connect() as connection, pytest.raises(  # noqa: SLF001
        duckdb.Error
    ):
        connection.execute("CREATE TABLE forbidden_write(value INTEGER)")
