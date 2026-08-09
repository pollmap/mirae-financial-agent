from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.retrieval.graph_retriever import (  # noqa: E402
    products_for_concept_value,
    products_for_party,
    traverse,
)
from etl.kg import BENCHMARK_SENTINELS, build_kg  # noqa: E402

_COLUMNS = [
    "product_uid",
    "name",
    "short_name",
    "product_id",
    "isin",
    "scope",
    "internal_type",
    "issuer",
    "manager",
    "manager_code",
    "asset_type",
    "region",
    "risk_grade",
    "benchmark",
    "source_table_id",
    "source_row_hash",
]

_DEFAULT_ROW = {column: None for column in _COLUMNS}


def _row(**overrides: object) -> tuple:
    values = {**_DEFAULT_ROW, **overrides}
    return tuple(values[column] for column in _COLUMNS)


def _build(rows: list[tuple], tmp_path: Path) -> tuple[duckdb.DuckDBPyConnection, dict[str, int]]:
    """Build a KG against a synthetic serving.product_catalog, matching the
    same in-memory-connection style test_lexical.py uses for etl/lexical.py --
    no need to run the full real-data ETL pipeline to exercise this stage's
    own role/merge/alias-kind logic.
    """

    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA serving")
    connection.execute(
        f"CREATE TABLE serving.product_catalog ({', '.join(f'{c} VARCHAR' for c in _COLUMNS)})"
    )
    connection.executemany(
        f"INSERT INTO serving.product_catalog VALUES ({', '.join('?' for _ in _COLUMNS)})",
        rows,
    )
    # No registry/semantic/value_aliases_v1.csv under tmp_path: that branch of
    # build_kg is opt-in (`if value_alias_path.exists()`) and covers a plain
    # CSV-join, not this stage's own role/merge logic -- out of scope here.
    counts = build_kg(connection, tmp_path)
    return connection, counts


def test_role_follows_scope_and_internal_type(tmp_path: Path) -> None:
    rows = [
        _row(
            product_uid="B1", name="국민주택채권", scope="bond",
            issuer="대한민국", source_table_id="PRBD01N001", source_row_hash="h1",
        ),
        _row(
            product_uid="E1", name="TIGER 200", scope="domestic_etp", internal_type="ETF",
            manager="미래에셋자산운용", source_table_id="PREF01N001", source_row_hash="h2",
        ),
        _row(
            product_uid="N1", name="삼성 ETN", scope="domestic_etp", internal_type="ETN",
            manager="삼성증권", source_table_id="PREF01N001", source_row_hash="h3",
        ),
        _row(
            product_uid="O1", name="SPY", scope="overseas_etp", internal_type="ETF",
            manager="State Street", source_table_id="PREF02N001", source_row_hash="h4",
        ),
    ]
    connection, _ = _build(rows, tmp_path)
    edges = dict(
        connection.execute(
            "SELECT src_node_id, edge_type FROM kg.kg_edge WHERE edge_type IN ('issuedBy', 'managedBy')"
        ).fetchall()
    )
    assert edges["product:B1"] == "issuedBy"  # bond -> always issuedBy
    assert edges["product:E1"] == "managedBy"  # domestic ETF -> managedBy
    assert edges["product:N1"] == "issuedBy"  # domestic ETN -> issuedBy, not managedBy
    assert edges["product:O1"] == "managedBy"  # overseas ETF -> managedBy


def test_same_name_variants_merge_within_one_scope(tmp_path: Path) -> None:
    rows = [
        _row(
            product_uid="E1", name="A", scope="domestic_etp", internal_type="ETF",
            manager="삼성자산운용", source_table_id="PREF01N001", source_row_hash="h1",
        ),
        _row(
            product_uid="E2", name="B", scope="domestic_etp", internal_type="ETF",
            manager="(주)삼성자산운용", source_table_id="PREF01N001", source_row_hash="h2",
        ),
    ]
    connection, _ = _build(rows, tmp_path)
    party_nodes = connection.execute(
        "SELECT node_id, is_inferred, evidence_note FROM kg.kg_node WHERE node_type = 'PARTY'"
    ).fetchall()
    assert len(party_nodes) == 1
    node_id, is_inferred, evidence_note = party_nodes[0]
    assert node_id == "party:domestic_etp:삼성자산운용"
    assert is_inferred is True
    assert evidence_note == "표기 변형 2건 정규화 병합"
    dst_nodes = {
        row[0]
        for row in connection.execute(
            "SELECT dst_node_id FROM kg.kg_edge WHERE src_node_id IN ('product:E1', 'product:E2')"
        ).fetchall()
    }
    assert dst_nodes == {node_id}


