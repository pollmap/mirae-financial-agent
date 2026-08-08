#!/usr/bin/env python3
"""Precompute CLOVA Studio embeddings into the committed parquet cache.

Standalone developer-side script. It is the ONLY place that calls the CLOVA
Studio embedding API; the Docker build has no API credentials, so ``etl``
loads the committed ``artifacts/embeddings/embeddings_cache.parquet`` instead
of calling any network endpoint.

Usage::

    python scripts/build_embeddings.py \
        --database data/serving/mirae_agent.duckdb \
        --out artifacts/embeddings/embeddings_cache.parquet \
        [--batch-size 16] [--limit N]

Environment:

* ``CLOVA_STUDIO_API_KEY`` (required) — Bearer token. Never printed.
* ``CLOVA_EMBEDDING_URL`` — defaults to
  ``https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2``.
* ``CLOVA_EMBEDDING_MODEL_ID`` — cache provenance label, default
  ``clova-embedding-v2``.

OPEN_QUESTION: the exact CLOVA Studio embedding endpoint path and model name
must be confirmed once the API key is issued (test-app URLs differ from
service-app URLs). Until then this script fails loudly on any non-1024-dim
response instead of silently caching an incompatible model.

Embedded sources (read-only from the serving DuckDB):

* overseas_etp strategy texts (``cu_strtegy``), and
* benchmark strings from every scope, excluding the two overseas sentinel
  strings that are non-values.

The cache is idempotent: rows whose ``text_sha256`` (and model_id) are
unchanged are reused without a new API call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import duckdb
import httpx
import pyarrow as pa
import pyarrow.parquet as pq

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

DEFAULT_EMBEDDING_URL = "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2"
DEFAULT_MODEL_ID = "clova-embedding-v2"
EXPECTED_DIM = 1024
MAX_RETRIES_429 = 3

# Same two sentinel strings excluded by etl/kg.py: they are "no benchmark"
# markers, not benchmark names, and must never be embedded.
BENCHMARK_SENTINELS = (
    "Index is not provided by Management Company",
    "Index is not available on Lipper Database",
)

CACHE_SCHEMA = pa.schema(
    [
        pa.field("doc_id", pa.string()),
        pa.field("product_uid", pa.string()),
        pa.field("scope", pa.string()),
        pa.field("field", pa.string()),
        pa.field("text_sha256", pa.string()),
        pa.field("embedding", pa.list_(pa.float32())),
        pa.field("model_id", pa.string()),
        pa.field("dim", pa.int32()),
    ]
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ClovaEmbeddingClient:
    """Single-text embedding calls with bounded 429 retry and no key leakage."""

    def __init__(
        self,
        api_key: str,
        *,
        url: str = DEFAULT_EMBEDDING_URL,
        client: httpx.Client | None = None,
        max_retries_429: int = MAX_RETRIES_429,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._max_retries_429 = max_retries_429
        self._sleep = sleep

    def embed_text(self, text: str) -> list[float]:
        """Return one embedding vector; raise RuntimeError on any failure.

        Only HTTP 429 is retried (bounded, backoff); everything else fails
        loudly so a wrong endpoint or model is caught immediately. Error
        messages never contain the API key or the source text.
        """

        retries = 0
        while True:
            response = self._client.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"text": text},
            )
            if response.status_code == 429:
                if retries >= self._max_retries_429:
                    raise RuntimeError(
                        f"CLOVA embedding API kept returning 429 after {retries} retries"
                    )
                delay = _retry_after_seconds(response.headers.get("Retry-After"))
                if delay is None:
                    delay = float(2**retries)
                self._sleep(delay)
                retries += 1
                continue
            if response.status_code != 200:
                raise RuntimeError(
                    "CLOVA embedding API returned HTTP "
                    f"{response.status_code}; confirm CLOVA_EMBEDDING_URL after key issuance"
                )
            return _parse_embedding(response)


def _retry_after_seconds(header_value: str | None) -> float | None:
    if not header_value:
        return None
    try:
        return max(0.0, float(header_value.strip().rstrip("s")))
    except ValueError:
        return None


def _parse_embedding(response: httpx.Response) -> list[float]:
    try:
        payload = response.json()
        embedding = payload["result"]["embedding"]
        values = [float(value) for value in embedding]
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError(
            "CLOVA embedding API response is missing result.embedding; "
            "confirm the endpoint/model after key issuance"
        ) from error
    if len(values) != EXPECTED_DIM:
        raise RuntimeError(
            f"CLOVA embedding dimension {len(values)} != expected {EXPECTED_DIM}; "
            "confirm the embedding model (serving schema is FLOAT[1024])"
        )
    return values


def fetch_source_rows(database: Path, limit: int | None) -> list[dict[str, str]]:
    """Deterministic (doc_id-ordered) embedding sources from the serving DB."""

    sql = """
        WITH sources AS (
            SELECT 'vec:strategy:' || product_uid AS doc_id,
                   product_uid, scope, 'strategy' AS field, strategy AS text
            FROM serving.product_catalog
            WHERE scope = 'overseas_etp' AND strategy IS NOT NULL
            UNION ALL
            SELECT 'vec:benchmark:' || product_uid,
                   product_uid, scope, 'benchmark', benchmark
            FROM serving.product_catalog
            WHERE benchmark IS NOT NULL AND benchmark NOT IN (?, ?)
        )
        SELECT doc_id, product_uid, scope, field, text
        FROM sources
        ORDER BY doc_id
    """
    params: list[object] = list(BENCHMARK_SENTINELS)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    return [
        {
            "doc_id": doc_id,
            "product_uid": product_uid,
            "scope": scope,
            "field": field,
            "text": text,
        }
        for doc_id, product_uid, scope, field, text in rows
    ]


def load_existing_cache(path: Path) -> dict[str, dict[str, object]]:
    """doc_id → cached row (used for the idempotent skip of unchanged texts)."""

    if not path.exists():
        return {}
    table = pq.read_table(path)
    return {str(row["doc_id"]): row for row in table.to_pylist()}


def _write_cache(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=CACHE_SCHEMA)
    temporary = path.with_name(path.name + ".tmp")
    pq.write_table(table, temporary, compression="zstd")
    os.replace(temporary, path)


def build_cache(
    *,
    database: Path,
    out: Path,
    batch_size: int,
    limit: int | None,
    client: ClovaEmbeddingClient,
    model_id: str,
) -> dict[str, object]:
    sources = fetch_source_rows(database, limit)
    existing = load_existing_cache(out)

    output_rows: list[dict[str, object]] = []
    pending: list[tuple[int, str, str]] = []  # (output index, doc_id, sha)
    for source in sources:
        sha = _sha256(source["text"])
        cached = existing.get(source["doc_id"])
        row: dict[str, object] = {
            "doc_id": source["doc_id"],
            "product_uid": source["product_uid"],
            "scope": source["scope"],
            "field": source["field"],
            "text_sha256": sha,
            "embedding": None,
            "model_id": model_id,
            "dim": EXPECTED_DIM,
        }
        if (
            cached is not None
            and cached.get("text_sha256") == sha
            and cached.get("model_id") == model_id
            and cached.get("embedding") is not None
        ):
            row["embedding"] = cached["embedding"]
        else:
            pending.append((len(output_rows), source["doc_id"], sha))
        output_rows.append(row)

    # One API call per distinct text: identical benchmark strings share a vector.
    text_by_doc_id = {source["doc_id"]: source["text"] for source in sources}
    vector_by_sha: dict[str, list[float]] = {}
    embedded_calls = 0
    for position, (index, doc_id, sha) in enumerate(pending, start=1):
        if sha not in vector_by_sha:
            vector_by_sha[sha] = client.embed_text(text_by_doc_id[doc_id])
            embedded_calls += 1
        output_rows[index]["embedding"] = vector_by_sha[sha]
        if batch_size > 0 and position % batch_size == 0:
            _write_cache(out, output_rows[: index + 1])
            print(f"[build_embeddings] {position}/{len(pending)} new texts embedded")

    _write_cache(out, output_rows)
    return {
        "status": "ok",
        "database": str(database),
        "out": str(out),
        "model_id": model_id,
        "dim": EXPECTED_DIM,
        "total_docs": len(output_rows),
        "reused_docs": len(output_rows) - len(pending),
        "newly_embedded_docs": len(pending),
        "api_calls": embedded_calls,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database",
        type=Path,
        default=PACKAGE_ROOT / "data" / "serving" / "mirae_agent.duckdb",
        help="Serving DuckDB path (opened read-only)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PACKAGE_ROOT / "artifacts" / "embeddings" / "embeddings_cache.parquet",
        help="Committed parquet cache path",
    )
    parser.add_argument("--batch-size", type=int, default=16, help="Checkpoint/progress interval")
    parser.add_argument("--limit", type=int, default=None, help="Embed at most N source rows")
    args = parser.parse_args()

    api_key = os.getenv("CLOVA_STUDIO_API_KEY")
    if not api_key:
        raise SystemExit("CLOVA_STUDIO_API_KEY 환경변수가 필요합니다 (키 값은 로그에 남기지 않습니다).")
    url = os.getenv("CLOVA_EMBEDDING_URL", DEFAULT_EMBEDDING_URL)
    model_id = os.getenv("CLOVA_EMBEDDING_MODEL_ID", DEFAULT_MODEL_ID)

    summary = build_cache(
        database=args.database,
        out=args.out,
        batch_size=args.batch_size,
        limit=args.limit,
        client=ClovaEmbeddingClient(api_key, url=url),
        model_id=model_id,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
