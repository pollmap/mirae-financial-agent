"""Relation traversal over the materialized knowledge graph.

Pure read-only SQL against the ``kg`` schema built at ETL time. Degrades
gracefully (returns empty results) when the database predates the KG stage so
the LIKE-based fallback paths keep working on old snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from app.semantics.normalize import normalize_party

_MAX_DEPTH = 2


@dataclass(frozen=True, slots=True)
class GraphHit:
    product_uid: str
    path_note: str


def _kg_available(connection: duckdb.DuckDBPyConnection) -> bool:
    try:
        connection.execute("SELECT 1 FROM kg.kg_node LIMIT 1")
        return True
    except duckdb.Error:
        return False


def resolve_party_nodes(
    connection: duckdb.DuckDBPyConnection, text: str, *, scope: str | None = None
) -> list[str]:
    """Exact alias first, then mechanical normalization — never fuzzy."""

    if not _kg_available(connection):
        return []
    scope_clause = " AND n.scope = ?" if scope else ""
    params: list[object] = [text]
    if scope:
        params.append(scope)
    rows = connection.execute(
        "SELECT DISTINCT a.node_id FROM kg.kg_alias a "
        "JOIN kg.kg_node n ON n.node_id=a.node_id "
        "WHERE a.alias = ? AND a.node_id LIKE 'party:%'" + scope_clause,
        params,
    ).fetchall()
    if not rows:
        params = [normalize_party(text)]
        if scope:
            params.append(scope)
        rows = connection.execute(
            "SELECT DISTINCT a.node_id FROM kg.kg_alias a "
            "JOIN kg.kg_node n ON n.node_id=a.node_id "
            "WHERE a.alias = ? AND a.alias_kind = 'NORMALIZED'" + scope_clause,
            params,
        ).fetchall()
    return [str(row[0]) for row in rows]


def resolve_product_nodes_by_name(
    connection: duckdb.DuckDBPyConnection, text: str
) -> list[str]:
    """Exact official-name/short-name/code alias match to product UIDs."""

    if not _kg_available(connection):
        return []
    rows = connection.execute(
        "SELECT DISTINCT node_id FROM kg.kg_alias "
        "WHERE alias = ? AND node_id LIKE 'product:%'",
        [text],
    ).fetchall()
    return [str(row[0]).removeprefix("product:") for row in rows]


def products_for_party(
    connection: duckdb.DuckDBPyConnection,
    party_text: str,
    *,
    roles: tuple[str, ...] = ("managedBy", "issuedBy"),
    scope: str | None = None,
) -> list[GraphHit]:
    """Bounded 1–2-hop traversal from a scoped party to official products."""

    party_nodes = resolve_party_nodes(connection, party_text, scope=scope)
    if not party_nodes:
        return []
    reachable = set(traverse(connection, party_nodes, roles, max_depth=2))
    placeholders = ", ".join("?" for _ in party_nodes)
    role_placeholders = ", ".join("?" for _ in roles)
    scope_clause = " AND n.scope = ?" if scope else ""
    params: list[object] = [*party_nodes, *roles]
    if scope:
        params.append(scope)
    rows = connection.execute(
        f"""
        SELECT DISTINCT e.src_node_id, e.edge_type, e.source_row_hash
        FROM kg.kg_edge e
        JOIN kg.kg_node n ON n.node_id = e.src_node_id
        WHERE e.dst_node_id IN ({placeholders})
          AND e.edge_type IN ({role_placeholders}){scope_clause}
        ORDER BY e.src_node_id
        """,
        params,
    ).fetchall()
    return [
        GraphHit(
            product_uid=str(row[0]).removeprefix("product:"),
            path_note=(
                f"{party_text} -[{row[1]}]-> product; bounded_depth<=2 "
                f"(row_hash {str(row[2])[:12]}…)"
            ),
        )
        for row in rows
        if str(row[0]).removeprefix("product:") in reachable
    ]


_CONCEPT_RELATIONS: dict[str, tuple[str, str]] = {
    "asset_type": ("CONCEPT_VALUE", "hasAssetType"),
    "region": ("CONCEPT_VALUE", "inRegion"),
    "risk_grade": ("CONCEPT_VALUE", "hasRiskGrade"),
    "benchmark": ("BENCHMARK", "tracksBenchmark"),
}


def products_for_concept_value(
    connection: duckdb.DuckDBPyConnection,
    field: str,
    value: str,
    *,
    scope: str,
) -> list[GraphHit]:
    """Resolve one official relation value and traverse it back to products."""

    relation = _CONCEPT_RELATIONS.get(field)
    if relation is None or not _kg_available(connection):
        return []
    node_type, edge_type = relation
    scope_clause = " AND n.scope = ?" if node_type == "CONCEPT_VALUE" else ""
    params: list[object] = [value, value, node_type]
    if scope_clause:
        params.append(scope)
    rows = connection.execute(
        """
        SELECT DISTINCT n.node_id
        FROM kg.kg_node n
        LEFT JOIN kg.kg_alias a ON a.node_id=n.node_id
        WHERE (n.label = ? OR a.alias = ?) AND n.node_type = ?
        """
        + scope_clause
        + " ORDER BY n.node_id",
        params,
    ).fetchall()
    node_ids = [str(row[0]) for row in rows]
    reachable = set(traverse(connection, node_ids, (edge_type,), max_depth=2))
    if not reachable:
        return []
    placeholders = ", ".join("?" for _ in node_ids)
    edge_rows = connection.execute(
        f"""
        SELECT DISTINCT e.src_node_id, e.source_row_hash
        FROM kg.kg_edge e
        JOIN kg.kg_node p ON p.node_id=e.src_node_id
        WHERE e.dst_node_id IN ({placeholders})
          AND e.edge_type=? AND p.scope=?
        ORDER BY e.src_node_id
        """,
        [*node_ids, edge_type, scope],
    ).fetchall()
    return [
        GraphHit(
            product_uid=uid,
            path_note=(
                f"{value} -[{edge_type}]-> product; bounded_depth<=2 "
                f"(row_hash {str(row_hash)[:12]}…)"
            ),
        )
        for node_id, row_hash in edge_rows
        if (uid := str(node_id).removeprefix("product:")) in reachable
    ]


def traverse(
    connection: duckdb.DuckDBPyConnection,
    start_nodes: list[str],
    edge_types: tuple[str, ...],
    *,
    max_depth: int = _MAX_DEPTH,
) -> list[str]:
    """Bounded recursive traversal returning reachable product node ids."""

    if not start_nodes or not _kg_available(connection):
        return []
    node_placeholders = ", ".join("?" for _ in start_nodes)
    type_placeholders = ", ".join("?" for _ in edge_types)
    rows = connection.execute(
        f"""
        WITH RECURSIVE walk(node_id, depth) AS (
            SELECT node_id, 0 FROM kg.kg_node WHERE node_id IN ({node_placeholders})
            UNION
            SELECT CASE WHEN e.src_node_id = w.node_id
                        THEN e.dst_node_id ELSE e.src_node_id END,
                   w.depth + 1
            FROM walk w
            JOIN kg.kg_edge e
              ON (e.src_node_id = w.node_id OR e.dst_node_id = w.node_id)
             AND e.edge_type IN ({type_placeholders})
            WHERE w.depth < ?
        )
        SELECT DISTINCT node_id FROM walk WHERE node_id LIKE 'product:%'
        ORDER BY node_id
        """,
        [*start_nodes, *edge_types, max_depth],
    ).fetchall()
    return [str(row[0]).removeprefix("product:") for row in rows]
