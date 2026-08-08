"""Knowledge-graph build stage: nodes, edges, aliases materialized in DuckDB.

Runs inside ``_create_database`` after the serving tables exist. Every edge is
anchored to the source row it came from (``source_row_hash``), party roles
follow the product type (ETF → managedBy, ETN → issuedBy, bond → issuedBy,
fund → managedByCode; fund manager names are NOT invented — the official data
only provides opaque codes), and the two overseas benchmark sentinel strings
are excluded as non-values.

Entity resolution is deliberately conservative: names are merged only when
they are identical after mechanical normalization (whitespace, corporate
suffixes, casefold) *and* occur within the same product scope. ``한국투자``
and ``한국투자증권`` never merge (different normalized strings). A domestic-ETP
manager and a bond issuer that happen to normalize to the same string also
never merge into one party node -- e.g. two unrelated firms both named
"Value Partners" but incorporated as "Ltd" in one filing and "LLC" in
another, in different scopes, would otherwise collide since normalize_party
treats those corporate suffixes as interchangeable. Party node ids therefore
encode scope: ``party:<scope>:<normalized>``, not just ``party:<normalized>``.
"""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pandas as pd

from app.semantics.normalize import normalize_party

BENCHMARK_SENTINELS = (
    "Index is not provided by Management Company",
    "Index is not available on Lipper Database",
)

CONCEPT_FIELDS = ("asset_type", "region", "risk_grade")


