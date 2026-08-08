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
    connection: duckdb.DuckDBPyConnection, text: str
) -> list[str]:
    """Exact alias first, then mechanical normalization — never fuzzy."""

    if not _kg_available(connection):
        return []
    rows = connection.execute(
        "SELECT DISTINCT node_id FROM kg.kg_alias "
        "WHERE alias = ? AND node_id LIKE 'party:%'",
        [text],
    ).fetchall()
    if not rows:
        rows = connection.execute(
            "SELECT DISTINCT node_id FROM kg.kg_alias "
            "WHERE alias = ? AND alias_kind = 'NORMALIZED'",
            [normalize_party(text)],
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
    """1-hop: party → products having a role edge to it."""

    party_nodes = resolve_party_nodes(connection, party_text)
    if not party_nodes:
        return []
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
            path_note=f"{party_text} -[{row[1]}]-> product (row_hash {str(row[2])[:12]}…)",
        )
        for row in rows
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
