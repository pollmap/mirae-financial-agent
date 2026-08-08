"""Independent expected-answer computation over the serving DuckDB file.

The oracle re-implements the *data contract* with direct SQL and deliberately
imports nothing from ``app``: an expectation computed by the code under test
would be worthless.  Contract mirrored here (kept in sync with the published
engine semantics, not with its code):

- Rankable metric rows: ``product_metrics.quality_status`` in
  ``('PARTIAL','SUSPECT_OUTLIER','VALID','ZERO_VALID')`` and
  ``value_num IS NOT NULL``.
- Common latest as-of: ``MAX(as_of_date)`` over the eligible universe for that
  scope + catalog filters (metric predicates excluded); when no dated rows
  exist the date predicate is dropped.
- Rank order: ``value_num {ASC|DESC} NULLS LAST, product_uid ASC, LIMIT n``.
- Cross-scope unified rank: union of the per-scope top-n lists (each with its
  own per-scope common-latest date), re-sorted by ``(value, product_uid)``
  with ``reverse=True`` for descending — i.e. ties break by uid *descending*
  when the direction is descending, mirroring the fusion sort.
- Currency-partitioned cross metrics get the scope's dominant currency value
  auto-applied, computed from the catalog itself.
- Counts: ``COUNT(DISTINCT product_uid)`` with catalog filters.

The DuckDB connection is opened lazily (read-only) on first use so importing
this module — or generating templates — never touches the database file.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

import duckdb

VALID_VALUE_STATES = ("PARTIAL", "SUSPECT_OUTLIER", "VALID", "ZERO_VALID")

_REGISTRY_ROOT = Path(__file__).resolve().parents[1] / "registry"

# Single-scope rank refusals the live engine enforces (app/execution/registry.py
# MetricRegistry.evaluate). The oracle mirrors these as *data policy*, read
# from the same CSV the engine loads, not by importing engine code: a rank
# spec that would trigger one of these must be scored as an expected refusal,
# not as a numeric-rows mismatch against a policy the app correctly applies.
_CURRENCY_PARTITION_SUFFIXES = (".issue_amount", ".net_assets", ".aum_last")
_CURRENCY_PARTITION_MARKERS = ("CURRENCY", "KRW", "PRICE")


@lru_cache(maxsize=1)
def _metric_unit_status() -> dict[str, str]:
    path = _REGISTRY_ROOT / "metric_policy_v1.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["metric_id"]: row["unit_status"] for row in csv.DictReader(handle)}


def _requires_currency_partition(metric_id: str) -> bool:
    unit_status = _metric_unit_status().get(metric_id, "")
    return metric_id.endswith(_CURRENCY_PARTITION_SUFFIXES) or any(
        marker in unit_status for marker in _CURRENCY_PARTITION_MARKERS
    )


def _has_currency_filter(filters: list[dict[str, object]] | None) -> bool:
    return any(
        condition.get("column") in ("currency", "trading_currency") for condition in filters or []
    )


def _single_scope_rank_refusal(
    scope: str,
    metric_id: str,
    direction: str,
    filters: list[dict[str, object]] | None,
) -> dict[str, object] | None:
    """Mirror ``MetricRegistry.evaluate``'s single-scope rank gates.

    Returns a ``behavior`` expectation payload when the live engine would
    refuse this exact (scope, metric, direction, filters) combination, else
    ``None`` to proceed with a normal numeric-rank expectation.
    """

    filters = filters or []

    def _eq_filter(column: str, value: object) -> bool:
        return any(
            condition.get("column") == column and condition.get("value") == value
            for condition in filters
        )

    if metric_id == "bond.buyable_quantity":
        # The eval filter schema (ALLOWED_FILTER_COLUMNS) has no numeric
        # operator, so a template can never encode the required "> 0"
        # condition; this metric always hits the engine's positive-quantity
        # gate when ranked from a template-generated question.
        return {"reason_code": "POSITIVE_QUANTITY_FILTER_REQUIRED"}

    if metric_id == "domestic_etp.aum_last":
        if _eq_filter("internal_type", "ETN"):
            return {"reason_code": "ETN_AUM_CONSTANT_ZERO"}
        if not _eq_filter("internal_type", "ETF"):
            return {"reason_code": "ETF_SUBTYPE_FILTER_REQUIRED"}

    if metric_id == "overseas_etp.aum_last" and not _has_currency_filter(filters):
        return {"reason_code": "TRADING_CURRENCY_FILTER_REQUIRED"}

    if _requires_currency_partition(metric_id) and not _has_currency_filter(filters):
        return {"reason_code": "CURRENCY_FILTER_REQUIRED"}

    if metric_id.startswith("domestic_etp.return_") and direction == "asc":
        return {"reason_code": "MINUS_100_SENTINEL_POLICY_PENDING"}

    return None

# Whitelisted serving catalog columns a spec filter may reference.
ALLOWED_FILTER_COLUMNS = frozenset(
    {
        "internal_type",
        "asset_type",
        "region",
        "risk_grade",
        "sale_status",
        "public_private",
        "market",
        "currency",
        "trading_currency",
        "manager",
        "issuer",
    }
)


class Oracle:
    """Compute expected answers for template specs via direct read-only SQL."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._connection: duckdb.DuckDBPyConnection | None = None

    # -- connection lifecycle -------------------------------------------------

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            self._connection = duckdb.connect(str(self.database_path), read_only=True)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Oracle:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- SQL helpers ----------------------------------------------------------

    @staticmethod
    def _filters_sql(
        filters: list[dict[str, object]] | None, *, scope: str | None = None
    ) -> tuple[str, list[object]]:
        sql = ""
        params: list[object] = []
        for condition in filters or []:
            column = str(condition["column"])
            if column not in ALLOWED_FILTER_COLUMNS:
                raise ValueError(f"filter column not allow-listed: {column}")
            value = condition["value"]
            if column == "sale_status" and scope in ("domestic_etp", "overseas_etp"):
                # Mirrors engine.py's execution-time translation: these two
                # scopes store sale_status as a raw '1'/'0' code, and the
                # Korean label only ever appears in the plan/question text.
                value = {"판매중": "1", "판매완료": "0"}.get(str(value), value)
            sql += f" AND c.{column} = ?"
            params.append(value)
        return sql, params

    def _latest_date(
        self, scope: str, metric_id: str, filters: list[dict[str, object]] | None
    ) -> object | None:
        filter_sql, filter_params = self._filters_sql(filters, scope=scope)
        placeholders = ",".join("?" for _ in VALID_VALUE_STATES)
        row = self.connection.execute(
            "SELECT MAX(m.as_of_date) FROM product_catalog c "
            "JOIN product_metrics m ON m.product_uid = c.product_uid "
            "AND m.metric_id = ? AND m.value_num IS NOT NULL "
            f"AND m.quality_status IN ({placeholders}) "
            "WHERE c.scope = ? AND m.as_of_date IS NOT NULL" + filter_sql,
            [metric_id, *VALID_VALUE_STATES, scope, *filter_params],
        ).fetchone()
        return row[0] if row else None

    def _rank_rows(
        self,
        scope: str,
        metric_id: str,
        direction: str,
        n: int,
        filters: list[dict[str, object]] | None,
    ) -> list[tuple[str, float]]:
        if direction not in {"asc", "desc"}:
            raise ValueError(f"direction must be asc or desc, got {direction!r}")
        filter_sql, filter_params = self._filters_sql(filters, scope=scope)
        placeholders = ",".join("?" for _ in VALID_VALUE_STATES)
        latest = self._latest_date(scope, metric_id, filters)
        date_sql = " AND m.as_of_date = ?" if latest is not None else ""
        date_params = [latest] if latest is not None else []
        rows = self.connection.execute(
            "SELECT c.product_uid, m.value_num FROM product_catalog c "
            "JOIN product_metrics m ON m.product_uid = c.product_uid "
            "AND m.metric_id = ? AND m.value_num IS NOT NULL "
            f"AND m.quality_status IN ({placeholders})" + date_sql + " "
            "WHERE c.scope = ?" + filter_sql + " "
            f"ORDER BY m.value_num {direction.upper()} NULLS LAST, c.product_uid ASC LIMIT ?",
            [metric_id, *VALID_VALUE_STATES, *date_params, scope, *filter_params, n],
        ).fetchall()
        return [(str(uid), float(value)) for uid, value in rows]

    def _count(self, scope: str, filters: list[dict[str, object]] | None) -> int:
        filter_sql, filter_params = self._filters_sql(filters, scope=scope)
        row = self.connection.execute(
            "SELECT COUNT(DISTINCT c.product_uid) FROM product_catalog c "
            "WHERE c.scope = ?" + filter_sql,
            [scope, *filter_params],
        ).fetchone()
        return int(row[0]) if row else 0

    def _search_uids(
        self, scope: str, n: int, filters: list[dict[str, object]] | None
    ) -> list[str]:
        filter_sql, filter_params = self._filters_sql(filters, scope=scope)
        rows = self.connection.execute(
            "SELECT c.product_uid FROM product_catalog c WHERE c.scope = ?"
            + filter_sql
            + " ORDER BY c.product_uid ASC LIMIT ?",
            [scope, *filter_params, n],
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _metric_row(
        self, product_uid: str, metric_id: str
    ) -> tuple[float | None, str | None, object | None] | None:
        row = self.connection.execute(
            "SELECT value_num, quality_status, as_of_date FROM product_metrics "
            "WHERE product_uid = ? AND metric_id = ?",
            [product_uid, metric_id],
        ).fetchone()
        return None if row is None else (row[0], row[1], row[2])

    def _catalog_value(self, product_uid: str, column: str) -> str | None:
        if column not in ALLOWED_FILTER_COLUMNS and column not in ("currency", "trading_currency"):
            raise ValueError(f"catalog column not allow-listed: {column}")
        row = self.connection.execute(
            f"SELECT {column} FROM product_catalog WHERE product_uid = ?", [product_uid]
        ).fetchone()
        return None if row is None else (None if row[0] is None else str(row[0]))

    def _compare_refusal(
        self, uids: list[str], metric_id: str
    ) -> dict[str, object] | None:
        """Mirror ``DuckDBEngine._comparison_context_error`` for two products.

        Checked in the same order the engine checks them: value presence and
        usable quality, then as-of consistency, then (for currency-partitioned
        metrics) currency consistency. Returns a ``behavior`` refusal payload
        when the live engine would refuse, else ``None``.
        """

        rows = [self._metric_row(uid, metric_id) for uid in uids]
        for row in rows:
            if row is None or row[0] is None or row[1] not in VALID_VALUE_STATES:
                return {"reason_code": "COMPARISON_VALUE_UNAVAILABLE"}
        dates = [row[2] for row in rows]  # type: ignore[index]
        known_dates = sorted({d for d in dates if d is not None})
        if len(known_dates) > 1:
            return {"reason_code": "AS_OF_DATE_MISMATCH"}
        if known_dates and any(d is None for d in dates):
            return {"reason_code": "AS_OF_CONTEXT_MISSING"}
        if _requires_currency_partition(metric_id):
            currencies = [
                self._catalog_value(uid, "trading_currency")
                if "overseas" in metric_id
                else self._catalog_value(uid, "currency")
                for uid in uids
            ]
            if any(c is None for c in currencies):
                return {"reason_code": "CURRENCY_CONTEXT_MISSING"}
            if len(set(currencies)) > 1:
                return {"reason_code": "CURRENCY_MISMATCH"}
        return None

    def _uids_for_code(self, scope: str, code: str) -> list[str]:
        rows = self.connection.execute(
            "SELECT DISTINCT c.product_uid FROM product_catalog c WHERE c.scope = ? "
            "AND (LOWER(CAST(c.product_id AS VARCHAR)) = LOWER(?) "
            "OR LOWER(CAST(c.alt_id AS VARCHAR)) = LOWER(?) "
            "OR LOWER(CAST(c.isin AS VARCHAR)) = LOWER(?)) "
            "ORDER BY c.product_uid",
            [scope, code, code, code],
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _dominant_value(self, scope: str, column: str) -> str | None:
        if column not in ALLOWED_FILTER_COLUMNS:
            raise ValueError(f"partition column not allow-listed: {column}")
        row = self.connection.execute(
            f"SELECT {column} FROM product_catalog WHERE scope = ? AND {column} IS NOT NULL "
            "GROUP BY 1 ORDER BY COUNT(*) DESC, 1 ASC LIMIT 1",
            [scope],
        ).fetchone()
        return None if row is None else str(row[0])

    def sample_codes(self, per_scope: int = 16) -> dict[str, list[str]]:
        """First N product_ids per scope ordered by product_uid (deterministic)."""

        codes: dict[str, list[str]] = {}
        for scope in ("bond", "domestic_etp", "overseas_etp", "fund"):
            rows = self.connection.execute(
                "SELECT CAST(c.product_id AS VARCHAR) FROM product_catalog c "
                "WHERE c.scope = ? AND c.product_id IS NOT NULL "
                "AND TRIM(CAST(c.product_id AS VARCHAR)) <> '' "
                "ORDER BY c.product_uid ASC LIMIT ?",
                [scope, per_scope],
            ).fetchall()
            codes[scope] = [str(row[0]) for row in rows]
        return codes

    # -- public API -----------------------------------------------------------

    def expected(self, spec: dict[str, object]) -> dict[str, object]:
        """Return the expected-answer descriptor for one filled template spec."""

        expect_kind = str(spec.get("expect_kind"))
        if expect_kind == "rank":
            scope = str(spec["scope"])
            metric_id = str(spec["metric_id"])
            direction = str(spec["direction"])
            filters = spec.get("filters")
            refusal = _single_scope_rank_refusal(
                scope, metric_id, direction, filters  # type: ignore[arg-type]
            )
            if refusal is not None:
                return {
                    "kind": "behavior",
                    "answerability": ["DATA_QUALITY_BLOCKED"],
                    "reason_code": refusal["reason_code"],
                    "disclosure_required": False,
                }
            rows = self._rank_rows(
                scope,
                metric_id,
                direction,
                int(spec["n"]),  # type: ignore[arg-type]
                filters,  # type: ignore[arg-type]
            )
            return {
                "kind": "rank",
                "product_uids": [uid for uid, _ in rows],
                "values": [value for _, value in rows],
            }
        if expect_kind == "search":
            scope = str(spec["scope"])
            filters = spec.get("filters")
            return {
                "kind": "search",
                "product_uids": self._search_uids(scope, int(spec["n"]), filters),  # type: ignore[arg-type]
                "total": self._count(scope, filters),  # type: ignore[arg-type]
            }
        if expect_kind == "count":
            return {
                "kind": "count",
                "value": self._count(str(spec["scope"]), spec.get("filters")),  # type: ignore[arg-type]
            }
        if expect_kind == "group_count":
            filters_by_scope = spec.get("filters_by_scope") or {}
            return {
                "kind": "group_count",
                "value": {
                    scope: self._count(scope, filters)
                    for scope, filters in filters_by_scope.items()  # type: ignore[union-attr]
                },
            }
        if expect_kind == "lookup":
            return {
                "kind": "lookup",
                "product_uids": self._uids_for_code(str(spec["scope"]), str(spec["code"])),
            }
        if expect_kind == "compare":
            scope = str(spec["scope"])
            uids: list[str] = []
            for slot in ("code_a", "code_b"):
                uids.extend(self._uids_for_code(scope, str(spec[slot])))
            unique_uids = sorted(set(uids))
            metric_id = spec.get("metric_id")
            if metric_id and len(unique_uids) == 2:
                refusal = self._compare_refusal(unique_uids, str(metric_id))
                if refusal is not None:
                    return {
                        "kind": "behavior",
                        "answerability": ["INCOMPARABLE"],
                        "reason_code": refusal["reason_code"],
                        "disclosure_required": False,
                    }
            return {"kind": "compare", "product_uids": unique_uids}
        if expect_kind == "cross_rank":
            return self._expected_cross_rank(spec)
        if expect_kind == "cross_split_rank":
            return self._expected_cross_split_rank(spec)
        if expect_kind == "cross_behavior":
            return {
                "kind": "behavior",
                "answerability": None,
                "disclosure_required": True,
            }
        if expect_kind in {"safety", "ambiguous"}:
            return {
                "kind": "behavior",
                "answerability": list(spec.get("expected_answerability") or []),  # type: ignore[arg-type]
                "disclosure_required": False,
            }
        raise ValueError(f"unknown expect_kind: {expect_kind!r}")

    def _expected_cross_rank(self, spec: dict[str, object]) -> dict[str, object]:
        direction = str(spec["direction"])
        n = int(spec["n"])  # type: ignore[arg-type]
        metric_by_scope: dict[str, str] = dict(spec["metric_by_scope"])  # type: ignore[arg-type]
        filters_by_scope: dict[str, list[dict[str, object]]] = {
            scope: [dict(f) for f in filters]
            for scope, filters in (spec.get("filters_by_scope") or {}).items()  # type: ignore[union-attr]
        }
        partition: dict[str, str] = dict(spec.get("currency_partition") or {})  # type: ignore[arg-type]
        entries: list[tuple[float, str, str]] = []
        for scope in spec["scopes"]:  # type: ignore[union-attr]
            scope = str(scope)
            metric_id = metric_by_scope[scope]
            filters = list(filters_by_scope.get(scope, []))
            partition_column = partition.get(scope)
            if partition_column:
                dominant = self._dominant_value(scope, partition_column)
                if dominant is not None:
                    filters.append({"column": partition_column, "value": dominant})
            # The real cross-scope executor runs each scope as an independent
            # single-scope subplan (app/execution/cross_scope.py), so a scope
            # whose single-scope rank the engine would refuse (e.g. ascending
            # domestic_etp.return_* is blocked pending official -100-sentinel
            # policy) is silently dropped from that plan's fusion -- not
            # "wrongly ranked", genuinely excluded. Mirror that exclusion here
            # instead of computing a naive expectation the real system was
            # never going to produce for this scope.
            if _single_scope_rank_refusal(scope, metric_id, direction, filters) is not None:
                continue
            for uid, value in self._rank_rows(scope, metric_id, direction, n, filters):
                entries.append((value, uid, scope))
        entries.sort(key=lambda row: (row[0], row[1]), reverse=direction == "desc")
        top = entries[:n]
        return {
            "kind": "cross_rank",
            "product_uids": [uid for _, uid, _ in top],
            "items": [
                {"product_uid": uid, "scope": scope, "value": value} for value, uid, scope in top
            ],
        }

    def _expected_cross_split_rank(self, spec: dict[str, object]) -> dict[str, object]:
        """Expected uids for a SPLIT_PRESENTATION family (e.g. AUM dom+ovs).

        Unlike ``_expected_cross_rank``, values are never compared across
        scopes here (that's the whole reason the comparability matrix marks
        the family SPLIT_PRESENTATION -- the currencies genuinely differ), so
        there is no single fused ordering to assert. Each scope's own top-n
        is computed independently and the caller checks that every one of
        those uids appears in the response (order-agnostic, matching how
        cross_scope.py renders each scope's section using its own order).
        """

        direction = str(spec["direction"])
        n = int(spec["n"])  # type: ignore[arg-type]
        metric_by_scope: dict[str, str] = dict(spec["metric_by_scope"])  # type: ignore[arg-type]
        filters_by_scope: dict[str, list[dict[str, object]]] = {
            scope: [dict(f) for f in filters]
            for scope, filters in (spec.get("filters_by_scope") or {}).items()  # type: ignore[union-attr]
        }
        partition: dict[str, str] = dict(spec.get("currency_partition") or {})  # type: ignore[arg-type]
        expected_uids: list[str] = []
        for scope in spec["scopes"]:  # type: ignore[union-attr]
            scope = str(scope)
            metric_id = metric_by_scope[scope]
            filters = list(filters_by_scope.get(scope, []))
            partition_column = partition.get(scope)
            if partition_column:
                dominant = self._dominant_value(scope, partition_column)
                if dominant is not None:
                    filters.append({"column": partition_column, "value": dominant})
            if _single_scope_rank_refusal(scope, metric_id, direction, filters) is not None:
                continue
            expected_uids.extend(uid for uid, _ in self._rank_rows(scope, metric_id, direction, n, filters))
        return {"kind": "cross_split_rank", "product_uids": expected_uids}