def _party_frame(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per (scope, role, original manager/issuer string)."""

    rows = connection.execute(
        """
        SELECT scope,
               CASE
                   WHEN scope = 'bond' THEN 'issuedBy'
                   WHEN internal_type = 'ETN' THEN 'issuedBy'
                   ELSE 'managedBy'
               END AS role,
               CASE WHEN scope = 'bond' THEN issuer ELSE manager END AS raw_name,
               COUNT(*) AS product_count
        FROM serving.product_catalog
        WHERE (scope = 'bond' AND issuer IS NOT NULL)
           OR (scope IN ('domestic_etp', 'overseas_etp') AND manager IS NOT NULL)
        GROUP BY 1, 2, 3
        """
    ).fetch_df()
    rows["normalized"] = rows["raw_name"].map(normalize_party)
    return rows


def build_kg(
    connection: duckdb.DuckDBPyConnection, package_root: Path
) -> dict[str, int]:
    connection.execute("CREATE SCHEMA IF NOT EXISTS kg")

    # ------------------------------------------------------------- nodes
    connection.execute(
        """
        CREATE TABLE kg.kg_node AS
        SELECT 'product:' || product_uid AS node_id,
               'PRODUCT' AS node_type,
               name AS label,
               scope,
               FALSE AS is_inferred,
               CAST(NULL AS VARCHAR) AS evidence_note
        FROM serving.product_catalog
        """
    )

    parties = _party_frame(connection)
    canonical = (
        parties.sort_values("product_count", ascending=False)
        .groupby(["scope", "normalized"], as_index=False)
        .agg(label=("raw_name", "first"), variant_count=("raw_name", "nunique"))
    )
    canonical["node_id"] = "party:" + canonical["scope"] + ":" + canonical["normalized"]
    canonical["node_type"] = "PARTY"
    canonical["is_inferred"] = canonical["variant_count"] > 1
    canonical["evidence_note"] = canonical["variant_count"].map(
        lambda count: f"표기 변형 {count}건 정규화 병합" if count > 1 else None
    )
    connection.register("_kg_party_nodes", canonical)
    connection.execute(
        """
        INSERT INTO kg.kg_node
        SELECT node_id, node_type, label,
               CAST(scope AS VARCHAR), is_inferred, CAST(evidence_note AS VARCHAR)
        FROM _kg_party_nodes
        """
    )
    connection.unregister("_kg_party_nodes")

    connection.execute(
        """
        INSERT INTO kg.kg_node
        SELECT DISTINCT 'party_code:' || manager_code,
               'PARTY_CODE',
               manager_code,
               'fund',
               FALSE,
               '공식 데이터에 코드-명칭 매핑이 없어 코드 노드만 유지'
        FROM serving.product_catalog
        WHERE scope = 'fund' AND manager_code IS NOT NULL
        """
    )

    for field in CONCEPT_FIELDS:
        connection.execute(
            f"""
            INSERT INTO kg.kg_node
            SELECT DISTINCT 'val:{field}:' || scope || ':' || {field},
                   'CONCEPT_VALUE',
                   {field},
                   scope,
                   FALSE,
                   NULL
            FROM serving.product_catalog
            WHERE {field} IS NOT NULL
            """
        )

    connection.execute(
        f"""
        INSERT INTO kg.kg_node
        SELECT DISTINCT 'benchmark:' || md5(benchmark),
               'BENCHMARK',
               benchmark,
               CAST(NULL AS VARCHAR),
               FALSE,
               CAST(NULL AS VARCHAR)
        FROM serving.product_catalog
        WHERE benchmark IS NOT NULL
          AND benchmark NOT IN ('{BENCHMARK_SENTINELS[0]}', '{BENCHMARK_SENTINELS[1]}')
        """
    )

    # ------------------------------------------------------------- edges
    connection.register("_kg_party_map", parties)
    # Every text column is explicitly CAST to VARCHAR: this is the table's
    # first CREATE TABLE AS SELECT, so DuckDB infers kg_edge's column types
    # from this result set alone. If _kg_party_map (built from the parties
    # pandas frame) is ever empty -- e.g. a dataset build with zero bond/ETP
    # rows carrying an issuer/manager, which cannot happen against the real
    # 145K-row source but does happen in a narrow unit-test fixture -- an
    # empty pandas frame's columns can register with DuckDB as a non-VARCHAR
    # type, and the later INSERTs into this same table (which do carry real
    # string data) then fail with a confusing INT32 conversion error instead
    # of the schema simply being VARCHAR throughout as intended.
    connection.execute(
        """
        CREATE TABLE kg.kg_edge AS
        SELECT CAST('e:' || c.product_uid || ':' || m.role AS VARCHAR) AS edge_id,
               CAST('product:' || c.product_uid AS VARCHAR) AS src_node_id,
               CAST('party:' || m.scope || ':' || m.normalized AS VARCHAR) AS dst_node_id,
               CAST(m.role AS VARCHAR) AS edge_type,
               FALSE AS is_inferred,
               1.0 AS confidence,
               CAST(c.source_table_id AS VARCHAR) AS source_table,
               CAST(
                   CASE WHEN c.scope = 'bond' THEN 'PD_PBCM' ELSE 'cu_fund_mgmt_co' END
                   AS VARCHAR
               ) AS source_field,
               CAST(c.source_row_hash AS VARCHAR) AS source_row_hash
        FROM serving.product_catalog c
        JOIN _kg_party_map m
          ON m.scope = c.scope
         AND m.raw_name = CASE WHEN c.scope = 'bond' THEN c.issuer ELSE c.manager END
        """
    )
    connection.unregister("_kg_party_map")

    connection.execute(
        """
        INSERT INTO kg.kg_edge
        SELECT 'e:' || product_uid || ':managedByCode',
               'product:' || product_uid,
               'party_code:' || manager_code,
               'managedByCode',
               FALSE, 1.0, source_table_id, 'or_co_xtn_itt_cd', source_row_hash
        FROM serving.product_catalog
        WHERE scope = 'fund' AND manager_code IS NOT NULL
        """
    )

    edge_types = {"asset_type": "hasAssetType", "region": "inRegion", "risk_grade": "hasRiskGrade"}
    source_fields = {
        ("bond", "asset_type"): "STD_PD_MCLS_NM",
        ("domestic_etp", "asset_type"): "wu_inv_ast_type",
        ("overseas_etp", "asset_type"): "wu_inv_ast_type",
        ("fund", "asset_type"): "or_attr_desc",
        ("domestic_etp", "region"): "wu_inv_rgn",
        ("overseas_etp", "region"): "wu_inv_rgn",
        ("fund", "region"): "fd_ivst_rgn_desc",
        ("bond", "risk_grade"): "PD_RISK_GCD",
        ("domestic_etp", "risk_grade"): "pd_risk_nm",
        ("fund", "risk_grade"): "zrin_fd_ivst_risk_grd_nm",
    }
    for field, edge_type in edge_types.items():
        case_parts = " ".join(
            f"WHEN scope = '{scope}' THEN '{source}'"
            for (scope, mapped_field), source in source_fields.items()
            if mapped_field == field
        )
        connection.execute(
            f"""
            INSERT INTO kg.kg_edge
            SELECT 'e:' || product_uid || ':{edge_type}',
                   'product:' || product_uid,
                   'val:{field}:' || scope || ':' || {field},
                   '{edge_type}',
                   FALSE, 1.0, source_table_id,
                   CASE {case_parts} ELSE '{field}' END,
                   source_row_hash
            FROM serving.product_catalog
            WHERE {field} IS NOT NULL
            """
        )

    connection.execute(
        f"""
        INSERT INTO kg.kg_edge
        SELECT 'e:' || product_uid || ':tracksBenchmark',
               'product:' || product_uid,
               'benchmark:' || md5(benchmark),
               'tracksBenchmark',
               FALSE, 1.0, source_table_id,
               CASE WHEN scope = 'fund' THEN 'bmrk_nm' ELSE 'cu_base_index' END,
               source_row_hash
        FROM serving.product_catalog
        WHERE benchmark IS NOT NULL
          AND benchmark NOT IN ('{BENCHMARK_SENTINELS[0]}', '{BENCHMARK_SENTINELS[1]}')
        """
    )

    # ------------------------------------------------------------ aliases
    connection.execute(
        """
        CREATE TABLE kg.kg_alias AS
        SELECT name AS alias, 'product:' || product_uid AS node_id,
               'OFFICIAL' AS alias_kind, FALSE AS is_inferred
        FROM serving.product_catalog WHERE name IS NOT NULL
        UNION ALL
        SELECT short_name, 'product:' || product_uid, 'OFFICIAL', FALSE
        FROM serving.product_catalog
        WHERE short_name IS NOT NULL AND short_name <> name
        UNION ALL
        SELECT product_id, 'product:' || product_uid, 'CODE', FALSE
        FROM serving.product_catalog WHERE product_id IS NOT NULL
        UNION ALL
        SELECT isin, 'product:' || product_uid, 'CODE', FALSE
        FROM serving.product_catalog WHERE isin IS NOT NULL
        """
    )
    party_aliases = pd.concat(
        [
            parties.assign(
                alias=parties["raw_name"],
                node_id="party:" + parties["scope"] + ":" + parties["normalized"],
                alias_kind="OFFICIAL",
                is_inferred=False,
            )[["alias", "node_id", "alias_kind", "is_inferred"]],
            parties.assign(
                alias=parties["normalized"],
                node_id="party:" + parties["scope"] + ":" + parties["normalized"],
                alias_kind="NORMALIZED",
                is_inferred=False,
            )[["alias", "node_id", "alias_kind", "is_inferred"]],
        ]
    ).drop_duplicates()
    connection.register("_kg_party_aliases", party_aliases)
    connection.execute("INSERT INTO kg.kg_alias SELECT * FROM _kg_party_aliases")
    connection.unregister("_kg_party_aliases")

    value_alias_path = package_root / "registry" / "semantic" / "value_aliases_v1.csv"
    if value_alias_path.exists():
        with value_alias_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = [
                {
                    "alias": row["alias"],
                    "node_id": (
                        f"val:{row['field'].removeprefix('product.')}:"
                        f"{row['scope']}:{row['canonical_value']}"
                    ),
                    "alias_kind": row["alias_kind"],
                    "is_inferred": row["is_inferred"].strip().lower() == "true",
                }
                for row in csv.DictReader(handle)
                if row["field"].removeprefix("product.") in CONCEPT_FIELDS
            ]
        if rows:
            frame = pd.DataFrame(rows)
            connection.register("_kg_value_aliases", frame)
            connection.execute(
                """
                INSERT INTO kg.kg_alias
                SELECT a.alias, a.node_id, a.alias_kind, a.is_inferred
                FROM _kg_value_aliases a
                JOIN kg.kg_node n ON n.node_id = a.node_id
                """
            )
            connection.unregister("_kg_value_aliases")

    connection.execute("CREATE INDEX kg_edge_src_idx ON kg.kg_edge(src_node_id)")
    connection.execute("CREATE INDEX kg_edge_dst_idx ON kg.kg_edge(dst_node_id)")
    connection.execute("CREATE INDEX kg_edge_type_idx ON kg.kg_edge(edge_type)")
    connection.execute("CREATE INDEX kg_alias_idx ON kg.kg_alias(alias)")

    for view in ("kg_node", "kg_edge", "kg_alias"):
        connection.execute(f"CREATE VIEW {view} AS SELECT * FROM kg.{view}")

    counts = {
        "kg_nodes": connection.execute("SELECT COUNT(*) FROM kg.kg_node").fetchone()[0],
        "kg_edges": connection.execute("SELECT COUNT(*) FROM kg.kg_edge").fetchone()[0],
        "kg_aliases": connection.execute("SELECT COUNT(*) FROM kg.kg_alias").fetchone()[0],
    }

    product_nodes = connection.execute(
        "SELECT COUNT(*) FROM kg.kg_node WHERE node_type = 'PRODUCT'"
    ).fetchone()[0]
    serving_products = connection.execute(
        "SELECT COUNT(*) FROM serving.product_catalog"
    ).fetchone()[0]
    if product_nodes != serving_products:
        raise RuntimeError(
            f"KG product nodes {product_nodes} != serving products {serving_products}"
        )
    orphan_edges = connection.execute(
        """
        SELECT COUNT(*) FROM kg.kg_edge e
        LEFT JOIN kg.kg_node s ON s.node_id = e.src_node_id
        LEFT JOIN kg.kg_node d ON d.node_id = e.dst_node_id
        WHERE s.node_id IS NULL OR d.node_id IS NULL
        """
    ).fetchone()[0]
    if orphan_edges:
        raise RuntimeError(f"KG has {orphan_edges} orphan edges")
    return counts
