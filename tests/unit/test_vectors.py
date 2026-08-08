"""Unit tests for the vector channel: cache build, ETL load, cosine search.

Everything runs against in-memory DuckDB databases and temporary parquet
caches — never the serving database, and never the network (all HTTP goes
through ``httpx.MockTransport``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.retrieval.vector_retriever import (  # noqa: E402
    ClovaQueryEmbedder,
    VectorHit,
    search,
)
from etl.vectors import EMBEDDING_DIM, build_vectors  # noqa: E402
from scripts.build_embeddings import (  # noqa: E402
    CACHE_SCHEMA,
    ClovaEmbeddingClient,
    build_cache,
)

# --------------------------------------------------------------------- helpers


def _vec(index: int, value: float = 1.0, dim: int = EMBEDDING_DIM) -> list[float]:
    vector = [0.0] * dim
    vector[index] = value
    return vector


def _cache_row(
    doc_id: str,
    product_uid: str,
    scope: str,
    field: str,
    embedding: list[float],
    dim: int = EMBEDDING_DIM,
) -> dict[str, object]:
    return {
        "doc_id": doc_id,
        "product_uid": product_uid,
        "scope": scope,
        "field": field,
        "text_sha256": "0" * 64,
        "embedding": embedding,
        "model_id": "clova-embedding-v2",
        "dim": dim,
    }


def _default_rows() -> list[dict[str, object]]:
    return [
        _cache_row(
            "vec:strategy:GLOBAL_ETP:A", "GLOBAL_ETP:A", "overseas_etp", "strategy", _vec(0)
        ),
        _cache_row(
            "vec:strategy:GLOBAL_ETP:B", "GLOBAL_ETP:B", "overseas_etp", "strategy", _vec(1)
        ),
        _cache_row("vec:strategy:KR_ETP:C", "KR_ETP:C", "domestic_etp", "strategy", _vec(3)),
        _cache_row("vec:benchmark:FUND:D", "FUND:D", "fund", "benchmark", _vec(2)),
    ]


def _install_cache(package_root: Path, rows: list[dict[str, object]]) -> Path:
    path = package_root / "artifacts" / "embeddings" / "embeddings_cache.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=CACHE_SCHEMA), path)
    return path


def _built_connection(
    package_root: Path, rows: list[dict[str, object]]
) -> duckdb.DuckDBPyConnection:
    _install_cache(package_root, rows)
    connection = duckdb.connect(":memory:")
    counts = build_vectors(connection, package_root, enabled=True)
    assert counts == {"vec_embeddings": len(rows)}
    return connection


class FakeEmbedder:
    """Deterministic embedder; ``vector=None`` simulates a CLOVA failure."""

    def __init__(self, vector: list[float] | None) -> None:
        self._vector = vector

    def embed(self, text: str) -> list[float] | None:
        return self._vector


# ------------------------------------------------- etl.build_vectors contract


def test_build_vectors_then_search_returns_nearest_neighbor(tmp_path: Path) -> None:
    connection = _built_connection(tmp_path, _default_rows())
    query = _vec(0)
    query[1] = 0.1  # near doc A, slightly toward B

    hits = search(connection, "AI 반도체 성장 전략", FakeEmbedder(query))

    assert hits is not None
    assert [hit.product_uid for hit in hits][0] == "GLOBAL_ETP:A"
    assert all(isinstance(hit, VectorHit) for hit in hits)
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(0.995, abs=0.005)


def test_search_scope_filter_restricts_hits(tmp_path: Path) -> None:
    connection = _built_connection(tmp_path, _default_rows())
    query = _vec(3)  # points at the domestic_etp doc

    unfiltered = search(connection, "배당 전략", FakeEmbedder(query))
    filtered = search(connection, "배당 전략", FakeEmbedder(query), scope="overseas_etp")

    assert unfiltered is not None and unfiltered[0].product_uid == "KR_ETP:C"
    assert filtered is not None
    assert {hit.product_uid for hit in filtered} == {"GLOBAL_ETP:A", "GLOBAL_ETP:B"}


def test_search_field_filter_selects_benchmark_channel(tmp_path: Path) -> None:
    connection = _built_connection(tmp_path, _default_rows())

    hits = search(connection, "코스피 벤치마크", FakeEmbedder(_vec(2)), field="benchmark")

    assert hits is not None
    assert [hit.product_uid for hit in hits] == ["FUND:D"]


def test_search_respects_limit(tmp_path: Path) -> None:
    connection = _built_connection(tmp_path, _default_rows())

    hits = search(connection, "전략", FakeEmbedder(_vec(0)), limit=1)

    assert hits is not None and len(hits) == 1


def test_build_vectors_disabled_returns_zero_and_creates_no_table(tmp_path: Path) -> None:
    _install_cache(tmp_path, _default_rows())
    connection = duckdb.connect(":memory:")

    counts = build_vectors(connection, tmp_path, enabled=False)

    assert counts == {"vec_embeddings": 0}
    assert search(connection, "아무 질의", FakeEmbedder(_vec(0))) is None


def test_build_vectors_missing_cache_disabled_returns_zero(tmp_path: Path) -> None:
    connection = duckdb.connect(":memory:")

    counts = build_vectors(connection, tmp_path, enabled=False)

    assert counts == {"vec_embeddings": 0}


def test_build_vectors_missing_cache_enabled_raises_korean_error(tmp_path: Path) -> None:
    connection = duckdb.connect(":memory:")

    with pytest.raises(RuntimeError, match="임베딩 캐시"):
        build_vectors(connection, tmp_path, enabled=True)


def test_build_vectors_dim_mismatch_raises(tmp_path: Path) -> None:
    bad = [
        _cache_row(
            "vec:strategy:GLOBAL_ETP:BAD",
            "GLOBAL_ETP:BAD",
            "overseas_etp",
            "strategy",
            _vec(0, dim=8),
            dim=8,
        )
    ]
    _install_cache(tmp_path, bad)
    connection = duckdb.connect(":memory:")

    with pytest.raises(RuntimeError, match="차원 불일치"):
        build_vectors(connection, tmp_path, enabled=True)


def test_build_vectors_duplicate_doc_id_raises(tmp_path: Path) -> None:
    row = _default_rows()[0]
    _install_cache(tmp_path, [row, dict(row)])
    connection = duckdb.connect(":memory:")

    with pytest.raises(RuntimeError, match="doc_id 중복"):
        build_vectors(connection, tmp_path, enabled=True)


def test_build_vectors_empty_cache_returns_zero_and_search_degrades(tmp_path: Path) -> None:
    _install_cache(tmp_path, [])
    connection = duckdb.connect(":memory:")

    counts = build_vectors(connection, tmp_path, enabled=True)

    assert counts == {"vec_embeddings": 0}
    assert search(connection, "질의", FakeEmbedder(_vec(0))) is None


# --------------------------------------------------- search degrade contract


def test_search_returns_none_when_embedder_fails(tmp_path: Path) -> None:
    connection = _built_connection(tmp_path, _default_rows())

    assert search(connection, "임베딩 실패 질의", FakeEmbedder(None)) is None


def test_search_returns_none_without_vec_table() -> None:
    connection = duckdb.connect(":memory:")

    assert search(connection, "테이블 없음", FakeEmbedder(_vec(0))) is None


def test_search_returns_none_on_oversized_query_vector(tmp_path: Path) -> None:
    connection = _built_connection(tmp_path, _default_rows())
    oversized = [0.1] * (EMBEDDING_DIM + 1)

    assert search(connection, "차원 초과", FakeEmbedder(oversized)) is None


def test_search_returns_none_on_zero_query_vector(tmp_path: Path) -> None:
    connection = _built_connection(tmp_path, _default_rows())

    assert search(connection, "영벡터", FakeEmbedder([0.0, 0.0, 0.0])) is None


def test_search_pads_short_query_vectors(tmp_path: Path) -> None:
    connection = _built_connection(tmp_path, _default_rows())

    hits = search(connection, "짧은 벡터", FakeEmbedder([1.0, 0.2]))

    assert hits is not None and hits[0].product_uid == "GLOBAL_ETP:A"


# ------------------------------------------------- ClovaQueryEmbedder (runtime)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_clova_query_embedder_returns_none_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated 1.5s budget breach", request=request)

    embedder = ClovaQueryEmbedder("test-secret", client=_client(handler))

    assert embedder.embed("연결 실패 질의") is None


def test_clova_query_embedder_returns_none_on_http_500() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"status": {"code": "50000"}})

    embedder = ClovaQueryEmbedder("test-secret", client=_client(handler))

    assert embedder.embed("서버 오류") is None


def test_clova_query_embedder_returns_none_on_malformed_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"no_embedding": True}})

    embedder = ClovaQueryEmbedder("test-secret", client=_client(handler))

    assert embedder.embed("본문 불량") is None


def test_clova_query_embedder_success_uses_bearer_and_parses_embedding() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"result": {"embedding": [0.25] * EMBEDDING_DIM}})

    embedder = ClovaQueryEmbedder("test-secret", client=_client(handler))
    vector = embedder.embed("정상 질의")

    assert vector == [0.25] * EMBEDDING_DIM
    assert seen["authorization"] == "Bearer test-secret"
    assert str(seen["url"]).startswith("https://clovastudio.stream.ntruss.com/")


# ------------------------------------------- scripts.build_embeddings (offline)


def test_embedding_client_retries_429_with_bounded_backoff() -> None:
    calls = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0.05"})
        if calls == 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"result": {"embedding": [0.1] * EMBEDDING_DIM}})

    client = ClovaEmbeddingClient("test-secret", client=_client(handler), sleep=delays.append)

    assert client.embed_text("재시도 텍스트") == [0.1] * EMBEDDING_DIM
    assert calls == 3
    assert delays == [0.05, 2.0]


def test_embedding_client_429_exhaustion_fails_without_key_leak() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    client = ClovaEmbeddingClient(
        "test-secret", client=_client(handler), max_retries_429=3, sleep=lambda _: None
    )

    with pytest.raises(RuntimeError) as excinfo:
        client.embed_text("한도 초과 텍스트")
    assert "429" in str(excinfo.value)
    assert "test-secret" not in str(excinfo.value)
    assert "한도 초과 텍스트" not in str(excinfo.value)
    assert calls == 4  # initial attempt + 3 bounded retries


def test_embedding_client_rejects_wrong_dimension() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"embedding": [0.1] * 8}})

    client = ClovaEmbeddingClient("test-secret", client=_client(handler))

    with pytest.raises(RuntimeError, match="1024"):
        client.embed_text("차원 확인 텍스트")


class CountingFakeApi:
    """Stands in for ClovaEmbeddingClient inside build_cache (no network)."""

    def __init__(self) -> None:
        self.calls = 0

    def embed_text(self, text: str) -> list[float]:
        self.calls += 1
        return [float(len(text))] + [0.0] * (EMBEDDING_DIM - 1)


def _seed_source_database(path: Path) -> None:
    connection = duckdb.connect(str(path))
    try:
        connection.execute("CREATE SCHEMA serving")
        connection.execute(
            "CREATE TABLE serving.product_catalog("
            "product_uid VARCHAR, scope VARCHAR, strategy VARCHAR, benchmark VARCHAR)"
        )
        connection.execute(
            """
            INSERT INTO serving.product_catalog VALUES
            ('GLOBAL_ETP:X', 'overseas_etp', 'AI 테마 성장주 전략', 'S&P 500'),
            ('GLOBAL_ETP:Y', 'overseas_etp', NULL,
             'Index is not provided by Management Company'),
            ('FUND:Z', 'fund', NULL, 'KOSPI 200'),
            ('KR_ETP:W', 'domestic_etp', '국내 배당 전략',
             'Index is not available on Lipper Database')
            """
        )
    finally:
        connection.close()


def test_build_cache_is_idempotent_and_excludes_sentinels(tmp_path: Path) -> None:
    database = tmp_path / "source.duckdb"
    out = tmp_path / "artifacts" / "embeddings" / "embeddings_cache.parquet"
    _seed_source_database(database)

    api = CountingFakeApi()
    first = build_cache(
        database=database, out=out, batch_size=2, limit=None, client=api, model_id="m1"
    )
    assert first["total_docs"] == 3
    assert first["newly_embedded_docs"] == 3
    assert api.calls == 3  # distinct texts only; sentinel benchmarks excluded

    table = pq.read_table(out)
    doc_ids = sorted(str(value) for value in table.column("doc_id").to_pylist())
    assert doc_ids == [
        "vec:benchmark:FUND:Z",
        "vec:benchmark:GLOBAL_ETP:X",
        "vec:strategy:GLOBAL_ETP:X",
    ]
    assert all(dim == EMBEDDING_DIM for dim in table.column("dim").to_pylist())

    second = build_cache(
        database=database, out=out, batch_size=2, limit=None, client=api, model_id="m1"
    )
    assert second["reused_docs"] == 3
    assert second["newly_embedded_docs"] == 0
    assert api.calls == 3  # unchanged texts trigger zero new API calls

    # The committed cache round-trips through the ETL stage.
    connection = duckdb.connect(":memory:")
    counts = build_vectors(connection, tmp_path, enabled=True)
    assert counts == {"vec_embeddings": 3}
