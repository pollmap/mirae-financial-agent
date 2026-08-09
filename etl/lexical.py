"""Pure-SQL BM25 lexical index build stage.

Materializes an inverted index under the ``kg`` schema (``lex_doc``,
``lex_term``, ``lex_df``, ``lex_stats``) so the runtime can score BM25 with a
single SQL statement. No DuckDB ``fts``/``vss`` extensions are involved — the
runtime image is offline and cannot install them.

Tokenization happens in Python and is imported from
``app.retrieval.lexical_retriever`` (etl may import app; never the reverse)
so index-time and query-time tokens can never drift. Documents cover product
names (all scopes), every non-null official strategy value, and benchmark strings
minus the two Lipper sentinel non-values, with deterministic document ids
(``field + ':' + product_uid``). Each field is fetched and tokenized
separately to keep memory bounded.
"""

from __future__ import annotations

from collections import Counter

import duckdb
import pandas as pd

from app.retrieval.lexical_retriever import field_uses_korean_ngrams, tokenize
from etl.kg import BENCHMARK_SENTINELS

_FIELD_QUERIES: dict[str, str] = {
    "name": """
        SELECT product_uid, scope, name AS text
        FROM serving.product_catalog
        WHERE name IS NOT NULL
        ORDER BY product_uid
        """,
    "strategy": """
        SELECT product_uid, scope, strategy AS text
        FROM serving.product_catalog
        WHERE strategy IS NOT NULL
        ORDER BY product_uid
        """,
    "benchmark": f"""
        SELECT product_uid, scope, benchmark AS text
        FROM serving.product_catalog
        WHERE benchmark IS NOT NULL
          AND benchmark NOT IN ('{BENCHMARK_SENTINELS[0]}', '{BENCHMARK_SENTINELS[1]}')
        ORDER BY product_uid
        """,
}


def create_lexical_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS kg")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS kg.lex_doc (
            doc_id VARCHAR PRIMARY KEY,
            product_uid VARCHAR,
            scope VARCHAR,
            field VARCHAR,
            doc_len INTEGER
        )
        """
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS kg.lex_term (term VARCHAR, doc_id VARCHAR, tf INTEGER)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS kg.lex_df (term VARCHAR, field VARCHAR, df INTEGER)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS kg.lex_stats (field VARCHAR, n_docs INTEGER, avg_doc_len DOUBLE)"
    )


def _refresh_statistics(connection: duckdb.DuckDBPyConnection) -> None:
    """Recompute df and per-field stats from the postings (idempotent)."""

    connection.execute("DELETE FROM kg.lex_df")
    connection.execute(
        """
        INSERT INTO kg.lex_df
        SELECT t.term, d.field, COUNT(*) AS df
        FROM kg.lex_term AS t
        JOIN kg.lex_doc AS d ON d.doc_id = t.doc_id
        GROUP BY t.term, d.field
        """
    )
    connection.execute("DELETE FROM kg.lex_stats")
    connection.execute(
        """
        INSERT INTO kg.lex_stats
        SELECT field, COUNT(*) AS n_docs, AVG(doc_len) AS avg_doc_len
        FROM kg.lex_doc
        GROUP BY field
        """
    )


def index_documents(
    connection: duckdb.DuckDBPyConnection,
    rows: list[tuple[str, str, str | None, str, str]],
) -> int:
    """Tokenize and index ``(doc_id, product_uid, scope, field, text)`` rows.

    Used by both the build stage and the unit tests so they exercise the same
    code path. Documents that tokenize to nothing are skipped. Returns the
    number of documents inserted; df/stats are refreshed afterwards.
    """

    create_lexical_tables(connection)
    doc_records: list[tuple[str, str, str | None, str, int]] = []
    term_records: list[tuple[str, str, int]] = []
    for doc_id, product_uid, scope, field, text in rows:
        tokens = tokenize(text, korean_ngrams=field_uses_korean_ngrams(field))
        if not tokens:
            continue
        doc_records.append((doc_id, product_uid, scope, field, len(tokens)))
        term_records.extend(
            (term, doc_id, tf) for term, tf in sorted(Counter(tokens).items())
        )
    if doc_records:
        docs = pd.DataFrame(
            doc_records, columns=["doc_id", "product_uid", "scope", "field", "doc_len"]
        )
        terms = pd.DataFrame(term_records, columns=["term", "doc_id", "tf"])
        connection.register("_lex_docs", docs)
        connection.execute(
            """
            INSERT INTO kg.lex_doc
            SELECT doc_id, product_uid, scope, field, CAST(doc_len AS INTEGER)
            FROM _lex_docs
            """
        )
        connection.unregister("_lex_docs")
        connection.register("_lex_terms", terms)
        connection.execute(
            "INSERT INTO kg.lex_term SELECT term, doc_id, CAST(tf AS INTEGER) FROM _lex_terms"
        )
        connection.unregister("_lex_terms")
    _refresh_statistics(connection)
    return len(doc_records)


def build_lexical(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Build the lexical index from ``serving.product_catalog`` (per field)."""

    create_lexical_tables(connection)
    for field, query in _FIELD_QUERIES.items():
        frame = connection.execute(query).fetch_df()
        rows = [
            (f"{field}:{record.product_uid}", record.product_uid, record.scope, field, record.text)
            for record in frame.itertuples(index=False)
        ]
        index_documents(connection, rows)

    connection.execute("CREATE INDEX IF NOT EXISTS lex_term_term_idx ON kg.lex_term(term)")
    connection.execute("CREATE INDEX IF NOT EXISTS lex_doc_doc_id_idx ON kg.lex_doc(doc_id)")

    counts = {
        "lex_docs": connection.execute("SELECT COUNT(*) FROM kg.lex_doc").fetchone()[0],
        "lex_terms": connection.execute("SELECT COUNT(*) FROM kg.lex_term").fetchone()[0],
        "lex_vocab": connection.execute(
            "SELECT COUNT(DISTINCT term) FROM kg.lex_term"
        ).fetchone()[0],
    }
    if min(counts.values()) <= 0:
        raise RuntimeError(f"Lexical index reconciliation failed: {counts}")
    return counts
