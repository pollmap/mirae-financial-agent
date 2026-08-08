"""Allow-listed DuckDB execution and field-level evidence construction."""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Any

import duckdb

from app.config import PROJECT_ROOT
from app.domain.models import (
    AggregateEvidence,
    Answerability,
    CalculationEvidence,
    CoverageEvidence,
    EvidenceBundle,
    FieldEvidence,
    ProductEvidence,
    QueryPlan,
    UniverseEvidence,
)
from app.execution.registry import (
    CATALOG_FIELD_MAP,
    CATALOG_SCOPE_FIELDS,
    MetricRegistry,
    PlanPolicyDecision,
    canonicalize_numeric_operand,
)

SOURCE_TABLE_BY_SCOPE = {
    "bond": "PRBD01N001",
    "domestic_etp": "PREF01N001",
    "overseas_etp": "PREF02N001",
    "fund": "PRFD01N001",
}

RAW_TABLE_BY_SCOPE = {
    "bond": "raw_prbd01n001",
    "domestic_etp": "raw_pref01n001",
    "overseas_etp": "raw_pref02n001",
    "fund": "raw_prfd01n001",
}
SCOPE_BY_SOURCE_TABLE = {value: key for key, value in SOURCE_TABLE_BY_SCOPE.items()}

CATALOG_SOURCE_FIELDS = {
    "bond": {
        "product_id": "PD_NO",
        "name": "PD_NM",
        "short_name": "PD_ABRV_NM",
        "market": "PD_EXG_MKT",
        "currency": "CURR_CD",
        "trading_currency": "CURR_CD",
        "issuer": "PD_PBCM",
        "asset_type": "STD_PD_MCLS_NM",
        "country_code": "PD_CTRY_CD",
        "risk_grade": "PD_RISK_GCD",
        "bond_kind": "BD_KND",
    },
    "domestic_etp": {
        "product_id": "pd_itm_no",
        "alt_id": "pd_itm_no_ma",
        "name": "pd_nm",
        "short_name": "pd_abrv_nm",
        "internal_type": "pd_grp_no",
        "market": "pd_exg_mkt_nm",
        "currency": "pd_curr_cd",
        "manager": "cu_fund_mgmt_co",
        "asset_type": "wu_inv_ast_type",
        "region": "wu_inv_rgn",
        "risk_grade": "pd_risk_nm",
        "sale_status": "pd_sale_yn",
        "pension_eligible": "pd_pen_tr_yn",
        "benchmark": "cu_base_index",
        "strategy": "cu_strtegy",
    },
    "overseas_etp": {
        "product_id": "pd_itm_no",
        "alt_id": "pd_itm_no_ma",
        "isin": "pd_isin_cd",
        "name": "pd_nm",
        "short_name": "pd_abrv_nm",
        "internal_type": "pd_grp_no",
        "market": "pd_exg_mkt_cd",
        "currency": "pd_curr_cd",
        "trading_currency": "pd_trd_ccy",
        "manager": "cu_fund_mgmt_co",
        "asset_type": "wu_inv_ast_type",
        "region": "wu_inv_rgn",
        "sale_status": "pd_sale_yn",
        "benchmark": "cu_base_index",
        "strategy": "cu_strtegy",
    },
    "fund": {
        "product_id": "itm_no",
        "name": "itm_nm",
        "short_name": "itm_abrv_nm",
        "currency": "curr_cd",
        "trading_currency": "curr_cd",
        "manager_code": "or_co_xtn_itt_cd",
        "asset_type": "or_attr_desc",
        "region": "fd_ivst_rgn_desc",
        "risk_grade": "zrin_fd_ivst_risk_grd_nm",
        "sale_status": "sale_yn",
        "public_private": "prvo_pbff_desc",
        "benchmark": "bmrk_nm",
        "domestic_overseas_class": "ovrs_fd_desc",
    },
}


def _catalog_as_of_fields() -> dict[tuple[str, str], str]:
    """Load source-field dates from the canonical registry.

    Catalog evidence is sourced directly from raw rows, so the date cell must
    be read from that same raw row rather than inferred from the dataset
    snapshot or from a different product.
    """

    mapping: dict[tuple[str, str], str] = {}
    path = PROJECT_ROOT / "registry" / "canonical_fields_v1.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            scope = row["scope"].strip()
            source_field = row["source_field"].strip()
            as_of_field = row["as_of_field"].strip()
            if source_field and as_of_field.upper() not in {"", "NONE"}:
                mapping[(scope, source_field)] = as_of_field
    return mapping


CATALOG_AS_OF_FIELDS = _catalog_as_of_fields()

SQL_OPERATOR = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}

VALID_VALUE_STATES = {
    "VALID",
    "ZERO_VALID",
    "SUSPECT_OUTLIER",
    "PARTIAL",
}
PRESENT_VALUE_STATES = {
    *VALID_VALUE_STATES,
    "ZERO_UNKNOWN",
    "UNUSABLE_CONSTANT",
    "SENTINEL",
}
OFFICIAL_DATA_ZIP_SHA256 = "c3809aca73396f57242ded0188fa06a3d271bd4ad65010e53d5533efc7c18163"


@dataclass(frozen=True)
class DatasetStats:
    raw_count: int
    logical_count: int
    serving_count: int
    quarantine_count: int


