"""Vector build stage: committed embedding cache → ``kg.vec_embedding``.

The Docker build environment has no CLOVA Studio credentials, so this stage
never calls a network API. It loads the parquet cache precomputed by
``scripts/build_embeddings.py`` and committed at
``artifacts/embeddings/embeddings_cache.parquet``, validates dimensional
consistency, and materializes a brute-force-searchable fixed-size array table
(DuckDB 1.3.2 without the vss extension: ``FLOAT[1024]`` +
``array_cosine_similarity`` at query time).

Contract:

* ``enabled=False`` → ``{"vec_embeddings": 0}`` silently (cache presence is
  irrelevant; callers log the returned count).
* ``enabled=True`` with the cache missing → ``RuntimeError`` with a Korean
  message telling the operator how to generate the cache.
* ``enabled=True`` with the cache present → table rebuilt, count returned.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

EMBEDDING_DIM = 1024
CACHE_RELATIVE_PATH = Path("artifacts") / "embeddings" / "embeddings_cache.parquet"


def _quoted(path: Path) -> str:
    return str(path).replace("'", "''")


def build_vectors(
    connection: duckdb.DuckDBPyConnection,
    package_root: Path,
    *,
    enabled: bool,
) -> dict[str, int]:
    """Load the committed embedding cache into ``kg.vec_embedding``."""

    if not enabled:
        return {"vec_embeddings": 0}

    cache_path = (package_root / CACHE_RELATIVE_PATH).resolve()
    if not cache_path.exists():
        raise RuntimeError(
            "벡터 검색이 활성화되었지만 임베딩 캐시 파일이 없습니다: "
            f"{cache_path}. 개발 환경에서 CLOVA_STUDIO_API_KEY를 설정한 뒤 "
            "`python scripts/build_embeddings.py`로 캐시를 생성해 커밋하거나, "
            "벡터 단계를 비활성화한 채 빌드하세요 (Docker 빌드는 API 키 없이 "
            "커밋된 캐시만 사용합니다)."
        )

    source = f"read_parquet('{_quoted(cache_path)}')"
    total, distinct_doc_ids, bad_rows = connection.execute(
        f"""
        SELECT COUNT(*),
               COUNT(DISTINCT doc_id),
               COUNT(*) FILTER (
                   WHERE doc_id IS NULL
                      OR embedding IS NULL
                      OR dim <> {EMBEDDING_DIM}
                      OR len(embedding) <> {EMBEDDING_DIM}
               )
        FROM {source}
        """
    ).fetchone()
    if bad_rows:
        raise RuntimeError(
            f"임베딩 캐시 차원 불일치: {bad_rows}개 행이 {EMBEDDING_DIM}차원 규격"
            "(dim 컬럼·embedding 길이)을 만족하지 않습니다. "
            "scripts/build_embeddings.py로 캐시를 다시 생성하세요."
        )
    if distinct_doc_ids != total:
        raise RuntimeError(
            f"임베딩 캐시 doc_id 중복: 전체 {total}행 중 고유 doc_id는 "
            f"{distinct_doc_ids}개입니다. 캐시를 다시 생성하세요."
        )

    connection.execute("CREATE SCHEMA IF NOT EXISTS kg")
    connection.execute("DROP TABLE IF EXISTS kg.vec_embedding")
    connection.execute(
        f"""
        CREATE TABLE kg.vec_embedding(
            doc_id VARCHAR PRIMARY KEY,
            product_uid VARCHAR NOT NULL,
            scope VARCHAR,
            field VARCHAR NOT NULL,
            embedding FLOAT[{EMBEDDING_DIM}] NOT NULL,
            model_id VARCHAR,
            text_sha256 VARCHAR
        )
        """
    )
    if total:
        connection.execute(
            f"""
            INSERT INTO kg.vec_embedding
            SELECT doc_id, product_uid, scope, field,
                   CAST(embedding AS FLOAT[{EMBEDDING_DIM}]) AS embedding,
                   model_id, text_sha256
            FROM {source}
            """
        )
    count = connection.execute("SELECT COUNT(*) FROM kg.vec_embedding").fetchone()[0]
    return {"vec_embeddings": int(count)}


__all__ = ["CACHE_RELATIVE_PATH", "EMBEDDING_DIM", "build_vectors"]