def test_never_merges_genuinely_different_names(tmp_path: Path) -> None:
    rows = [
        _row(
            product_uid="E1", name="A", scope="domestic_etp", internal_type="ETF",
            manager="한국투자", source_table_id="PREF01N001", source_row_hash="h1",
        ),
        _row(
            product_uid="E2", name="B", scope="domestic_etp", internal_type="ETF",
            manager="한국투자증권", source_table_id="PREF01N001", source_row_hash="h2",
        ),
    ]
    connection, _ = _build(rows, tmp_path)
    party_nodes = connection.execute(
        "SELECT label FROM kg.kg_node WHERE node_type = 'PARTY' ORDER BY label"
    ).fetchall()
    assert [row[0] for row in party_nodes] == ["한국투자", "한국투자증권"]


def test_same_normalized_name_in_different_scopes_does_not_merge(tmp_path: Path) -> None:
    """Regression test for the scope-blind groupby bug: a bond issuer and a
    domestic ETP manager that normalize to the same string (corporate-suffix
    stripping treats Ltd/LLC as interchangeable) must NOT collapse into one
    party node just because the normalized text matches -- they are two
    unrelated real-world entities in different product scopes.
    """

    rows = [
        _row(
            product_uid="B1", name="Bond1", scope="bond",
            issuer="Value Partners Ltd", source_table_id="PRBD01N001", source_row_hash="h1",
        ),
        _row(
            product_uid="E1", name="ETF1", scope="domestic_etp", internal_type="ETF",
            manager="Value Partners LLC", source_table_id="PREF01N001", source_row_hash="h2",
        ),
    ]
    connection, _ = _build(rows, tmp_path)
    party_nodes = connection.execute(
        "SELECT node_id, scope, is_inferred FROM kg.kg_node WHERE node_type = 'PARTY' ORDER BY node_id"
    ).fetchall()
    assert party_nodes == [
        ("party:bond:value partners", "bond", False),
        ("party:domestic_etp:value partners", "domestic_etp", False),
    ]
    edges = dict(
        connection.execute(
            "SELECT src_node_id, dst_node_id FROM kg.kg_edge WHERE edge_type IN ('issuedBy', 'managedBy')"
        ).fetchall()
    )
    assert edges["product:B1"] == "party:bond:value partners"
    assert edges["product:E1"] == "party:domestic_etp:value partners"


def test_alias_kinds_and_scoped_lookup(tmp_path: Path) -> None:
    rows = [
        _row(
            product_uid="E1", name="TIGER 200", short_name="TIGER200",
            product_id="A001", isin="KR001", scope="domestic_etp", internal_type="ETF",
            manager="미래에셋자산운용㈜", source_table_id="PREF01N001", source_row_hash="h1",
        ),
    ]
    connection, _ = _build(rows, tmp_path)
    product_aliases = dict(
        connection.execute(
            "SELECT alias, alias_kind FROM kg.kg_alias WHERE node_id = 'product:E1'"
        ).fetchall()
    )
    assert product_aliases == {
        "TIGER 200": "OFFICIAL",
        "TIGER200": "OFFICIAL",
        "A001": "CODE",
        "KR001": "CODE",
    }
    # Raw name carries the ㈜ suffix, normalize_party strips it -- so OFFICIAL
    # (raw) and NORMALIZED (stripped) are two genuinely distinct alias
    # strings pointing at the same scoped party node.
    party_aliases = dict(
        connection.execute(
            "SELECT alias, alias_kind FROM kg.kg_alias "
            "WHERE node_id = 'party:domestic_etp:미래에셋자산운용'"
        ).fetchall()
    )
    assert party_aliases == {
        "미래에셋자산운용㈜": "OFFICIAL",
        "미래에셋자산운용": "NORMALIZED",
    }


def test_benchmark_sentinels_excluded(tmp_path: Path) -> None:
    rows = [
        _row(
            product_uid="O1", name="Real benchmark ETF", scope="overseas_etp", internal_type="ETF",
            manager="Mgr", benchmark="S&P 500", source_table_id="PREF02N001", source_row_hash="h1",
        ),
        _row(
            product_uid="O2", name="Sentinel benchmark ETF", scope="overseas_etp", internal_type="ETF",
            manager="Mgr", benchmark=BENCHMARK_SENTINELS[0],
            source_table_id="PREF02N001", source_row_hash="h2",
        ),
    ]
    connection, _ = _build(rows, tmp_path)
    benchmark_nodes = connection.execute(
        "SELECT label FROM kg.kg_node WHERE node_type = 'BENCHMARK'"
    ).fetchall()
    assert [row[0] for row in benchmark_nodes] == ["S&P 500"]
    benchmark_edges = connection.execute(
        "SELECT src_node_id FROM kg.kg_edge WHERE edge_type = 'tracksBenchmark'"
    ).fetchall()
    assert [row[0] for row in benchmark_edges] == ["product:O1"]


