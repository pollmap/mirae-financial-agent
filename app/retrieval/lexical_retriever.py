"""BM25 lexical retrieval over the ``kg.lex_*`` tables materialized at ETL time.

The tokenizer lives here — not in ``etl`` — because the runtime image ships
without the ``etl`` package; the build stage imports it from this module so
index-time and query-time tokenization can never drift. ``name`` and
``benchmark`` are Korean/latin mixed (word tokens plus Korean character
2/3-grams); ``strategy`` is English free text (word tokens minus a tiny
stopword set). Scoring is a single SQL statement over the materialized
postings (BM25, k1=1.2, b=0.75) and degrades gracefully (returns empty
results) when the database predates the lexical stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb

BM25_K1 = 1.2
BM25_B = 0.75

ENGLISH_STOPWORDS = frozenset(
    {
        "the", "of", "and", "in", "to", "a", "is", "for", "on",
        "with", "as", "by", "or", "an", "its", "that", "are",
    }
)

_WORD_RUN = re.compile(r"[0-9a-z]+")
_TOKEN_RUN = re.compile(r"[0-9a-z]+|[가-힣ㄱ-ㆎ]+")


@dataclass(frozen=True, slots=True)
class LexicalHit:
    product_uid: str
    score: float


def field_uses_korean_ngrams(field: str) -> bool:
    """``strategy`` is English free text; every other field mixes Korean/latin."""

    return field != "strategy"


def _hangul_ngrams(run: str) -> list[str]:
    """Character 2-grams and 3-grams; a run shorter than 2 chars is kept as-is."""

    if len(run) < 2:
        return [run]
    grams = [run[i : i + 2] for i in range(len(run) - 1)]
    grams.extend(run[i : i + 3] for i in range(len(run) - 2))
    return grams


def tokenize(text: str | None, *, korean_ngrams: bool) -> list[str]:
    """Lowercase tokens with duplicates preserved (callers count tf from them).

    Word tokens are ``[0-9a-z]+`` runs. With ``korean_ngrams`` set, Korean
    character runs additionally emit 2-grams and 3-grams; without it, English
    stopwords are dropped. Shared by the index build and query paths.
    """

    if not text:
        return []
    lowered = text.lower()
    if not korean_ngrams:
        return [token for token in _WORD_RUN.findall(lowered) if token not in ENGLISH_STOPWORDS]
    tokens: list[str] = []
    for match in _TOKEN_RUN.finditer(lowered):
        run = match.group(0)
        if _WORD_RUN.fullmatch(run):
            tokens.append(run)
        else:
            tokens.extend(_hangul_ngrams(run))
    return tokens


def _lexical_available(connection: duckdb.DuckDBPyConnection) -> bool:
    try:
        connection.execute("SELECT 1 FROM kg.lex_doc LIMIT 1")
        return True
    except duckdb.Error:
        return False


def search(
    connection: duckdb.DuckDBPyConnection,
    query_text: str,
    *,
    field: str = "name",
    scope: str | None = None,
    limit: int = 50,
) -> list[LexicalHit]:
    """BM25 top-``limit`` products for ``query_text`` against one indexed field."""

    if not _lexical_available(connection):
        return []
    tokens = tokenize(query_text, korean_ngrams=field_uses_korean_ngrams(field))
    terms = list(dict.fromkeys(tokens))
    if not terms:
        return []
    placeholders = ", ".join("?" for _ in terms)
    scope_clause = " AND d.scope = ?" if scope else ""
    params: list[object] = [*terms, field]
    if scope:
        params.append(scope)
    params.append(limit)
    rows = connection.execute(
        f"""
        SELECT d.product_uid,
               SUM(
                   ln(1 + (s.n_docs - f.df + 0.5) / (f.df + 0.5))
                   * t.tf * ({BM25_K1} + 1)
                   / (t.tf + {BM25_K1} * (1 - {BM25_B} + {BM25_B} * d.doc_len / s.avg_doc_len))
               ) AS score
        FROM kg.lex_term AS t
        JOIN kg.lex_doc AS d ON d.doc_id = t.doc_id
        JOIN kg.lex_df AS f ON f.term = t.term AND f.field = d.field
        JOIN kg.lex_stats AS s ON s.field = d.field
        WHERE t.term IN ({placeholders})
          AND d.field = ?{scope_clause}
        GROUP BY d.product_uid
        ORDER BY score DESC, d.product_uid
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [LexicalHit(product_uid=str(uid), score=float(score)) for uid, score in rows]
