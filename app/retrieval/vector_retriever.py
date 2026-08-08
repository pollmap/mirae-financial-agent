"""Runtime cosine-similarity search over ``kg.vec_embedding``.

Brute-force ``array_cosine_similarity`` over the FLOAT[1024] table built by
``etl.vectors`` (DuckDB 1.3.2 without the vss extension). The degrade
contract is strict: every failure mode — query-embedding failure, missing or
empty table, dimension mismatch, SQL error — returns ``None`` so the caller
falls back to the BM25 channel. This module never raises at query time and
never logs the API key or the question text.

The only network call is the query-time embedding via the CLOVA Studio
embedding API (Naver platform, allowed alongside HyperCLOVA X; no other
provider endpoints exist anywhere in the runtime).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import duckdb
import httpx

# Keep in sync with scripts/build_embeddings.py. OPEN_QUESTION: exact
# endpoint/model must be confirmed after the CLOVA Studio key is issued.
DEFAULT_EMBEDDING_URL = "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2"
EMBEDDING_DIM = 1024
DEFAULT_TIMEOUT_SECONDS = 1.5


@dataclass(frozen=True, slots=True)
class VectorHit:
    product_uid: str
    score: float


class QueryEmbedder(Protocol):
    """Query-time embedder; ``None`` is the BM25-degrade signal."""

    def embed(self, text: str) -> list[float] | None: ...


class ClovaQueryEmbedder:
    """CLOVA Studio query embedding with a strict 1.5s budget.

    Returns ``None`` on ANY failure (timeout, transport error, non-200,
    malformed body) — the retriever then degrades to BM25 instead of
    blocking or crashing the answer path.
    """

    def __init__(
        self,
        api_key: str,
        *,
        url: str = DEFAULT_EMBEDDING_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._client = client

    def embed(self, text: str) -> list[float] | None:
        try:
            if self._client is not None:
                response = self._post(self._client, text)
            else:
                with httpx.Client(timeout=self._timeout_seconds) as client:
                    response = self._post(client, text)
            if response.status_code != 200:
                return None
            embedding = response.json()["result"]["embedding"]
            values = [float(value) for value in embedding]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None
        return values or None

    def _post(self, client: httpx.Client, text: str) -> httpx.Response:
        return client.post(
            self._url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"text": text},
            timeout=self._timeout_seconds,
        )


def _prepare_query_vector(vector: list[float] | None) -> list[float] | None:
    """Pad to ``EMBEDDING_DIM`` (zero-fill) or reject; never crash the caller."""

    if not vector or len(vector) > EMBEDDING_DIM:
        return None
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError):
        return None
    if not any(values):
        return None  # zero magnitude: cosine similarity is undefined
    if len(values) < EMBEDDING_DIM:
        values = values + [0.0] * (EMBEDDING_DIM - len(values))
    return values


def _table_has_rows(connection: duckdb.DuckDBPyConnection) -> bool:
    try:
        row = connection.execute("SELECT COUNT(*) FROM kg.vec_embedding").fetchone()
    except duckdb.Error:
        return False
    return bool(row and row[0])


def search(
    connection: duckdb.DuckDBPyConnection,
    query_text: str,
    embedder: QueryEmbedder,
    *,
    field: str = "strategy",
    scope: str | None = None,
    limit: int = 20,
) -> list[VectorHit] | None:
    """Top-k cosine search; ``None`` means "degrade to BM25".

    ``None`` is returned when the embedder fails (returns ``None``), when the
    query vector cannot be validated/padded to 1024 dims, or when
    ``kg.vec_embedding`` is missing or empty (databases built before the
    vector stage, or built with the stage disabled).
    """

    vector = _prepare_query_vector(embedder.embed(query_text))
    if vector is None:
        return None
    if not _table_has_rows(connection):
        return None

    scope_clause = " AND scope = ?" if scope is not None else ""
    params: list[object] = [vector, field]
    if scope is not None:
        params.append(scope)
    params.append(int(limit))
    try:
        rows = connection.execute(
            f"""
            SELECT product_uid,
                   array_cosine_similarity(embedding, CAST(? AS FLOAT[{EMBEDDING_DIM}])) AS score
            FROM kg.vec_embedding
            WHERE field = ?{scope_clause}
            ORDER BY score DESC NULLS LAST
            LIMIT ?
            """,
            params,
        ).fetchall()
    except duckdb.Error:
        return None
    return [
        VectorHit(product_uid=str(product_uid), score=float(score))
        for product_uid, score in rows
        if score is not None and score == score  # drop SQL NULL and NaN scores
    ]