def test_fund_manager_uses_code_only_no_invented_name(tmp_path: Path) -> None:
    rows = [
        _row(
            product_uid="F1", name="미래에셋 코스피 펀드", scope="fund",
            manager_code="00040010", source_table_id="PRFD01N001", source_row_hash="h1",
        ),
    ]
    connection, _ = _build(rows, tmp_path)
    party_nodes = connection.execute(
        "SELECT node_id, node_type FROM kg.kg_node WHERE node_type IN ('PARTY', 'PARTY_CODE')"
    ).fetchall()
    assert party_nodes == [("party_code:00040010", "PARTY_CODE")]
    edge = connection.execute(
        "SELECT dst_node_id, edge_type FROM kg.kg_edge WHERE src_node_id = 'product:F1'"
    ).fetchone()
    assert edge == ("party_code:00040010", "managedByCode")


def test_structural_invariants_hold(tmp_path: Path) -> None:
    rows = [
        _row(
            product_uid="B1", name="Bond1", scope="bond",
            issuer="Issuer A", source_table_id="PRBD01N001", source_row_hash="h1",
        ),
        _row(
            product_uid="E1", name="ETF1", scope="domestic_etp", internal_type="ETF",
            manager="Mgr A", source_table_id="PREF01N001", source_row_hash="h2",
        ),
    ]
    connection, counts = _build(rows, tmp_path)
    assert counts["kg_nodes"] > 0
    assert counts["kg_edges"] > 0
    assert counts["kg_aliases"] > 0
    product_nodes = connection.execute(
        "SELECT COUNT(*) FROM kg.kg_node WHERE node_type = 'PRODUCT'"
    ).fetchone()[0]
    assert product_nodes == len(rows)
    orphans = connection.execute(
        """
        SELECT COUNT(*) FROM kg.kg_edge e
        LEFT JOIN kg.kg_node s ON s.node_id = e.src_node_id
        LEFT JOIN kg.kg_node d ON d.node_id = e.dst_node_id
        WHERE s.node_id IS NULL OR d.node_id IS NULL
        """
    ).fetchone()[0]
    assert orphans == 0


def test_party_lookup_and_bounded_traversal_are_live(tmp_path: Path) -> None:
    rows = [
        _row(
            product_uid="E1", name="ETF1", scope="domestic_etp", internal_type="ETF",
            manager="Manager A", source_table_id="PREF01N001", source_row_hash="a" * 32,
        ),
        _row(
            product_uid="E2", name="ETF2", scope="domestic_etp", internal_type="ETF",
            manager="Manager A", source_table_id="PREF01N001", source_row_hash="b" * 32,
        ),
    ]
    connection, _ = _build(rows, tmp_path)
    hits = products_for_party(
        connection, "Manager A", roles=("managedBy",), scope="domestic_etp"
    )
    assert [hit.product_uid for hit in hits] == ["E1", "E2"]
    assert all(
        "managedBy" in hit.path_note
        and "row_hash" in hit.path_note
        and "bounded_depth<=2" in hit.path_note
        for hit in hits
    )
    assert traverse(
        connection,
        ["party:domestic_etp:manager a"],
        ("managedBy",),
        max_depth=2,
    ) == ["E1", "E2"]


def test_concept_and_benchmark_relations_return_scoped_products(tmp_path: Path) -> None:
    rows = [
        _row(
            product_uid="E1", name="ETF1", scope="domestic_etp", internal_type="ETF",
            manager="Manager A", asset_type="Equity", region="US", risk_grade="High",
            benchmark="S&P 500", source_table_id="PREF01N001", source_row_hash="c" * 32,
        ),
        _row(
            product_uid="O1", name="ETF2", scope="overseas_etp", internal_type="ETF",
            manager="Manager B", asset_type="Equity", region="US",
            benchmark="S&P 500", source_table_id="PREF02N001", source_row_hash="d" * 32,
        ),
    ]
    connection, _ = _build(rows, tmp_path)
    assert [
        hit.product_uid
        for hit in products_for_concept_value(
            connection, "region", "US", scope="domestic_etp"
        )
    ] == ["E1"]
    assert [
        hit.product_uid
        for hit in products_for_concept_value(
            connection, "benchmark", "S&P 500", scope="overseas_etp"
        )
    ] == ["O1"]