def _scalar(value: Any) -> str | int | float | bool | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f").rstrip("0").rstrip(".") or "0"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _source_date(value: Any) -> str | None:
    """Parse an explicitly supplied raw YYYYMMDD cell without inference."""

    if value is None:
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _hash_query(sql: str, params: Iterable[Any]) -> str:
    payload = json.dumps(
        {"sql": sql, "params": [_scalar(value) for value in params]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bounded_filter_summary(plan: QueryPlan) -> str:
    """Return valid, useful JSON without echoing an unbounded planner payload."""

    def compact(value: Any) -> Any:
        if isinstance(value, str):
            return value if len(value) <= 80 else value[:79] + "…"
        if isinstance(value, list):
            return [compact(item) for item in value[:5]]
        return value

    all_conditions = [
        (group_index, condition)
        for group_index, group in enumerate(plan.filter_groups)
        for condition in group.conditions
    ]
    entries: list[dict[str, Any]] = []
    for group_index, condition in all_conditions:
        entry = {
            "group": group_index,
            "field": condition.field,
            "op": condition.op,
            "value": compact(condition.value),
            "value2": compact(condition.value2),
            "unit": compact(condition.unit),
        }
        candidate = {
            "groups_join": plan.groups_join,
            "conditions": [*entries, entry],
            "omitted_conditions": len(all_conditions) - len(entries) - 1,
        }
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 1_900:
            break
        entries.append(entry)
    payload = {
        "groups_join": plan.groups_join,
        "conditions": entries,
        "omitted_conditions": len(all_conditions) - len(entries),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class DuckDBEngine:
    """Compile a validated QueryPlan into parameterized, allow-listed SQL."""

    def __init__(self, database_path: Path, registry: MetricRegistry | None = None) -> None:
        self.database_path = Path(database_path)
        self.registry = registry or MetricRegistry.load()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if not self.database_path.is_file():
            raise FileNotFoundError(
                f"Serving database is missing: {self.database_path}. Run scripts/build_data.py first."
            )
        return duckdb.connect(str(self.database_path), read_only=True)

    @staticmethod
    def _rows(
        connection: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
    ) -> list[dict[str, Any]]:
        cursor = connection.execute(sql, params or [])
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _raw_records(
        self,
        connection: duckdb.DuckDBPyConnection,
        scope: str,
        source_excel_rows: Iterable[int],
    ) -> dict[int, dict[str, Any]]:
        """Load literal raw cells for the small set of rows returned to a user."""

        rows = sorted(set(int(item) for item in source_excel_rows))
        if not rows:
            return {}
        table = RAW_TABLE_BY_SCOPE[scope]
        sql = (
            f"SELECT * FROM {table} WHERE source_excel_row IN ("
            + ",".join("?" for _ in rows)
            + ")"
        )
        return {
            int(row["source_excel_row"]): row
            for row in self._rows(connection, sql, rows)
        }

    def _stats(self, connection: duckdb.DuckDBPyConnection, scope: str) -> DatasetStats:
        dataset_id = SOURCE_TABLE_BY_SCOPE[scope]
        row = self._rows(
            connection,
            """
            SELECT raw_row_count, logical_product_count, serving_product_count, quarantine_count
            FROM metadata.build_reconciliation
            WHERE dataset_id = ?
            """,
            [dataset_id],
        )[0]
        return DatasetStats(
            raw_count=int(row["raw_row_count"]),
            logical_count=int(row["logical_product_count"]),
            serving_count=int(row["serving_product_count"]),
            quarantine_count=int(row["quarantine_count"]),
        )

    def policy_decision(self, plan: QueryPlan) -> PlanPolicyDecision:
        decision = self.registry.evaluate(plan)
        if not decision.allowed:
            return decision
        if (
            plan.intent == "rank"
            and plan.metrics == ["domestic_etp.aum_last"]
            and any(
                condition.field == "product.internal_type" and condition.value == "ETN"
                for group in plan.filter_groups
                for condition in group.conditions
            )
        ):
            return PlanPolicyDecision(
                False,
                Answerability.DATA_QUALITY_BLOCKED,
                "ETN_AUM_CONSTANT_ZERO",
                "국내 ETN의 제공 AUM 409건이 모두 0이므로 크기 순위를 만들지 않습니다.",
            )
        return decision

    def readiness_error(self) -> str | None:
        """Return a stable reason code when the serving DB cannot satisfy this code version."""

        if not self.database_path.is_file() or self.database_path.stat().st_size == 0:
            return "database_missing_or_empty"
        try:
            with self._connect() as connection:
                required = {
                    "product_catalog",
                    "product_metrics",
                    "quarantine",
                    "dataset_stats",
                }
                existing = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='main'"
                    ).fetchall()
                }
                if not required <= existing:
                    return "required_serving_objects_missing"
                build_info = dict(
                    connection.execute("SELECT key, value FROM metadata.build_info").fetchall()
                )
                if build_info.get("snapshot_date") != "2026-07-11":
                    return "snapshot_date_mismatch"
                if build_info.get("source_zip_sha256") != OFFICIAL_DATA_ZIP_SHA256:
                    return "source_hash_mismatch"
                scope_counts = {
                    str(scope): int(count)
                    for scope, count in connection.execute(
                        "SELECT scope, COUNT(*) FROM product_catalog GROUP BY scope"
                    ).fetchall()
                }
                if sum(scope_counts.values()) != 60_903:
                    return "serving_product_count_mismatch"
                expected_metric_rows = sum(
                    scope_counts.get(scope, 0) * count
                    for scope, count in self.registry.scope_metric_counts.items()
                ) + 2 * sum(scope_counts.values())
                actual_metric_rows = int(
                    connection.execute("SELECT COUNT(*) FROM product_metrics").fetchone()[0]
                )
                if actual_metric_rows != expected_metric_rows:
                    return "metric_row_count_mismatch"
                duplicate_metric_keys = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM ("
                        "SELECT product_uid, metric_id FROM product_metrics "
                        "GROUP BY 1,2 HAVING COUNT(*) <> 1)"
                    ).fetchone()[0]
                )
                if duplicate_metric_keys:
                    return "metric_key_not_unique"
                actual_metric_ids = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT DISTINCT metric_id FROM product_metrics"
                    ).fetchall()
                }
                expected_metric_ids = {*self.registry.metric_ids, "product.id", "product.name"}
                if actual_metric_ids != expected_metric_ids:
                    return "metric_registry_mismatch"
        except (duckdb.Error, IndexError, KeyError, OSError):
            return "database_open_or_query_failed"
        return None

    def resolve_entity_matches(self, plan: QueryPlan) -> list[tuple[str, tuple[str, ...]]]:
        """Return exact product UIDs per entity without applying result limits.

        The UID sets let the service diagnose not only a 0-or-many match, but
        also two differently written targets (ticker vs ISIN vs name) that
        resolve to the same product.  No candidate is silently selected.
        """

        if len(plan.scopes) != 1:
            return []
        resolutions: list[tuple[str, tuple[str, ...]]] = []
        with self._connect() as connection:
            catalog_filter_sql, catalog_filter_params = self._compile_filters(
                plan, include_metric=False
            )
            for entity in plan.entities:
                if entity.code:
                    predicate = (
                        "(LOWER(c.product_id)=LOWER(?) OR LOWER(c.alt_id)=LOWER(?) "
                        "OR LOWER(c.isin)=LOWER(?))"
                    )
                    entity_params = [entity.code, entity.code, entity.code]
                    label = entity.code
                else:
                    label = entity.name or ""
                    # Exact KG alias match (official name/short name) is more
                    # precise than the substring LIKE; fall back when the KG
                    # stage is absent or nothing matches exactly.
                    from app.retrieval.graph_retriever import (
                        resolve_product_nodes_by_name,
                    )

                    exact_uids = resolve_product_nodes_by_name(connection, label)
                    if exact_uids:
                        uid_placeholders = ", ".join("?" for _ in exact_uids)
                        predicate = f"c.product_uid IN ({uid_placeholders})"
                        entity_params = list(exact_uids)
                    else:
                        predicate = (
                            "(LOWER(c.name) LIKE LOWER(?) "
                            "OR LOWER(c.short_name) LIKE LOWER(?))"
                        )
                        entity_params = [
                            f"%{entity.name}%",
                            f"%{entity.name}%",
                        ]
                params = [
                    plan.scopes[0],
                    *catalog_filter_params,
                    *entity_params,
                ]
                matches = tuple(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT DISTINCT c.product_uid FROM product_catalog c "
                        f"WHERE c.scope=?{catalog_filter_sql} AND {predicate} "
                        "ORDER BY c.product_uid",
                        params,
                    ).fetchall()
                )
                if not entity.code and not matches and label:
                    matches = self._lexical_entity_fallback(connection, label, plan.scopes[0])
                resolutions.append((label, matches))
        return resolutions

    def _lexical_entity_fallback(
        self, connection: Any, label: str, scope: str
    ) -> tuple[str, ...]:
        """Federated retrieval fallback when exact alias and LIKE both miss.

        Consulted only once both stronger channels
        (``resolve_product_nodes_by_name`` exact KG alias, then the LIKE
        substring match above) already found nothing: BM25 tolerates typos,
        reordering, and partial-token matches neither of those do. Results
        are still exact ``product_uid``s re-joined against the catalog, so
        they flow through the same "not exactly one match -> clarify"
        contract as any other resolution -- a fuzzy hit is never silently
        promoted to a chosen answer.
        """

        from app.retrieval.router import route_entity_fallback

        # vector_enabled is hardcoded False here (not threaded from Settings):
        # DuckDBEngine has no settings handle, and vector search has no
        # committed embeddings cache yet everywhere else in this codebase
        # either (config.py's own default is False). Once a real cache
        # exists, extending this call to accept the flag is a one-line change.
        decision = route_entity_fallback(exact_match_count=0, vector_enabled=False)
        if "lexical" not in decision.channels:
            return ()
        from app.retrieval.fusion import reciprocal_rank_fusion
        from app.retrieval.lexical_retriever import search as lexical_search

        hits = lexical_search(connection, label, field="name", scope=scope, limit=5)
        # Single channel today (vector_enabled is always False here -- see the
        # comment above), so this is provably a no-op on ordering: RRF's
        # score 1/(k+rank) is strictly monotonic in rank within one channel.
        # Routing through it anyway keeps this call site correct by
        # construction once a second channel is added, rather than requiring
        # someone to remember to insert fusion at that point.
        fused = reciprocal_rank_fusion(
            {"lexical": [hit.product_uid for hit in hits]}, limit=5
        )
        return tuple(fused)

    def resolve_entities(self, plan: QueryPlan) -> list[tuple[str, int]]:
        """Return match cardinality per declared entity without applying limits."""

        return [
            (label, len(matches))
            for label, matches in self.resolve_entity_matches(plan)
        ]

    def _comparison_context_error(
        self, plan: QueryPlan, items: list[ProductEvidence]
    ) -> tuple[str, str] | None:
        """Fail closed before exposing contextually incomparable values.

        Product-by-product comparison does not need the user to repeat a
        currency filter when every resolved product has the same source-backed
        currency.  It must, however, stop when the products use different
        currencies, a required currency context is absent, or their source
        dates differ.  The official package has neither FX conversion nor a
        time-alignment series.
        """

        if plan.intent != "compare" or len(items) < 2:
            return None
        for metric_id in plan.metrics:
            date_contexts: list[str | None] = []
            for item in items:
                candidate_field = next(
                    (field for field in item.fields if field.metric_id == metric_id),
                    None,
                )
                if candidate_field is None or candidate_field.normalized_value in (None, ""):
                    return (
                        "COMPARISON_VALUE_UNAVAILABLE",
                        f"{metric_id} 비교 대상 중 제공 데이터에서 값을 확인할 수 없는 상품이 있어 "
                        "불완전한 비교를 만들지 않습니다.",
                    )
                if any(
                    flag in {
                        "SENTINEL",
                        "UNAVAILABLE",
                        "UNUSABLE_CONSTANT",
                        "ZERO_UNKNOWN",
                    }
                    or flag.startswith("MISSING")
                    for flag in candidate_field.quality_flags
                ):
                    return (
                        "COMPARISON_VALUE_UNUSABLE",
                        f"{metric_id} 비교 대상 중 값의 의미나 품질이 확정되지 않은 상품이 있어 "
                        "해당 값을 정상 수치로 비교하지 않습니다.",
                    )
                field = next(
                    (
                        candidate
                        for candidate in item.fields
                        if candidate.metric_id == metric_id
                        and candidate.normalized_value not in (None, "")
                        and not any(
                            flag in {
                                "SENTINEL",
                                "UNAVAILABLE",
                                "UNUSABLE_CONSTANT",
                                "ZERO_UNKNOWN",
                            }
                            or flag.startswith("MISSING")
                            for flag in candidate.quality_flags
                        )
                    ),
                    None,
                )
                if field is not None:
                    date_contexts.append(str(field.as_of_date) if field.as_of_date else None)
            dates = sorted({date for date in date_contexts if date})
            if len(dates) > 1:
                return (
                    "AS_OF_DATE_MISMATCH",
                    f"{metric_id} 비교 대상의 원천 기준일이 {dates[0]}~{dates[-1]}로 다릅니다. "
                    "제공 데이터에 동일 시점 재산출 자료가 없어 값을 직접 비교하지 않습니다.",
                )
            if dates and any(date is None for date in date_contexts):
                return (
                    "AS_OF_CONTEXT_MISSING",
                    f"{metric_id} 비교 대상 중 원천 기준일이 제공되지 않은 값과 "
                    f"기준일 {dates[0]} 값이 함께 있어 동일 시점 비교를 보장할 수 없습니다.",
                )
            policy = self.registry.get(metric_id)
            if policy is None or not policy.requires_currency_partition:
                continue
            if "TRADING_CURRENCY" in policy.unit_status or "CONTEXT_REQUIRED" in policy.unit_status:
                preferred = ("product.trading_currency",)
            elif "PRODUCT_CURRENCY" in policy.unit_status:
                preferred = ("product.currency",)
            else:
                preferred = ("product.currency", "product.trading_currency")
            currencies: list[str | None] = []
            for item in items:
                currency = next(
                    (
                        str(field.normalized_value)
                        for field_id in preferred
                        for field in item.fields
                        if field.metric_id == field_id
                        and field.normalized_value not in (None, "")
                    ),
                    None,
                )
                currencies.append(currency)
            known = sorted({currency for currency in currencies if currency})
            if any(currency is None for currency in currencies):
                return (
                    "CURRENCY_CONTEXT_MISSING",
                    f"{metric_id} 비교 대상 중 원본 통화를 확인할 수 없는 상품이 있어 값을 비교하지 않습니다.",
                )
            if len(known) > 1:
                return (
                    "CURRENCY_MISMATCH",
                    f"{metric_id} 비교 대상의 원본 통화가 {', '.join(known)}로 다릅니다. "
                    "제공 데이터에 환율·환산 기준이 없어 금액이나 가격을 직접 비교하지 않습니다.",
                )
        return None

    def empty_bundle(
        self,
        plan: QueryPlan,
        *,
        answerability: Answerability,
        reason_code: str,
        limitation: str,
        clarification: Any = None,
    ) -> EvidenceBundle:
        coverage = None
        with self._connect() as connection:
            if len(plan.scopes) == 1:
                stats = self._stats(connection, plan.scopes[0])
                scope = plan.scopes[0]
                raw = stats.raw_count
                serving = stats.serving_count
                try:
                    coverage = self._coverage(connection, plan, stats)
                except ValueError:
                    coverage = None
            else:
                target_scopes = plan.scopes or list(SOURCE_TABLE_BY_SCOPE)
                all_stats = [self._stats(connection, scope) for scope in target_scopes]
                scope = "multi"
                raw = sum(item.raw_count for item in all_stats)
                serving = sum(item.serving_count for item in all_stats)
        return EvidenceBundle(
            execution_id=str(uuid.uuid4()),
            answerability=answerability,
            reason_code=reason_code,
            result_count=0,
            universe=UniverseEvidence(
                scope=scope,
                raw_count=raw,
                serving_count=serving,
                eligible_count=coverage.denominator if coverage else 0,
                excluded_count=max(serving - (coverage.denominator if coverage else 0), 0),
                filter_summary=_bounded_filter_summary(plan),
            ),
            clarification=clarification,
            coverage=coverage,
            limitations=[limitation],
        )

    @staticmethod
    def _compile_catalog_condition(
        column: str, op: str, value: Any, value2: Any
    ) -> tuple[str, list[Any]]:
        identifier = f'c."{column}"'
        if op in SQL_OPERATOR:
            return f"{identifier} {SQL_OPERATOR[op]} ?", [value]
        if op == "contains":
            return f"LOWER({identifier}) LIKE LOWER(?)", [f"%{value}%"]
        if op == "starts_with":
            return f"LOWER({identifier}) LIKE LOWER(?)", [f"{value}%"]
        if op == "between":
            return f"{identifier} BETWEEN ? AND ?", [value, value2]
        if op == "in":
            values = list(value) if isinstance(value, list) else [value]
            if not values:
                return "FALSE", []
            return f"{identifier} IN ({','.join('?' for _ in values)})", values
        if op == "is_null":
            return f"{identifier} IS NULL", []
        if op == "is_not_null":
            return f"{identifier} IS NOT NULL", []
        raise ValueError(f"unsupported catalog operator: {op}")

    def _compile_metric_condition(
        self,
        metric_id: str,
        op: str,
        value: Any,
        value2: Any,
        latest_dates: dict[str, str | None] | None = None,
    ) -> tuple[str, list[Any]]:
        policy = self.registry.require(metric_id)
        value_column = "m.value_num" if policy.value_kind == "numeric" else "m.value_text"
        valid_states = sorted(VALID_VALUE_STATES)
        valid_placeholders = ",".join("?" for _ in valid_states)
        latest_date = (latest_dates or {}).get(metric_id)
        date_sql = " AND m.as_of_date = ?" if latest_date else ""
        date_params = [latest_date] if latest_date else []
        if op == "is_null":
            return (
                "NOT EXISTS (SELECT 1 FROM product_metrics m "
                "WHERE m.product_uid = c.product_uid AND m.metric_id = ? "
                f"AND {value_column} IS NOT NULL "
                f"AND m.quality_status IN ({valid_placeholders}){date_sql})",
                [metric_id, *valid_states, *date_params],
            )
        if op == "is_not_null":
            return (
                "EXISTS (SELECT 1 FROM product_metrics m "
                "WHERE m.product_uid = c.product_uid AND m.metric_id = ? "
                f"AND {value_column} IS NOT NULL "
                f"AND m.quality_status IN ({valid_placeholders}){date_sql})",
                [metric_id, *valid_states, *date_params],
            )
        if policy.value_kind == "numeric" and op not in {"is_null", "is_not_null"}:
            if isinstance(value, list):
                value = [canonicalize_numeric_operand(item) for item in value]
            else:
                value = canonicalize_numeric_operand(value)
            if op == "between":
                value2 = canonicalize_numeric_operand(value2)
        if op in SQL_OPERATOR:
            predicate = f"{value_column} {SQL_OPERATOR[op]} ?"
            values = [value]
        elif op == "between":
            predicate = f"{value_column} BETWEEN ? AND ?"
            values = [value, value2]
        elif op == "in":
            members = list(value) if isinstance(value, list) else [value]
            predicate = f"{value_column} IN ({','.join('?' for _ in members)})"
            values = members
        elif op == "contains":
            predicate = f"LOWER({value_column}) LIKE LOWER(?)"
            values = [f"%{value}%"]
        elif op == "starts_with":
            predicate = f"LOWER({value_column}) LIKE LOWER(?)"
            values = [f"{value}%"]
        else:
            raise ValueError(f"unsupported metric operator: {op}")
        return (
            "EXISTS (SELECT 1 FROM product_metrics m "
            f"WHERE m.product_uid = c.product_uid AND m.metric_id = ? "
            f"AND m.quality_status IN ({valid_placeholders}) AND {predicate}"
            f"{date_sql})",
            [metric_id, *valid_states, *values, *date_params],
        )

    def _compile_filters(
        self,
        plan: QueryPlan,
        *,
        include_metric: bool = True,
        latest_dates: dict[str, str | None] | None = None,
    ) -> tuple[str, list[Any]]:
        groups_sql: list[str] = []
        params: list[Any] = []
        for group in plan.filter_groups:
            conditions_sql: list[str] = []
            for condition in group.conditions:
                catalog_column = CATALOG_FIELD_MAP.get(condition.field)
                if catalog_column:
                    value = condition.value
                    value2 = condition.value2
                    if catalog_column == "sale_status" and plan.scopes[0] in {
                        "domestic_etp",
                        "overseas_etp",
                    }:
                        status_codes = {"판매중": "1", "판매완료": "0"}
                        if isinstance(value, list):
                            value = [status_codes.get(str(item), item) for item in value]
                        else:
                            value = status_codes.get(str(value), value)
                        value2 = status_codes.get(str(value2), value2)
                    sql, values = self._compile_catalog_condition(
                        catalog_column, condition.op, value, value2
                    )
                elif include_metric and self.registry.get(condition.field):
                    sql, values = self._compile_metric_condition(
                        condition.field,
                        condition.op,
                        condition.value,
                        condition.value2,
                        latest_dates,
                    )
                elif not include_metric and self.registry.get(condition.field):
                    continue
                else:
                    raise ValueError(f"filter field is not allow-listed: {condition.field}")
                conditions_sql.append(sql)
                params.extend(values)
            if conditions_sql:
                groups_sql.append("(" + " AND ".join(conditions_sql) + ")")
        if not groups_sql:
            return "", []
        return " AND (" + f" {plan.groups_join} ".join(groups_sql) + ")", params

    @staticmethod
    def _entity_filter(plan: QueryPlan) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for entity in plan.entities:
            if entity.code:
                clauses.append(
                    "(LOWER(c.product_id) = LOWER(?) OR LOWER(c.alt_id) = LOWER(?) "
                    "OR LOWER(c.isin) = LOWER(?))"
                )
                params.extend([entity.code, entity.code, entity.code])
            elif entity.name:
                clauses.append("(LOWER(c.name) LIKE LOWER(?) OR LOWER(c.short_name) LIKE LOWER(?))")
                params.extend([f"%{entity.name}%", f"%{entity.name}%"])
        if not clauses:
            return "", []
        return " AND (" + " OR ".join(clauses) + ")", params

    def _catalog_base(
        self,
        plan: QueryPlan,
        latest_dates: dict[str, str | None] | None = None,
    ) -> tuple[str, list[Any]]:
        if not plan.scopes:
            raise ValueError("execution requires at least one scope after policy validation")
        if len(plan.scopes) == 1:
            sql = "FROM product_catalog c WHERE c.scope = ?"
            params: list[Any] = [plan.scopes[0]]
        else:
            sql = (
                "FROM product_catalog c WHERE c.scope IN ("
                + ",".join("?" for _ in plan.scopes)
                + ")"
            )
            params = list(plan.scopes)
        filters_sql, filter_params = self._compile_filters(
            plan, latest_dates=latest_dates
        )
        sql += filters_sql
        params.extend(filter_params)
        entity_sql, entity_params = self._entity_filter(plan)
        sql += entity_sql
        params.extend(entity_params)
        return sql, params

    def _metric_latest_date(
        self,
        connection: duckdb.DuckDBPyConnection,
        plan: QueryPlan,
        metric_id: str,
    ) -> str | None:
        """Return the common latest date inside the non-metric eligible universe.

        The date is intentionally calculated after scope, catalog, and entity
        restrictions.  A global metric maximum could otherwise erase a valid
        issuer/currency/subtype whose newest supplied snapshot is older.  Metric
        predicates are excluded here to avoid circularly choosing a date from
        values that already passed the requested numeric threshold.
        """

        policy = self.registry.require(metric_id)
        value_column = "value_num" if policy.value_kind == "numeric" else "value_text"
        catalog_filter_sql, catalog_filter_params = self._compile_filters(
            plan, include_metric=False
        )
        entity_sql, entity_params = self._entity_filter(plan)
        valid_states = sorted(VALID_VALUE_STATES)
        placeholders = ",".join("?" for _ in valid_states)
        value = connection.execute(
            "SELECT MAX(m.as_of_date) FROM product_catalog c "
            "JOIN product_metrics m ON m.product_uid=c.product_uid "
            "AND m.metric_id=? "
            f"AND m.{value_column} IS NOT NULL "
            f"AND m.quality_status IN ({placeholders}) "
            "WHERE c.scope=? AND m.as_of_date IS NOT NULL"
            + catalog_filter_sql
            + entity_sql,
            [
                metric_id,
                *valid_states,
                plan.scopes[0],
                *catalog_filter_params,
                *entity_params,
            ],
        ).fetchone()[0]
        return _scalar(value) if value is not None else None

    def _latest_dates_for_plan(
        self,
        connection: duckdb.DuckDBPyConnection,
        plan: QueryPlan,
    ) -> dict[str, str | None]:
        metric_ids = {
            metric_id
            for metric_id in plan.metrics
            if self.registry.get(metric_id) is not None
        }
        metric_ids.update(
            condition.field
            for group in plan.filter_groups
            for condition in group.conditions
            if self.registry.get(condition.field) is not None
        )
        metric_ids.update(
            aggregation.field
            for aggregation in plan.aggregations
            if aggregation.field and self.registry.get(aggregation.field) is not None
        )
        return {
            metric_id: self._metric_latest_date(connection, plan, metric_id)
            for metric_id in sorted(metric_ids)
        }

    def _execute_cross_scope_count(self, plan: QueryPlan) -> EvidenceBundle:
        """Count distinct products per scope without mixing a financial metric."""

        with self._connect() as connection:
            stats_by_scope = {
                scope: self._stats(connection, scope) for scope in plan.scopes
            }
            has_scope_routes = any(
                condition.field == "product.scope"
                for group in plan.filter_groups
                for condition in group.conditions
            )
            values_by_scope: dict[str, int] = {}
            source_fields_by_scope: dict[str, list[str]] = {}
            executed_sql: list[str] = []
            executed_params: list[Any] = []
            for scope in plan.scopes:
                group_clauses: list[str] = []
                filter_params: list[Any] = []
                source_fields = [CATALOG_SOURCE_FIELDS[scope]["product_id"]]
                for group in plan.filter_groups:
                    scope_conditions = [
                        condition
                        for condition in group.conditions
                        if condition.field == "product.scope"
                    ]
                    if has_scope_routes and str(scope_conditions[0].value) != scope:
                        continue
                    conditions = [
                        condition
                        for condition in group.conditions
                        if condition.field != "product.scope"
                        and condition.field in CATALOG_SCOPE_FIELDS[scope]
                    ]
                    if not conditions:
                        continue
                    condition_clauses: list[str] = []
                    for condition in conditions:
                        column = CATALOG_FIELD_MAP[condition.field]
                        clause, values = self._compile_catalog_condition(
                            column,
                            condition.op,
                            condition.value,
                            condition.value2,
                        )
                        condition_clauses.append(clause)
                        filter_params.extend(values)
                        source_field = CATALOG_SOURCE_FIELDS[scope].get(column)
                        if source_field:
                            source_fields.append(source_field)
                    group_clauses.append("(" + " AND ".join(condition_clauses) + ")")
                filter_sql = (
                    " AND (" + f" {plan.groups_join} ".join(group_clauses) + ")"
                    if group_clauses
                    else ""
                )
                scope_sql = (
                    "SELECT COUNT(DISTINCT c.product_uid) AS value "
                    "FROM product_catalog c WHERE c.scope=?" + filter_sql
                )
                scope_params = [scope, *filter_params]
                values_by_scope[scope] = int(
                    connection.execute(scope_sql, scope_params).fetchone()[0]
                )
                source_fields_by_scope[scope] = list(dict.fromkeys(source_fields))
                executed_sql.append(scope_sql)
                executed_params.extend(scope_params)
        sql = "; ".join(executed_sql)
        params = executed_params
        query_hash = _hash_query(sql, params)
        aggregates: list[AggregateEvidence] = []
        for scope in plan.scopes:
            value = values_by_scope.get(scope, 0)
            aggregates.append(
                AggregateEvidence(
                    aggregate_id=plan.aggregations[0].alias,
                    group_key=scope,
                    value=value,
                    unit="count",
                    source_table_ids=[SOURCE_TABLE_BY_SCOPE[scope]],
                    source_fields=source_fields_by_scope[scope],
                    source_row_count=value,
                    query_hash=query_hash,
                )
            )
        raw_count = sum(stats.raw_count for stats in stats_by_scope.values())
        serving_count = sum(stats.serving_count for stats in stats_by_scope.values())
        eligible_count = sum(values_by_scope.values())
        return EvidenceBundle(
            execution_id=str(uuid.uuid4()),
            answerability=Answerability.FULL,
            reason_code=None,
            result_count=len(aggregates),
            universe=UniverseEvidence(
                scope="multi",
                raw_count=raw_count,
                serving_count=serving_count,
                eligible_count=eligible_count,
                excluded_count=max(serving_count - eligible_count, 0),
                filter_summary=_bounded_filter_summary(plan),
            ),
            calculation=CalculationEvidence(
                operation="aggregate",
                formula=f"COUNT(DISTINCT product_uid) per scope; parameterized_query_sha256={query_hash}",
                formula_version="sql-v1.1",
                rounding=None,
                tie_breakers=[],
            ),
            aggregates=aggregates,
            limitations=[
                "상품군별 상품 ID를 각각 distinct count했으며 금액·수익률·위험척도를 합산하거나 "
                "교차 순위로 변환하지 않았습니다."
            ],
        )

    def _coverage(
        self,
        connection: duckdb.DuckDBPyConnection,
        plan: QueryPlan,
        stats: DatasetStats,
        latest_dates: dict[str, str | None] | None = None,
    ) -> CoverageEvidence | None:
        if not plan.metrics:
            return None
        metric_id = plan.metrics[0]
        policy = self.registry.get(metric_id)
        if policy is None:
            return None
        catalog_filter_sql, catalog_filter_params = self._compile_filters(
            plan, include_metric=False
        )
        entity_sql, entity_params = self._entity_filter(plan)
        non_metric_sql = catalog_filter_sql + entity_sql
        non_metric_params = [*catalog_filter_params, *entity_params]
        eligible_sql = "SELECT COUNT(*) FROM product_catalog c WHERE c.scope = ?" + non_metric_sql
        eligible_params: list[Any] = [plan.scopes[0], *non_metric_params]
        eligible = int(connection.execute(eligible_sql, eligible_params).fetchone()[0])
        present_states = sorted(PRESENT_VALUE_STATES)
        present = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT c.product_uid)
                FROM product_catalog c
                JOIN product_metrics m ON m.product_uid = c.product_uid AND m.metric_id = ?
                WHERE c.scope = ? AND m.quality_status IN (?,?,?,?,?,?,?)
                """
                + non_metric_sql,
                [metric_id, plan.scopes[0], *present_states, *non_metric_params],
            ).fetchone()[0]
        )
        valid = self._valid_metric_count(
            connection, plan, metric_id, latest_dates=latest_dates
        )
        rankable = valid if policy.ranking_allowed else 0
        return CoverageEvidence(
            metric_id=metric_id,
            # Metric coverage is a product-level ratio.  In the fund source,
            # raw physical rows are attribute rows and must not be mixed with
            # logical/serving product counts in the same object.
            raw_count=stats.logical_count,
            serving_count=stats.serving_count,
            present_count=present,
            valid_count=valid,
            rankable_count=rankable,
            numerator=valid,
            denominator=eligible,
            basis=(
                f"serving {plan.scopes[0]} products after non-metric filters; "
                "numerator/rankable use the same valid quality states and, when supplied, "
                "the common latest source date inside that eligible universe"
            ),
        )

    def _valid_metric_count(
        self,
        connection: duckdb.DuckDBPyConnection,
        plan: QueryPlan,
        metric_id: str,
        latest_dates: dict[str, str | None] | None = None,
    ) -> int:
        """Count the exact valid, non-null population used by rank/aggregate."""

        policy = self.registry.require(metric_id)
        catalog_filter_sql, catalog_filter_params = self._compile_filters(
            plan, include_metric=False
        )
        entity_sql, entity_params = self._entity_filter(plan)
        value_column = "value_num" if policy.value_kind == "numeric" else "value_text"
        valid_states = sorted(VALID_VALUE_STATES)
        valid_placeholders = ",".join("?" for _ in valid_states)
        latest_date = (latest_dates or {}).get(metric_id)
        date_sql = " AND m.as_of_date=?" if latest_date else ""
        date_params = [latest_date] if latest_date else []
        return int(
            connection.execute(
                "SELECT COUNT(DISTINCT c.product_uid) FROM product_catalog c "
                "JOIN product_metrics m ON m.product_uid=c.product_uid AND m.metric_id=? "
                f"AND m.{value_column} IS NOT NULL "
                f"AND m.quality_status IN ({valid_placeholders}){date_sql} "
                "WHERE c.scope=?"
                + catalog_filter_sql
                + entity_sql,
                [
                    metric_id,
                    *sorted(VALID_VALUE_STATES),
                    *date_params,
                    plan.scopes[0],
                    *catalog_filter_params,
                    *entity_params,
                ],
            ).fetchone()[0]
        )

    def _core_field_evidence(
        self,
        row: dict[str, Any],
        rank: int | None,
        selected_columns: set[str] | None = None,
        raw_record: dict[str, Any] | None = None,
        metric_ids_by_column: dict[str, str] | None = None,
    ) -> ProductEvidence:
        scope = str(row["scope"])
        field_map = CATALOG_SOURCE_FIELDS[scope]
        fields: list[FieldEvidence] = []
        for column in (
            "product_id",
            "alt_id",
            "isin",
            "name",
            "short_name",
            "internal_type",
            "market",
            "currency",
            "trading_currency",
            "manager",
            "manager_code",
            "issuer",
            "asset_type",
            "region",
            "country_code",
            "risk_grade",
            "sale_status",
            "public_private",
            "pension_eligible",
            "benchmark",
            "strategy",
            "bond_kind",
            "domestic_overseas_class",
        ):
            if selected_columns is not None and column not in selected_columns:
                continue
            if column not in row or row[column] in (None, "") or column not in field_map:
                continue
            metric_id = (metric_ids_by_column or {}).get(column) or {
                "product_id": "product.id",
                "alt_id": "product.alias.mirae_id",
                "isin": "product.alias.isin",
                "short_name": "product.alias.short_name",
                "asset_type": "product.asset_class",
                "region": "product.investment_region",
                "pension_eligible": "product.pension_trade_eligible",
                "manager_code": "product.manager_code",
                "issuer": "product.issuer",
                "country_code": "product.country_code",
                "bond_kind": "bond.kind",
                "domestic_overseas_class": "fund.domestic_overseas_class",
            }.get(column, "product." + column)
            raw_value = row[column]
            if raw_record is not None and field_map[column] in raw_record:
                raw_value = raw_record[field_map[column]]
            as_of_field = CATALOG_AS_OF_FIELDS.get((scope, field_map[column]))
            as_of_date = (
                _source_date(raw_record.get(as_of_field))
                if raw_record is not None and as_of_field
                else None
            )
            fields.append(
                FieldEvidence(
                    evidence_id=f"{row['product_uid']}:{field_map[column]}",
                    metric_id=metric_id,
                    source_table_id=row["source_table_id"],
                    source_file=row["source_file"],
                    source_sheet=row["source_sheet"],
                    source_excel_row=int(row["source_excel_row"]),
                    source_field=field_map[column],
                    raw_value=_scalar(raw_value),
                    normalized_value=_scalar(row[column]),
                    unit=None,
                    as_of_date=as_of_date,
                    as_of_status=("AVAILABLE" if as_of_date else "DATASET_SNAPSHOT_ONLY"),
                    source_row_hash=row["source_row_hash"],
                    quality_flags=[]
                    if row.get("quality_status") == "VALID"
                    else [row.get("quality_status", "")],
                )
            )
        return ProductEvidence(
            product_uid=row["product_uid"],
            name=row["name"],
            rank=rank,
            fields=fields,
        )

    def _append_metric_evidence(
        self,
        connection: duckdb.DuckDBPyConnection,
        items: list[ProductEvidence],
        metrics: list[str],
        latest_dates: dict[str, str | None] | None = None,
    ) -> None:
        if not items or not metrics:
            return
        product_ids = [item.product_uid for item in items]
        sql = (
            "SELECT * FROM product_metrics WHERE product_uid IN ("
            + ",".join("?" for _ in product_ids)
            + ") AND metric_id IN ("
            + ",".join("?" for _ in metrics)
            + ")"
        )
        rows = [
            row
            for row in self._rows(connection, sql, [*product_ids, *metrics])
            if not (latest_date := (latest_dates or {}).get(str(row["metric_id"])))
            or _scalar(row["as_of_date"]) == latest_date
        ]
        raw_by_source: dict[tuple[str, int], dict[str, Any]] = {}
        for source_table_id in {str(row["source_table_id"]) for row in rows}:
            scope = SCOPE_BY_SOURCE_TABLE[source_table_id]
            source_rows = [
                int(row["source_excel_row"])
                for row in rows
                if row["source_table_id"] == source_table_id
            ]
            raw_by_source.update(
                {
                    (source_table_id, source_excel_row): record
                    for source_excel_row, record in self._raw_records(
                        connection, scope, source_rows
                    ).items()
                }
            )
        by_product = {item.product_uid: item for item in items}
        for row in rows:
            item = by_product[row["product_uid"]]
            raw_record = raw_by_source.get(
                (str(row["source_table_id"]), int(row["source_excel_row"])), {}
            )
            raw_value = raw_record.get(row["source_field"], row["raw_value"])
            item.fields.append(
                FieldEvidence(
                    evidence_id=f"{row['product_uid']}:{row['metric_id']}",
                    metric_id=row["metric_id"],
                    source_table_id=row["source_table_id"],
                    source_file=row["source_file"],
                    source_sheet=row["source_sheet"],
                    source_excel_row=int(row["source_excel_row"]),
                    source_field=row["source_field"],
                    raw_value=_scalar(raw_value),
                    normalized_value=_scalar(
                        row["value_num"] if row["value_num"] is not None else row["value_text"]
                    ),
                    unit=row["unit"],
                    as_of_date=_scalar(row["as_of_date"]),
                    as_of_status=row["as_of_status"],
                    source_row_hash=row["source_row_hash"],
                    quality_flags=[]
                    if row["quality_status"] == "VALID"
                    else [row["quality_status"]],
                )
            )

    def _execute_products(
        self,
        connection: duckdb.DuckDBPyConnection,
        plan: QueryPlan,
        latest_dates: dict[str, str | None] | None = None,
    ) -> tuple[list[ProductEvidence], str, list[Any], bool]:
        base_sql, params = self._catalog_base(plan, latest_dates=latest_dates)
        sql = "SELECT c.* " + base_sql
        if plan.intent == "rank":
            joins: list[str] = []
            order: list[str] = []
            # Rebuild as joins must appear before WHERE.
            where_part = base_sql.removeprefix("FROM product_catalog c ")
            sql = "SELECT c.* FROM product_catalog c "
            metric_params: list[Any] = []
            for index, metric_id in enumerate(plan.metrics):
                alias = f"rank_m{index}"
                # The primary metric defines the rankable universe.  Later
                # metrics are tie-breakers only: a missing secondary value
                # must sort last, not remove an otherwise rankable product.
                join_type = "JOIN" if index == 0 else "LEFT JOIN"
                joins.append(
                    f"{join_type} product_metrics {alias} "
                    f"ON {alias}.product_uid=c.product_uid "
                    f"AND {alias}.metric_id=? AND {alias}.value_num IS NOT NULL "
                    f"AND {alias}.quality_status IN "
                    "('VALID','ZERO_VALID','SUSPECT_OUTLIER','PARTIAL')"
                    + (f" AND {alias}.as_of_date=?" if (latest_dates or {}).get(metric_id) else "")
                )
                metric_params.append(metric_id)
                if latest_date := (latest_dates or {}).get(metric_id):
                    metric_params.append(latest_date)
                sort_item = next((item for item in plan.sort if item.field == metric_id), None)
                direction = sort_item.direction if sort_item else "desc"
                order.append(f"{alias}.value_num {direction.upper()} NULLS LAST")
            sql += " ".join(joins) + " " + where_part
            params = [*metric_params, *params]
            sql += " ORDER BY " + ", ".join([*order, "c.product_uid ASC"]) + " LIMIT ?"
        else:
            sql += " ORDER BY c.product_uid ASC LIMIT ?"
        params.append(plan.limit)
        rows = self._rows(connection, sql, params)
        selected_columns: set[str] | None = None
        metric_ids_by_column: dict[str, str] = {}
        if plan.intent not in {"lookup", "explain"}:
            selected_columns = {"product_id", "name"}
            for group in plan.filter_groups:
                for condition in group.conditions:
                    column = CATALOG_FIELD_MAP.get(condition.field)
                    if column:
                        selected_columns.add(column)
                        metric_ids_by_column[column] = condition.field
            if any(
                (policy := self.registry.get(metric_id)) is not None
                and policy.requires_currency_partition
                for metric_id in plan.metrics
            ):
                selected_columns.update({"currency", "trading_currency"})
        raw_records = self._raw_records(
            connection,
            plan.scopes[0],
            [int(row["source_excel_row"]) for row in rows],
        )
        items = [
            self._core_field_evidence(
                row,
                rank=index if plan.intent == "rank" else None,
                selected_columns=selected_columns,
                raw_record=raw_records.get(int(row["source_excel_row"])),
                metric_ids_by_column=metric_ids_by_column,
            )
            for index, row in enumerate(rows, start=1)
        ]
        filtered_metric_ids = {
            condition.field
            for group in plan.filter_groups
            for condition in group.conditions
            if self.registry.get(condition.field) is not None
        }
        evidence_latest_dates = (
            latest_dates
            if plan.intent == "rank"
            else {
                metric_id: latest_date
                for metric_id, latest_date in (latest_dates or {}).items()
                if metric_id in filtered_metric_ids
            }
        )
        self._append_metric_evidence(
            connection,
            items,
            plan.metrics,
            latest_dates=evidence_latest_dates,
        )
        share_class_codes = [
            str(record.get("rptt_ksd_itm_no") or "").strip()
            for record in raw_records.values()
            if str(record.get("rptt_ksd_itm_no") or "").strip()
            not in {"", "KR0000000000", "000000000000"}
        ]
        has_share_class_candidates = len(share_class_codes) != len(set(share_class_codes))
        return items, sql, params, has_share_class_candidates

    def _execute_aggregate(
        self,
        connection: duckdb.DuckDBPyConnection,
        plan: QueryPlan,
        stats: DatasetStats,
        latest_dates: dict[str, str | None] | None = None,
    ) -> tuple[list[AggregateEvidence], str, list[Any], list[str]]:
        scope = plan.scopes[0]
        source_table = SOURCE_TABLE_BY_SCOPE[scope]
        date_notes: list[str] = []
        basis = next(
            (item.split("=", 1)[1] for item in plan.assumptions if item.startswith("count_basis=")),
            "serving",
        )
        aggregation = plan.aggregations[0]
        if basis == "quarantine":
            sql = "SELECT COUNT(*) AS value FROM quarantine WHERE source_table_id = ?"
            params: list[Any] = [source_table]
            value = connection.execute(sql, params).fetchone()[0]
            rows = [
                {
                    "aggregate_id": aggregation.alias,
                    "group_key": None,
                    "value": value,
                    "unit": "count",
                    "source_row_count": value,
                    "source_fields": ["quality_status"],
                    "query_hash": _hash_query(sql, params),
                }
            ]
            fields = ["quality_status"]
        elif basis == "fund_attribute":
            sql = "SELECT (SELECT COUNT(*) FROM raw_prfd01n001) AS raw_value, (SELECT COUNT(*) FROM fund_attribute) AS serving_value, (SELECT COUNT(*) FROM quarantine WHERE source_table_id='PRFD01N001') AS quarantine_value"
            params = []
            raw_value, serving_value, quarantine_value = connection.execute(sql).fetchone()
            rows = [
                {"group_key": "raw_attribute_rows", "value": raw_value},
                {"group_key": "serving_attribute_rows", "value": serving_value},
                {"group_key": "quarantine_rows", "value": quarantine_value},
            ]
            fields = ["itm_no", "prfd_attr_cd"]
        elif basis == "raw" and plan.group_by == ["product.internal_type"]:
            raw_table = RAW_TABLE_BY_SCOPE[scope]
            sql = f'SELECT "pd_grp_no" AS group_key, COUNT(*) AS value FROM {raw_table} GROUP BY 1 ORDER BY 1'
            params = []
            rows = self._rows(connection, sql)
            fields = ["pd_grp_no"]
        elif basis == "raw":
            sql = "SELECT ? AS value"
            params = [stats.raw_count]
            rows = [{"group_key": None, "value": stats.raw_count}]
            fields = ["*"]
        else:
            rows = []
            executed_sql: list[str] = []
            executed_params: list[Any] = []
            for aggregation in plan.aggregations:
                base_sql, base_params = self._catalog_base(
                    plan, latest_dates=latest_dates
                )
                where_part = base_sql.removeprefix("FROM product_catalog c ")
                group_expression = (
                    "c.internal_type" if plan.group_by == ["product.internal_type"] else "NULL"
                )
                metric_id = (
                    aggregation.field
                    if aggregation.field and self.registry.get(aggregation.field)
                    else (plan.metrics[0] if plan.metrics else None)
                )
                if aggregation.function == "count":
                    if metric_id:
                        metric_policy = self.registry.require(metric_id)
                        metric_value_column = (
                            "value_num" if metric_policy.value_kind == "numeric" else "value_text"
                        )
                        source_present_basis = (
                            "metric_count_basis=source_present" in plan.assumptions
                        )
                        count_states = sorted(
                            PRESENT_VALUE_STATES
                            if source_present_basis
                            else VALID_VALUE_STATES
                        )
                        state_placeholders = ",".join("?" for _ in count_states)
                        latest_date = (
                            None
                            if source_present_basis
                            else (latest_dates or {}).get(metric_id)
                        )
                        date_sql = " AND m.as_of_date=?" if latest_date else ""
                        sql = (
                            f"SELECT {group_expression} AS group_key, "
                            "COUNT(DISTINCT c.product_uid) AS value, "
                            "COUNT(DISTINCT c.product_uid) AS source_row_count, "
                            "MIN(m.unit) AS unit, MIN(m.as_of_date) AS as_of_min, "
                            "MAX(m.as_of_date) AS as_of_max, "
                            "COUNT(DISTINCT m.as_of_date) AS as_of_date_count, "
                            "COUNT(m.as_of_date) AS dated_count "
                            "FROM product_catalog c "
                            "JOIN product_metrics m ON m.product_uid=c.product_uid "
                            f"AND m.metric_id=? AND m.{metric_value_column} IS NOT NULL "
                            f"AND m.quality_status IN ({state_placeholders})"
                            + date_sql
                            + " "
                            + where_part
                        )
                        params = [
                            metric_id,
                            *count_states,
                            *([latest_date] if latest_date else []),
                            *base_params,
                        ]
                        source_fields = [metric_policy.source_field]
                    else:
                        sql = (
                            f"SELECT {group_expression} AS group_key, "
                            "COUNT(DISTINCT c.product_uid) AS value, "
                            "COUNT(DISTINCT c.product_uid) AS source_row_count, "
                            "NULL AS unit, NULL AS as_of_min, NULL AS as_of_max, "
                            "0 AS as_of_date_count, 0 AS dated_count " + base_sql
                        )
                        params = base_params
                        source_fields = [CATALOG_SOURCE_FIELDS[scope]["product_id"]]
                    output_unit = "count"
                else:
                    metric_id = str(aggregation.field)
                    latest_date = (latest_dates or {}).get(metric_id)
                    date_sql = " AND m.as_of_date=?" if latest_date else ""
                    function = aggregation.function.upper()
                    aggregate_expression = (
                        "SUM(m.value_num) AS value_sum"
                        if aggregation.function == "avg"
                        else f"{function}(m.value_num) AS value"
                    )
                    sql = (
                        f"SELECT {group_expression} AS group_key, "
                        f"{aggregate_expression}, "
                        "COUNT(DISTINCT c.product_uid) AS source_row_count, MIN(m.unit) AS unit, "
                        "MIN(m.as_of_date) AS as_of_min, MAX(m.as_of_date) AS as_of_max, "
                        "COUNT(DISTINCT m.as_of_date) AS as_of_date_count, "
                        "COUNT(m.as_of_date) AS dated_count "
                        "FROM product_catalog c JOIN product_metrics m "
                        "ON m.product_uid=c.product_uid AND m.metric_id=? "
                        "AND m.value_num IS NOT NULL AND m.quality_status IN "
                        "('VALID','ZERO_VALID','SUSPECT_OUTLIER','PARTIAL')"
                        + date_sql
                        + " "
                        + where_part
                    )
                    params = [metric_id, *([latest_date] if latest_date else []), *base_params]
                    metric_policy = self.registry.require(metric_id)
                    source_fields = [metric_policy.source_field]
                    output_unit = None
                    if metric_policy.requires_currency_partition:
                        currency_fields = (
                            ["product.trading_currency"]
                            if "TRADING_CURRENCY" in metric_policy.unit_status
                            or "CONTEXT_REQUIRED" in metric_policy.unit_status
                            else ["product.currency", "product.trading_currency"]
                        )
                        output_unit = next(
                            (
                                str(condition.value)
                                for group in plan.filter_groups
                                for condition in group.conditions
                                if condition.field in currency_fields
                                and condition.op == "eq"
                                and condition.value
                            ),
                            None,
                        )
                    if "PENDING" in metric_policy.unit_status or "CONTEXT_REQUIRED" in (
                        metric_policy.unit_status
                    ):
                        output_unit = (
                            f"[지표 단위 미확정; 필터 통화 {output_unit}]"
                            if output_unit
                            else "[지표 단위 미확정]"
                        )
                if plan.group_by == ["product.internal_type"]:
                    sql += " GROUP BY 1 ORDER BY 1"
                query_hash = _hash_query(sql, params)
                for row in self._rows(connection, sql, params):
                    if aggregation.function == "avg":
                        count = int(row["source_row_count"])
                        if row.get("value_sum") is None or count == 0:
                            row["value"] = None
                        else:
                            with localcontext() as context:
                                context.prec = 50
                                row["value"] = (
                                    Decimal(str(row["value_sum"])) / Decimal(count)
                                ).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_EVEN)
                    source_count = int(row["source_row_count"])
                    date_count = int(row.get("as_of_date_count") or 0)
                    dated_count = int(row.get("dated_count") or 0)
                    as_of_min = _scalar(row.get("as_of_min"))
                    as_of_max = _scalar(row.get("as_of_max"))
                    aggregate_as_of = (
                        as_of_min
                        if date_count == 1 and dated_count == source_count and source_count > 0
                        else None
                    )
                    group_suffix = (
                        f" ({row['group_key']})" if row.get("group_key") is not None else ""
                    )
                    if date_count > 1:
                        date_notes.append(
                            f"{metric_id}{group_suffix} 집계에는 상품별 원천 기준일 "
                            f"{as_of_min}~{as_of_max} 값이 함께 포함되어 "
                            "AggregateEvidence.as_of_date를 null로 두었습니다."
                        )
                    elif date_count == 1 and dated_count < source_count:
                        date_notes.append(
                            f"{metric_id}{group_suffix} 집계에는 원천 기준일 {as_of_min} 값과 "
                            "개별 기준일이 제공되지 않은 값이 함께 포함되어 "
                            "AggregateEvidence.as_of_date를 null로 두었습니다."
                        )
                    elif date_count == 1 and dated_count == source_count:
                        date_notes.append(
                            f"{metric_id}{group_suffix} 집계는 요청의 비수치 조건·상품 대상 안에서 "
                            f"확인된 공통 최신 원천 기준일 {as_of_min} 값만 사용했습니다."
                        )
                    elif date_count == 0 and source_count > 0:
                        date_notes.append(
                            f"{metric_id}{group_suffix} 집계의 개별 원천 기준일은 제공되지 않았습니다. "
                            "데이터셋 추출일을 개별 값 기준일로 추정하지 않았습니다."
                        )
                    row.update(
                        {
                            "aggregate_id": aggregation.alias,
                            "unit": output_unit or row.get("unit"),
                            "source_fields": [field for field in source_fields if field],
                            "query_hash": query_hash,
                            "as_of_date": aggregate_as_of,
                        }
                    )
                    rows.append(row)
                executed_sql.append(sql)
                executed_params.extend(params)
            sql = "; ".join(executed_sql)
            params = executed_params
            fields = []
            for group in plan.filter_groups:
                for condition in group.conditions:
                    catalog_column = CATALOG_FIELD_MAP.get(condition.field)
                    source_field = CATALOG_SOURCE_FIELDS[scope].get(catalog_column or "")
                    if source_field:
                        fields.append(source_field)
            fields = list(dict.fromkeys(field for field in fields if field))
            for row in rows:
                row["source_fields"] = list(dict.fromkeys([*row.get("source_fields", []), *fields]))
        if basis != "serving":
            query_hash = _hash_query(sql, params)
            for index, row in enumerate(rows, start=1):
                row.update(
                    {
                        "aggregate_id": f"{aggregation.alias}-{index}",
                        "unit": "count",
                        "source_row_count": int(row["value"]),
                        "source_fields": fields,
                        "query_hash": query_hash,
                    }
                )
        aggregates = [
            AggregateEvidence(
                aggregate_id=str(row["aggregate_id"]),
                group_key=_scalar(row.get("group_key")),
                value=_scalar(row["value"]),
                unit=row.get("unit"),
                source_table_ids=[source_table],
                source_fields=row["source_fields"],
                source_row_count=int(row["source_row_count"]),
                query_hash=row["query_hash"],
                as_of_date=_scalar(row.get("as_of_date")),
            )
            for row in rows
        ]
        return aggregates, sql, params, list(dict.fromkeys(date_notes))

    def execute(self, plan: QueryPlan) -> EvidenceBundle:
        decision = self.policy_decision(plan)
        if not decision.allowed:
            bundle = self.empty_bundle(
                plan,
                answerability=decision.answerability,
                reason_code=decision.reason_code or "POLICY_BLOCKED",
                limitation=decision.limitation or "정책상 실행할 수 없습니다.",
                clarification=plan.clarification(),
            )
            if len(plan.scopes) == 1 and plan.metrics:
                try:
                    catalog_filter_sql, catalog_filter_params = self._compile_filters(
                        plan, include_metric=False
                    )
                    with self._connect() as connection:
                        zero_count = int(
                            connection.execute(
                                """
                                SELECT COUNT(DISTINCT c.product_uid)
                                FROM product_catalog c
                                JOIN product_metrics m ON m.product_uid=c.product_uid
                                  AND m.metric_id=? AND m.value_num=0
                                WHERE c.scope=?
                                """
                                + catalog_filter_sql,
                                [plan.metrics[0], plan.scopes[0], *catalog_filter_params],
                            ).fetchone()[0]
                        )
                except ValueError:
                    zero_count = 0
                if zero_count:
                    bundle.limitations.append(
                        f"해당 serving 모집단에서 원본 값이 0인 상품은 {zero_count:,}개입니다. "
                        "0의 의미가 확정되지 않았거나 상수형이어서 순위에서 제외했습니다."
                    )
            if decision.reason_code == "SOURCE_FIELD_ABSENT" and plan.scopes == ["overseas_etp"]:
                bundle.limitations.append(
                    "대신 제공된 AUM(overseas_etp.aum_last), 종가(overseas_etp.close_price), "
                    "거래량(overseas_etp.volume_1d)은 각각의 단위·기준일 제한과 함께 조회할 수 있습니다."
                )
            return bundle
        if len(plan.scopes) > 1:
            if self.registry._is_safe_cross_scope_count(plan):
                return self._execute_cross_scope_count(plan)
            from app.execution.cross_scope import execute_cross_scope

            return execute_cross_scope(self, plan)
        if len(plan.scopes) != 1:
            raise ValueError("a validated executable plan requires at least one scope")
        scope = plan.scopes[0]
        with self._connect() as connection:
            stats = self._stats(connection, scope)
            latest_dates = self._latest_dates_for_plan(connection, plan)
            coverage = self._coverage(
                connection, plan, stats, latest_dates=latest_dates
            )
            catalog_sql, catalog_params = self._catalog_base(
                plan, latest_dates=latest_dates
            )
            filtered_eligible = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT c.product_uid) " + catalog_sql,
                    catalog_params,
                ).fetchone()[0]
            )
            execution_notes: list[str] = []
            has_share_class_candidates = False
            secondary_aggregate_missing: dict[str, int] = {}
            if plan.intent == "aggregate":
                aggregates, sql, params, aggregate_notes = self._execute_aggregate(
                    connection, plan, stats, latest_dates=latest_dates
                )
                execution_notes.extend(aggregate_notes)
                if (
                    "metric_count_basis=source_present" in plan.assumptions
                    and coverage is not None
                ):
                    non_valid = max(coverage.present_count - coverage.valid_count, 0)
                    execution_notes.append(
                        f"{coverage.metric_id} '제공된 값' 개수는 원본 셀이 존재하는 "
                        f"{coverage.present_count:,}개 기준입니다. 이 중 현재 품질정책상 "
                        f"필터·순위·수치 집계에 쓸 수 없는 값은 {non_valid:,}개이며, "
                        "결측을 0으로 바꾸지 않았습니다."
                    )
                aggregate_denominator = (
                    coverage.denominator if coverage is not None else filtered_eligible
                )
                for metric_id in plan.metrics[1:]:
                    if self.registry.get(metric_id) is None:
                        continue
                    valid_count = self._valid_metric_count(
                        connection, plan, metric_id, latest_dates=latest_dates
                    )
                    if valid_count < aggregate_denominator:
                        secondary_aggregate_missing[metric_id] = (
                            aggregate_denominator - valid_count
                        )
                items: list[ProductEvidence] = []
                result_count = len(aggregates)
                basis = next(
                    (
                        item.split("=", 1)[1]
                        for item in plan.assumptions
                        if item.startswith("count_basis=")
                    ),
                    "serving",
                )
                if basis == "quarantine":
                    rows = connection.execute(
                        "SELECT source_excel_row FROM quarantine WHERE source_table_id=? ORDER BY source_excel_row",
                        [SOURCE_TABLE_BY_SCOPE[scope]],
                    ).fetchall()
                    execution_notes.append(
                        "격리된 원본 Excel row: " + ", ".join(str(row[0]) for row in rows)
                    )
                    if scope == "overseas_etp":
                        execution_notes.append(
                            "TBF와 EMOP.K 2개 행은 핵심 식별정보가 남아 있어 PARTIAL 품질표시로 serving에 유지했습니다."
                        )
                if basis == "raw" and plan.group_by == ["product.internal_type"]:
                    serving_groups = dict(
                        connection.execute(
                            "SELECT internal_type, COUNT(*) FROM product_catalog WHERE scope=? GROUP BY 1",
                            [scope],
                        ).fetchall()
                    )
                    execution_notes.append(
                        "serving 기준(격리 제외): "
                        + ", ".join(
                            f"{key} {value:,}개" for key, value in sorted(serving_groups.items())
                        )
                    )
            else:
                items, sql, params, has_share_class_candidates = self._execute_products(
                    connection, plan, latest_dates=latest_dates
                )
                aggregates = []
                result_count = len(items)
                context_error = self._comparison_context_error(plan, items)
                if context_error is not None:
                    reason_code, limitation = context_error
                    return self.empty_bundle(
                        plan,
                        answerability=Answerability.INCOMPARABLE,
                        reason_code=reason_code,
                        limitation=limitation,
                    )
                if plan.intent == "compare":
                    for metric_id in plan.metrics:
                        metric_fields = [
                            field
                            for item in items
                            for field in item.fields
                            if field.metric_id == metric_id
                            and field.normalized_value not in (None, "")
                        ]
                        if metric_fields and all(
                            field.as_of_date is None for field in metric_fields
                        ):
                            execution_notes.append(
                                f"{metric_id} 비교값에는 개별 원천 기준일이 제공되지 않아 "
                                "동일한 2026-07-11 데이터셋 스냅샷 안의 값으로만 나란히 표시했습니다."
                            )
                if plan.intent == "rank":
                    for metric_id in plan.metrics:
                        if latest_date := latest_dates.get(metric_id):
                            execution_notes.append(
                                f"{metric_id} 순위는 요청의 비수치 조건·상품 대상 안에서 "
                                f"확인된 공통 최신 원천 기준일 {latest_date} 값만 사용했습니다. "
                                "이보다 오래되거나 개별 기준일이 없는 값은 순위에서 제외했습니다."
                            )

        answerability = Answerability.FULL
        limitations: list[str] = execution_notes
        reason_code: str | None = None
        if result_count == 0 or (
            aggregates
            and all(item.value is None or item.source_row_count == 0 for item in aggregates)
        ):
            answerability = Answerability.NO_RESULT
            reason_code = "NO_MATCHING_PRODUCT"
            limitations.append("요청 조건에 맞는 상품을 찾지 못했습니다.")
        elif coverage and coverage.numerator < coverage.denominator:
            answerability = Answerability.PARTIAL_WITH_COVERAGE
            reason_code = "PARTIAL_METRIC_COVERAGE"
            limitations.append(
                f"{coverage.metric_id} 값이 있는 상품은 유효 모집단 {coverage.denominator:,}개 중 "
                f"{coverage.numerator:,}개입니다."
            )
            # present_count > numerator means some products DO have a raw
            # value for this metric but it was excluded from ranking (e.g. a
            # literal 0 pending official zero-policy confirmation) -- a
            # materially different situation from "no source cell at all"
            # (denominator - present_count), and one an evaluator reading
            # only the bare ratio above would not otherwise learn. Only the
            # count is disclosed here, not the registry's internal audit
            # `notes` column (that text is written for auditors and can
            # contain raw sentinel values -- e.g. a literal "99991231" -- that
            # must not leak into a user-facing answer).
            excluded_present = coverage.present_count - coverage.numerator
            if excluded_present > 0:
                limitations.append(
                    f"{coverage.metric_id} 원본값이 있지만 순위에서 제외된 상품이 "
                    f"{excluded_present:,}개 있습니다(0값 등 공식 정책 확정 전 값 포함)."
                )
        if answerability != Answerability.NO_RESULT and secondary_aggregate_missing:
            answerability = Answerability.PARTIAL_WITH_COVERAGE
            reason_code = reason_code or "PARTIAL_METRIC_COVERAGE"
            for metric_id, missing_count in secondary_aggregate_missing.items():
                limitations.append(
                    f"집계 유효 모집단 중 {metric_id} 값을 확인할 수 없는 상품은 "
                    f"{missing_count:,}개입니다."
                )
        if answerability != Answerability.NO_RESULT and items:
            missing_by_metric: dict[str, int] = {}
            for metric_id in plan.metrics:
                if metric_id in CATALOG_FIELD_MAP:
                    continue
                missing_count = sum(
                    not any(
                        field.metric_id == metric_id
                        and field.normalized_value is not None
                        and not any(
                            flag.startswith("MISSING")
                            or flag in {"UNAVAILABLE", "SENTINEL", "UNUSABLE_CONSTANT"}
                            for flag in field.quality_flags
                        )
                        for field in item.fields
                    )
                    for item in items
                )
                if missing_count:
                    missing_by_metric[metric_id] = missing_count
            if missing_by_metric:
                answerability = Answerability.PARTIAL_WITH_COVERAGE
                reason_code = reason_code or "PARTIAL_METRIC_COVERAGE"
                for metric_id, missing_count in missing_by_metric.items():
                    limitations.append(
                        f"반환된 {len(items):,}개 상품 중 {metric_id} 값을 확인할 수 없는 "
                        f"상품은 {missing_count:,}개입니다."
                    )
        if plan.intent == "explain" and items:
            requested_catalog_fields = [
                metric_id for metric_id in plan.metrics if metric_id in CATALOG_FIELD_MAP
            ]
            missing_catalog_fields = {
                metric_id
                for item in items
                for metric_id in requested_catalog_fields
                if not any(
                    field.metric_id == metric_id
                    and field.normalized_value not in (None, "")
                    for field in item.fields
                )
            }
            if missing_catalog_fields:
                answerability = Answerability.PARTIAL_WITH_COVERAGE
                reason_code = "SOURCE_FIELD_ABSENT"
                limitations.append(
                    "요청한 원본 상품 필드 중 값이 없어 확인할 수 없는 항목: "
                    + ", ".join(sorted(missing_catalog_fields))
                    + "."
                )
        if answerability != Answerability.NO_RESULT and any(
            flag == "SUSPECT_OUTLIER"
            for item in items
            for field in item.fields
            for flag in field.quality_flags
        ):
            answerability = Answerability.PARTIAL_WITH_COVERAGE
            reason_code = reason_code or "SOURCE_OUTLIER_PRESERVED"
            limitations.append("원본 이상치 후보는 임의로 삭제·절삭하지 않고 그대로 표시했습니다.")
        if answerability != Answerability.NO_RESULT and any(
            flag == "ZERO_UNKNOWN"
            for item in items
            for field in item.fields
            for flag in field.quality_flags
        ):
            answerability = Answerability.PARTIAL_WITH_COVERAGE
            reason_code = reason_code or "ZERO_SEMANTICS_UNCONFIRMED"
            limitations.append(
                "원본 값 0의 의미가 정상 0인지 미입력·placeholder인지 확정되지 않은 항목이 "
                "있습니다. 원본 0은 근거에 보존하되 순위·필터·집계의 유효값으로 사용하지 않습니다."
            )
        primary_policy = self.registry.get(plan.metrics[0]) if plan.metrics else None
        if (
            answerability != Answerability.NO_RESULT
            and primary_policy
            and "OUTLIER_REVIEW" in primary_policy.quality_status
        ):
            answerability = Answerability.PARTIAL_WITH_COVERAGE
            reason_code = reason_code or "SOURCE_OUTLIER_PRESERVED"
            note = "원본 지표 전체에 이상치 후보가 있어 값은 삭제·절삭하지 않고 원문 기준으로 표시합니다."
            if note not in limitations:
                limitations.append(note)
        if primary_policy and (
            "PENDING" in primary_policy.unit_status
            or "CONTEXT_REQUIRED" in primary_policy.unit_status
        ):
            limitations.append(
                f"{plan.metrics[0]}의 단위 정의는 설명회 확인 전 미확정입니다. "
                "금액·가격은 원본 상품통화 또는 거래통화를 함께 표시하고, "
                "다른 통화를 환산하거나 상품군을 넘어 합산·비교하지 않습니다."
            )
        if (
            plan.intent == "rank"
            and scope == "fund"
            and len(items) >= 2
            and has_share_class_candidates
        ):
            limitations.append(
                "상위 결과에서 원본 rptt_ksd_itm_no가 같은 서로 다른 상품 코드가 확인되어 "
                "동일 펀드의 클래스 후보가 함께 포함될 수 있습니다. 해당 필드의 공식 share-class "
                "의미는 설명회 확인 전이므로 임의 통합하지 않았습니다."
            )

        eligible = filtered_eligible
        return EvidenceBundle(
            execution_id=str(uuid.uuid4()),
            answerability=answerability,
            reason_code=reason_code,
            result_count=result_count,
            universe=UniverseEvidence(
                scope=scope,
                raw_count=stats.raw_count,
                serving_count=stats.serving_count,
                eligible_count=eligible,
                excluded_count=max(stats.serving_count - eligible, 0),
                filter_summary=_bounded_filter_summary(plan),
            ),
            coverage=coverage,
            calculation=CalculationEvidence(
                operation=(
                    "aggregate"
                    if plan.intent == "aggregate"
                    else "sort"
                    if plan.intent == "rank"
                    else "compare"
                    if plan.intent == "compare"
                    else "lookup"
                    if plan.intent in {"lookup", "explain"}
                    else "filter"
                ),
                formula=f"parameterized_query_sha256={_hash_query(sql, params)}",
                formula_version="sql-v1.1",
                rounding="원본 Decimal 값을 유지하며 답변 표시에서만 불필요한 후행 0 제거",
                tie_breakers=["product_uid ASC"] if plan.intent == "rank" else [],
            ),
            aggregates=aggregates,
            items=items,
            limitations=limitations,
        )
