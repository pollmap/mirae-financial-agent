from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.retrieval.lexical_retriever import LexicalHit, search, tokenize  # noqa: E402
from etl.lexical import index_documents  # noqa: E402


def _connection_with_corpus() -> duckdb.DuckDBPyConnection:
    """In-memory database indexed through the same path the build stage uses."""

    connection = duckdb.connect(":memory:")
    inserted = index_documents(
        connection,
        [
            ("name:ETP1", "ETP1", "domestic_etp", "name", "TIGER 반도체"),
            ("name:ETP2", "ETP2", "domestic_etp", "name", "KODEX 200"),
            ("name:ETP3", "ETP3", "overseas_etp", "name", "TIGER 미국배당"),
            ("name:FUND1", "FUND1", "fund", "name", "미래에셋 코스피 펀드"),
            (
                "strategy:ETP3",
                "ETP3",
                "overseas_etp",
                "strategy",
                "The fund seeks semiconductor exposure in the US market",
            ),
            (
                "strategy:ETP4",
                "ETP4",
                "overseas_etp",
                "strategy",
                "Tracks broad market equity exposure with a dividend focus",
            ),
            ("strategy:ETP5", "ETP5", "domestic_etp", "strategy", "실물복제"),
            ("benchmark:ETP2", "ETP2", "domestic_etp", "benchmark", "KOSPI 200"),
            ("benchmark:ETP3", "ETP3", "overseas_etp", "benchmark", "S&P 500"),
        ],
    )
    assert inserted == 9
    return connection


def test_exact_token_match_ranks_target_first() -> None:
    connection = _connection_with_corpus()

    hits = search(connection, "KODEX", field="name")
    assert [hit.product_uid for hit in hits] == ["ETP2"]
    assert isinstance(hits[0], LexicalHit)
    assert hits[0].score > 0

    # Both TIGER products match on the shared word token, but the one that
    # also matches the Korean n-grams must rank first.
    hits = search(connection, "TIGER 반도체", field="name")
    assert hits[0].product_uid == "ETP1"
    assert {hit.product_uid for hit in hits} == {"ETP1", "ETP3"}
    assert hits[0].score > hits[1].score


def test_korean_bigram_matching_finds_tiger_semiconductor() -> None:
    connection = _connection_with_corpus()

    hits = search(connection, "반도체", field="name")
    assert [hit.product_uid for hit in hits] == ["ETP1"]
    assert hits[0].score > 0


def test_strategy_field_english_search_ranks_right_doc() -> None:
    connection = _connection_with_corpus()

    hits = search(connection, "semiconductor exposure", field="strategy")
    assert [hit.product_uid for hit in hits] == ["ETP3", "ETP4"]
    assert hits[0].score > hits[1].score

    # Stopwords are never indexed for the strategy field, so a stopword-only
    # query has no terms left and returns nothing.
    assert search(connection, "the of and", field="strategy") == []


def test_korean_strategy_query_expands_to_official_english_text() -> None:
    connection = _connection_with_corpus()

    hits = search(connection, "배당 인컴 전략", field="strategy")
    assert [hit.product_uid for hit in hits] == ["ETP4"]
    assert hits[0].score > 0


def test_korean_strategy_value_is_indexed_without_assuming_english_only() -> None:
    connection = _connection_with_corpus()

    hits = search(connection, "실물복제", field="strategy", scope="domestic_etp")
    assert [hit.product_uid for hit in hits] == ["ETP5"]


def test_scope_filter_restricts_results() -> None:
    connection = _connection_with_corpus()

    unfiltered = {hit.product_uid for hit in search(connection, "TIGER", field="name")}
    assert unfiltered == {"ETP1", "ETP3"}

    overseas = search(connection, "TIGER", field="name", scope="overseas_etp")
    assert [hit.product_uid for hit in overseas] == ["ETP3"]


def test_missing_tables_degrade_to_empty() -> None:
    bare = duckdb.connect(":memory:")
    assert search(bare, "반도체", field="name") == []


def test_duplicate_query_terms_are_deduplicated() -> None:
    connection = _connection_with_corpus()

    once = search(connection, "KODEX", field="name")
    twice = search(connection, "KODEX KODEX", field="name")
    assert [hit.product_uid for hit in twice] == [hit.product_uid for hit in once]
    assert twice[0].score == pytest.approx(once[0].score)


def test_tokenizer_edge_cases() -> None:
    assert tokenize("", korean_ngrams=True) == []
    assert tokenize(None, korean_ngrams=True) == []
    assert tokenize("가", korean_ngrams=True) == ["가"]
    assert tokenize("반도체", korean_ngrams=True) == ["반도", "도체", "반도체"]
    assert tokenize("TIGER 반도체", korean_ngrams=True) == ["tiger", "반도", "도체", "반도체"]
    assert tokenize("S&P 500", korean_ngrams=True) == ["s", "p", "500"]
    # English mode drops stopwords and ignores Korean runs entirely.
    assert tokenize("The exposure of", korean_ngrams=False) == ["exposure"]
    assert tokenize("반도체 fund", korean_ngrams=False) == ["fund"]
    assert tokenize("", korean_ngrams=False) == []

    # An empty query degrades to no hits rather than erroring.
    connection = _connection_with_corpus()
    assert search(connection, "", field="name") == []
