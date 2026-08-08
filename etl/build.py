"""Build the immutable organizer snapshot into a queryable DuckDB database.

The build intentionally keeps four layers:

* ``raw``: cell values represented losslessly enough for evidence and replay,
  before trimming or sentinel interpretation.
* ``clean``: Unicode-normalized and whitespace-trimmed values plus row quality.
* ``canonical``: common product, metric, attribute, locator, and quarantine tables.
* ``serving``: quarantine-free tables and product-type snapshots used by the API.

No organizer file is extracted to or modified in the repository.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .source import EXPECTED_DATASETS, SourceVerification, load_datarows, verify_source_bundle

SNAPSHOT_DATE = "2026-07-11"
RAW_TABLE_NAMES = {
    "PRBD01N001": "raw_prbd01n001",
    "PREF01N001": "raw_pref01n001",
    "PREF02N001": "raw_pref02n001",
    "PRFD01N001": "raw_prfd01n001",
}
SCOPE_BY_DATASET = {
    "PRBD01N001": "bond",
    "PREF01N001": "domestic_etp",
    "PREF02N001": "overseas_etp",
    "PRFD01N001": "fund",
}
UID_PREFIX = {
    "PRBD01N001": "BOND",
    "PREF01N001": "KR_ETP",
    "PREF02N001": "GLOBAL_ETP",
    "PRFD01N001": "FUND",
}
ID_FIELD = {
    "PRBD01N001": "PD_NO",
    "PREF01N001": "pd_itm_no",
    "PREF02N001": "pd_itm_no",
    "PRFD01N001": "itm_no",
}
NAME_FIELD = {
    "PRBD01N001": "PD_NM",
    "PREF01N001": "pd_nm",
    "PREF02N001": "pd_nm",
    "PRFD01N001": "itm_nm",
}

DOMESTIC_PARTIAL_IDS = {"KRG597100145"}
OVERSEAS_QUARANTINE_IDS = {"XW", "BTCK.K", "BAY", "BZZ", "AV", "ONX", "PINC.K", "OWN"}
OVERSEAS_PARTIAL_IDS = {"TBF", "EMOP.K"}

CATALOG_COLUMNS = [
    "product_uid",
    "scope",
    "source_table_id",
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
    "public_private",
    "sale_status",
    "pension_eligible",
    "pension_risk",
    "hedge_status",
    "listing_date",
    "benchmark",
    "strategy",
    "bond_kind",
    "domestic_overseas_class",
    "snapshot_date",
    "source_file",
    "source_sheet",
    "source_excel_row",
    "source_row_hash",
    "quality_status",
]

METRIC_COLUMNS = [
    "product_uid",
    "metric_id",
    "value_num",
    "value_text",
    "unit",
    "period",
    "quality_status",
    "source_table_id",
    "source_file",
    "source_sheet",
    "source_excel_row",
    "source_field",
    "raw_value",
    "as_of_date",
    "as_of_status",
    "source_row_hash",
]

RECONCILIATION = {
    "PRBD01N001": (42_394, 42_394, 42_394, 0),
    "PREF01N001": (1_734, 1_734, 1_733, 1),
    "PREF02N001": (5_646, 5_646, 5_638, 8),
    "PRFD01N001": (95_619, 11_139, 11_138, 1),
}


@dataclass(frozen=True)
class BuildResult:
    database_path: Path
    source_zip_sha256: str
    raw_row_count: int
    logical_product_count: int
    serving_product_count: int
    quarantine_count: int
    serving_fund_attribute_count: int
    serving_metric_count: int
    parquet_dir: Path | None
    kg_counts: dict[str, int] = field(default_factory=dict)
    lexical_counts: dict[str, int] = field(default_factory=dict)
    vector_counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "database_path": str(self.database_path),
            "source_zip_sha256": self.source_zip_sha256,
            "raw_row_count": self.raw_row_count,
            "logical_product_count": self.logical_product_count,
            "serving_product_count": self.serving_product_count,
            "quarantine_count": self.quarantine_count,
            "serving_fund_attribute_count": self.serving_fund_attribute_count,
            "serving_metric_count": self.serving_metric_count,
            "parquet_dir": str(self.parquet_dir) if self.parquet_dir else None,
            "kg_counts": self.kg_counts,
            "lexical_counts": self.lexical_counts,
            "vector_counts": self.vector_counts,
        }


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        result = pd.isna(value)
        return bool(result) if isinstance(result, bool) else False
    except (TypeError, ValueError):
        return False


def _raw_text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        if value.is_integer():
            return str(int(value))
        return format(value, ".15g")
    return str(value)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", str(value)).strip()
    return normalized or None


def _row_hash(values: tuple[Any, ...]) -> str:
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _uid(dataset_id: str, product_id: str | None, source_excel_row: int) -> str:
    identifier = product_id if product_id is not None else f"ROW-{source_excel_row}"
    return f"{UID_PREFIX[dataset_id]}:{dataset_id}:{identifier}"


def _mark_row_quality(dataset_id: str, frame: pd.DataFrame) -> None:
    frame["quality_status"] = "VALID"
    frame["quarantine_reason"] = None
    product_id = frame[ID_FIELD[dataset_id]]

    if dataset_id == "PREF01N001":
        quarantine = (product_id == "KR") & (frame["pd_nm"] == ".")
        if int(quarantine.sum()) != 1:
            raise RuntimeError("Expected exactly one domestic ETP placeholder row")
        row = int(frame.loc[quarantine, "source_excel_row"].iloc[0])
        if row != 1_155:
            raise RuntimeError(f"Domestic ETP placeholder moved from Excel row 1155 to {row}")
        frame.loc[product_id.isin(DOMESTIC_PARTIAL_IDS), "quality_status"] = "PARTIAL"
        frame.loc[quarantine, "quality_status"] = "QUARANTINE"
        frame.loc[quarantine, "quarantine_reason"] = "PLACEHOLDER_CORE_FIELDS_MISSING"

    elif dataset_id == "PREF02N001":
        quarantine = product_id.isin(OVERSEAS_QUARANTINE_IDS)
        observed = set(product_id[quarantine].dropna())
        if observed != OVERSEAS_QUARANTINE_IDS or int(quarantine.sum()) != 8:
            raise RuntimeError(f"Overseas ETP quarantine set changed: {sorted(observed)}")
        frame.loc[product_id.isin(OVERSEAS_PARTIAL_IDS), "quality_status"] = "PARTIAL"
        frame.loc[quarantine, "quality_status"] = "QUARANTINE"
        frame.loc[quarantine, "quarantine_reason"] = (
            "PLACEHOLDER_ZERO_LISTING_DATE_AND_CORE_FIELDS_MISSING"
        )

    elif dataset_id == "PRFD01N001":
        quarantine = product_id == '"'
        if int(quarantine.sum()) != 1:
            raise RuntimeError("Expected exactly one damaged fund row")
        row = int(frame.loc[quarantine, "source_excel_row"].iloc[0])
        if row != 84_563:
            raise RuntimeError(f"Damaged fund row moved from Excel row 84563 to {row}")
        frame.loc[quarantine, "quality_status"] = "QUARANTINE"
        frame.loc[quarantine, "quarantine_reason"] = "DAMAGED_SHIFTED_ROW_INVALID_PRODUCT_ID"


def _prepare_frames(
    verification: SourceVerification,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    loaded = load_datarows(verification)
    raw_frames: dict[str, pd.DataFrame] = {}
    clean_frames: dict[str, pd.DataFrame] = {}

    for dataset_id in EXPECTED_DATASETS:
        source, workbook = loaded[dataset_id]
        source_columns = list(source.columns)
        raw = source.map(_raw_text)
        hashes = [
            _row_hash(tuple(row)) for row in raw[source_columns].itertuples(index=False, name=None)
        ]
        raw["source_table_id"] = dataset_id
        raw["source_file"] = workbook.source_file
        raw["source_sheet"] = "datarows"
        raw["source_excel_row"] = pd.Series(range(2, len(raw) + 2), dtype="int64")
        raw["source_row_hash"] = hashes

        clean = raw.copy()
        for column in source_columns:
            clean[column] = clean[column].map(_clean_text)
        _mark_row_quality(dataset_id, clean)
        raw_frames[dataset_id] = raw
        clean_frames[dataset_id] = clean

    if sum(len(frame) for frame in raw_frames.values()) != 145_393:
        raise RuntimeError("Raw row reconciliation failed")
    return raw_frames, clean_frames


def _column(frame: pd.DataFrame, name: str | None, constant: str | None = None) -> pd.Series:
    if constant is not None:
        return pd.Series([constant] * len(frame), index=frame.index, dtype="object")
    if name and name in frame.columns:
        return frame[name]
    return pd.Series([None] * len(frame), index=frame.index, dtype="object")


def _isin_like(series: pd.Series) -> pd.Series:
    text = series.fillna("").astype(str)
    return series.where(text.str.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}"), None)


def _catalog_for_dataset(dataset_id: str, source: pd.DataFrame) -> pd.DataFrame:
    if dataset_id == "PRFD01N001":
        frame = source.drop_duplicates(subset=["itm_no"], keep="first").copy()
    else:
        frame = source.copy()

    mappings: dict[str, tuple[str | None, str | None]]
    if dataset_id == "PRBD01N001":
        mappings = {
            "product_id": ("PD_NO", None),
            "alt_id": ("PD_ABRV_NM", None),
            "name": ("PD_NM", None),
            "short_name": ("PD_ABRV_NM", None),
            "internal_type": (None, "BOND"),
            "market": ("PD_EXG_MKT", None),
            "currency": ("CURR_CD", None),
            "trading_currency": ("CURR_CD", None),
            "manager": (None, None),
            "manager_code": (None, None),
            "issuer": ("PD_PBCM", None),
            "asset_type": ("STD_PD_MCLS_NM", None),
            "region": (None, None),
            "country_code": ("PD_CTRY_CD", None),
            "risk_grade": ("PD_RISK_GCD", None),
            "public_private": (None, None),
            "sale_status": (None, None),
            "pension_eligible": (None, None),
            "pension_risk": (None, None),
            "hedge_status": (None, None),
            "listing_date": (None, None),
            "benchmark": (None, None),
            "strategy": (None, None),
            "bond_kind": ("BD_KND", None),
            "domestic_overseas_class": (None, None),
        }
    elif dataset_id == "PREF01N001":
        mappings = {
            "product_id": ("pd_itm_no", None),
            "alt_id": ("pd_itm_no_ma", None),
            "name": ("pd_nm", None),
            "short_name": ("pd_abrv_nm", None),
            "internal_type": ("pd_grp_no", None),
            "market": ("pd_exg_mkt_nm", None),
            "currency": ("pd_curr_cd", None),
            "trading_currency": ("pd_curr_cd", None),
            "manager": ("cu_fund_mgmt_co", None),
            "manager_code": (None, None),
            "issuer": (None, None),
            "asset_type": ("wu_inv_ast_type", None),
            "region": ("wu_inv_rgn", None),
            "country_code": (None, None),
            "risk_grade": ("pd_risk_nm", None),
            "public_private": (None, None),
            "sale_status": ("pd_sale_yn", None),
            "pension_eligible": ("pd_pen_tr_yn", None),
            "pension_risk": ("pd_pen_risk_nm", None),
            "hedge_status": (None, None),
            "listing_date": ("pd_lstg_dt", None),
            "benchmark": ("cu_base_index", None),
            "strategy": ("cu_strtegy", None),
            "bond_kind": (None, None),
            "domestic_overseas_class": (None, None),
        }
    elif dataset_id == "PREF02N001":
        mappings = {
            "product_id": ("pd_itm_no", None),
            "alt_id": ("pd_itm_no_ma", None),
            "name": ("pd_nm", None),
            "short_name": ("pd_abrv_nm", None),
            "internal_type": ("pd_grp_no", None),
            "market": ("pd_exg_mkt_cd", None),
            "currency": ("pd_curr_cd", None),
            "trading_currency": ("pd_trd_ccy", None),
            "manager": ("cu_fund_mgmt_co", None),
            "manager_code": (None, None),
            "issuer": (None, None),
            "asset_type": ("wu_inv_ast_type", None),
            "region": ("wu_inv_rgn", None),
            "country_code": (None, None),
            "risk_grade": (None, None),
            "public_private": (None, None),
            "sale_status": ("pd_sale_yn", None),
            "pension_eligible": (None, None),
            "pension_risk": (None, None),
            "hedge_status": (None, None),
            "listing_date": ("pd_lstg_dt", None),
            "benchmark": ("cu_base_index", None),
            "strategy": ("cu_strtegy", None),
            "bond_kind": (None, None),
            "domestic_overseas_class": (None, None),
        }
    else:
        mappings = {
            "product_id": ("itm_no", None),
            "alt_id": ("std_itm_no", None),
            "name": ("itm_nm", None),
            "short_name": ("itm_abrv_nm", None),
            "internal_type": (None, "FUND"),
            "market": ("prvo_pbff_desc", None),
            "currency": ("curr_cd", None),
            "trading_currency": ("curr_cd", None),
            "manager": (None, None),
            "manager_code": ("or_co_xtn_itt_cd", None),
            "issuer": (None, None),
            "asset_type": ("or_attr_desc", None),
            "region": ("fd_ivst_rgn_desc", None),
            "country_code": (None, None),
            "risk_grade": ("zrin_fd_ivst_risk_grd_nm", None),
            "public_private": ("prvo_pbff_desc", None),
            "sale_status": ("sale_yn", None),
            "pension_eligible": (None, None),
            "pension_risk": (None, None),
            "hedge_status": ("exchdg_yn", None),
            "listing_date": (None, None),
            "benchmark": ("bmrk_nm", None),
            "strategy": (None, None),
            "bond_kind": (None, None),
            "domestic_overseas_class": ("ovrs_fd_desc", None),
        }

    catalog = pd.DataFrame(index=frame.index)
    catalog["scope"] = SCOPE_BY_DATASET[dataset_id]
    catalog["source_table_id"] = dataset_id
    for target, (source_field, constant) in mappings.items():
        catalog[target] = _column(frame, source_field, constant)
    if dataset_id == "PREF02N001":
        catalog["isin"] = _column(frame, "pd_isin_cd")
    else:
        catalog["isin"] = _isin_like(catalog["product_id"])
    catalog["product_uid"] = [
        _uid(dataset_id, product_id, int(row))
        for product_id, row in zip(catalog["product_id"], frame["source_excel_row"], strict=True)
    ]
    catalog["snapshot_date"] = SNAPSHOT_DATE
    for column in [
        "source_file",
        "source_sheet",
        "source_excel_row",
        "source_row_hash",
        "quality_status",
    ]:
        catalog[column] = frame[column]
    return catalog[CATALOG_COLUMNS].reset_index(drop=True)


def _attach_uid(dataset_id: str, frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["product_uid"] = [
        _uid(dataset_id, product_id, int(row))
        for product_id, row in zip(
            result[ID_FIELD[dataset_id]], result["source_excel_row"], strict=True
        )
    ]
    return result


def _parse_source_date(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    text = text.split("T", 1)[0].split(" ", 1)[0]
    text = text.replace("-", "").replace("/", "")
    if text.endswith(".0"):
        text = text[:-2]
    if not re.fullmatch(r"\d{8}", text) or text in {"00000000", "99991231"}:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _metric_as_of_field(dataset_id: str, source_field: str) -> str | None:
    if dataset_id == "PRBD01N001":
        market_fields = {
            "BUY_YIELD",
            "CORP_PRETAX_YIELD",
            "CORP_AFTER_TAX_YIELD",
            "AFTER_TAX_YIELD",
            "PREF_TAX_YIELD",
            "AVG_ANNUAL_TAX_YIELD",
            "DEPO_EQUIV_YIELD_154",
            "BUYABLE_QUANTITY",
            "REMAINING_DAYS",
            "DUR",
            "COV",
            "NDY_DUR",
            "NDY_COV",
            "EVAL_PRICE",
            "APPLIED_YIELD",
            "DIRTY",
            "NDY_EVAL_PRICE",
            "NDY_APPLIED_YIELD",
            "NDY_DIRTY",
        }
        return "PD_STD_INFO_UPDATE" if source_field in market_fields else None
    if dataset_id == "PREF01N001":
        if source_field.startswith("du_"):
            return "du_upt_dt"
        if source_field.startswith("cu_"):
            return "cu_upt_dt"
        if source_field.startswith("wu_"):
            return "wu_upt_dt"
    if dataset_id == "PREF02N001":
        if source_field == "du_clpr":
            return "du_clpr_base_dt"
        if source_field.startswith("du_") or source_field.startswith("ru_"):
            return "du_upt_dt"
        if source_field.startswith("cu_"):
            return "cu_upt_dt"
        if source_field.startswith("wu_"):
            return "wu_upt_dt"
    return None


def _value_as_of(
    source_field: str | None,
    raw_value: str | None,
    as_of_date: str | None,
) -> tuple[str | None, str]:
    """Return a field-level date and status without borrowing a row/snapshot date."""

    if not source_field or raw_value is None:
        return None, "UNAVAILABLE"
    if as_of_date:
        return as_of_date, "AVAILABLE"
    return None, "DATASET_SNAPSHOT_ONLY"


def _metric_value_quality(
    raw_value: str | None,
    numeric_value: float | None,
    row_quality: str,
    source_field: str,
    period: str,
    zero_policy: str,
    registry_quality: str,
) -> str:
    if row_quality == "QUARANTINE":
        return "QUARANTINE"
    if not source_field:
        return "UNAVAILABLE"
    if raw_value is None:
        return "MISSING_NULL"
    if raw_value.upper() in {"NULL", "NONE", "N/A", "NA", "NAN"}:
        return "SENTINEL"
    if period == "date" and raw_value in {"0", "00000000", "99991231"}:
        return "SENTINEL"
    if "UNUSABLE_CONSTANT" in registry_quality or zero_policy == "UNUSABLE_CONSTANT":
        return "UNUSABLE_CONSTANT"
    if zero_policy == "SENTINEL_MINUS_100_OPEN" and numeric_value == -100:
        return "SENTINEL"
    if numeric_value == 0:
        if zero_policy == "SENTINEL_MINUS_100_OPEN":
            return "ZERO_UNKNOWN"
        if zero_policy.startswith("SENTINEL"):
            return "SENTINEL"
        if zero_policy in {"ZERO_UNKNOWN", "ZERO_VALID_PENDING", "FIELD_POLICY_REQUIRED"}:
            return "ZERO_UNKNOWN"
        return "ZERO_VALID"
    if (
        numeric_value is not None
        and "OUTLIER" in registry_quality
        and (abs(numeric_value) >= 1_000 or numeric_value == -100)
    ):
        return "SUSPECT_OUTLIER"
    return "VALID" if row_quality == "VALID" else row_quality


def _metric_rows(
    package_root: Path,
    clean_frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    registry = pd.read_csv(
        package_root / "registry" / "metric_policy_v1.csv",
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )
    canonical_registry = pd.read_csv(
        package_root / "registry" / "canonical_fields_v1.csv",
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )
    as_of_by_metric = {
        row["canonical_field"]: row["as_of_field"].strip()
        for row in canonical_registry.to_dict(orient="records")
    }
    dataset_by_scope = {scope: dataset for dataset, scope in SCOPE_BY_DATASET.items()}
    bases: dict[str, pd.DataFrame] = {}
    for dataset_id, source in clean_frames.items():
        frame = (
            source.drop_duplicates("itm_no", keep="first") if dataset_id == "PRFD01N001" else source
        )
        bases[dataset_id] = _attach_uid(dataset_id, frame)

    frames: list[pd.DataFrame] = []
    text_units = {"YYYYMMDD", "CODE", "RATING", "TEXT", "RISK_SCALE", "UNAVAILABLE"}
    for spec in registry.to_dict(orient="records"):
        scope = spec["scope"]
        dataset_id = dataset_by_scope[scope]
        base = bases[dataset_id]
        source_field = spec["source_field"].strip()
        if source_field.upper() == "NONE":
            source_field = ""
        period = (
            "date"
            if spec["unit_status"] == "YYYYMMDD"
            else (
                spec["metric_id"].rsplit("return_", 1)[-1]
                if ".return_" in spec["metric_id"]
                else "point_in_time"
            )
        )
        ranking_policy = spec["ranking_policy"]
        registry_quality = (
            "UNAVAILABLE"
            if not source_field or ranking_policy == "UNAVAILABLE"
            else (
                "UNUSABLE_CONSTANT"
                if ranking_policy == "BLOCKED_CONSTANT"
                else (
                    "USABLE_WITH_OUTLIER_REVIEW"
                    if "OUTLIER_REVIEW" in ranking_policy
                    else ranking_policy
                )
            )
        )
        raw_values = _column(base, source_field if source_field else None).map(_clean_text)
        is_text = period == "date" or spec["unit_status"] in text_units
        numeric = (
            pd.to_numeric(raw_values.str.replace(",", "", regex=False), errors="coerce")
            if not is_text
            else pd.Series([float("nan")] * len(base), index=base.index)
        )
        numeric_text = raw_values.where(numeric.notna(), None) if not is_text else None
        value_text = raw_values if is_text else pd.Series([None] * len(base), index=base.index)
        declared_as_of = as_of_by_metric.get(spec["metric_id"], "").strip()
        as_of_field = (
            None
            if declared_as_of.upper() in {"", "NONE"}
            else declared_as_of
        )
        if spec["metric_id"] not in as_of_by_metric:
            as_of_field = _metric_as_of_field(dataset_id, source_field)
        if as_of_field:
            as_of_date = _column(base, as_of_field).map(_parse_source_date)
        else:
            as_of_date = pd.Series([None] * len(base), index=base.index)
        quality = [
            _metric_value_quality(
                raw_value,
                None if pd.isna(number) else float(number),
                row_quality,
                source_field,
                period,
                spec["zero_policy"],
                registry_quality,
            )
            for raw_value, number, row_quality in zip(
                raw_values, numeric, base["quality_status"], strict=True
            )
        ]
        as_of = [
            _value_as_of(source_field or None, raw_value, date_value)
            for raw_value, date_value in zip(raw_values, as_of_date, strict=True)
        ]
        item = pd.DataFrame(index=base.index)
        item["product_uid"] = base["product_uid"]
        item["metric_id"] = spec["metric_id"]
        item["value_num"] = numeric_text
        item["value_text"] = value_text
        item["unit"] = spec["unit_status"] or None
        item["period"] = period
        item["quality_status"] = quality
        item["source_table_id"] = dataset_id
        item["source_file"] = base["source_file"]
        item["source_sheet"] = base["source_sheet"]
        item["source_excel_row"] = base["source_excel_row"]
        item["source_field"] = source_field or None
        item["raw_value"] = raw_values
        item["as_of_date"] = [date_value for date_value, _ in as_of]
        item["as_of_status"] = [status for _, status in as_of]
        item["source_row_hash"] = base["source_row_hash"]
        frames.append(item[METRIC_COLUMNS])

    # Common identity fields are included so lookup answers can cite PD_NO/PD_NM,
    # pd_itm_no/pd_nm, and itm_no/itm_nm without reaching back into wide tables.
    for dataset_id, base in bases.items():
        for metric_id, source_field in (
            ("product.id", ID_FIELD[dataset_id]),
            ("product.name", NAME_FIELD[dataset_id]),
        ):
            raw_values = base[source_field].map(_clean_text)
            item = pd.DataFrame(index=base.index)
            item["product_uid"] = base["product_uid"]
            item["metric_id"] = metric_id
            item["value_num"] = None
            item["value_text"] = raw_values
            item["unit"] = None
            item["period"] = "point_in_time"
            item["quality_status"] = [
                "QUARANTINE"
                if row_quality == "QUARANTINE"
                else ("MISSING_NULL" if raw is None else "VALID")
                for raw, row_quality in zip(raw_values, base["quality_status"], strict=True)
            ]
            item["source_table_id"] = dataset_id
            item["source_file"] = base["source_file"]
            item["source_sheet"] = base["source_sheet"]
            item["source_excel_row"] = base["source_excel_row"]
            item["source_field"] = source_field
            item["raw_value"] = raw_values
            item["as_of_date"] = None
            item["as_of_status"] = [
                _value_as_of(source_field, raw_value, None)[1] for raw_value in raw_values
            ]
            item["source_row_hash"] = base["source_row_hash"]
            frames.append(item[METRIC_COLUMNS])
    return pd.concat(frames, ignore_index=True)


def _canonical_tables(
    package_root: Path,
    clean_frames: dict[str, pd.DataFrame],
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    catalog = pd.concat(
        [
            _catalog_for_dataset(dataset_id, clean_frames[dataset_id])
            for dataset_id in EXPECTED_DATASETS
        ],
        ignore_index=True,
    )
    if len(catalog) != 60_913 or catalog["product_uid"].nunique() != 60_913:
        raise RuntimeError("Canonical product reconciliation failed")

    locator_frames: list[pd.DataFrame] = []
    for dataset_id, source in clean_frames.items():
        attached = _attach_uid(dataset_id, source)
        locator = pd.DataFrame(
            {
                "product_uid": attached["product_uid"],
                "source_table_id": dataset_id,
                "product_id": attached[ID_FIELD[dataset_id]],
                "source_file": attached["source_file"],
                "source_sheet": attached["source_sheet"],
                "source_excel_row": attached["source_excel_row"],
                "source_row_hash": attached["source_row_hash"],
                "quality_status": attached["quality_status"],
                "quarantine_reason": attached["quarantine_reason"],
            }
        )
        locator_frames.append(locator)
    source_locator = pd.concat(locator_frames, ignore_index=True)

    fund = _attach_uid("PRFD01N001", clean_frames["PRFD01N001"])
    fund_attribute = pd.DataFrame(
        {
            "product_uid": fund["product_uid"],
            "attribute_code": fund["prfd_attr_cd"],
            "source_table_id": "PRFD01N001",
            "source_file": fund["source_file"],
            "source_sheet": fund["source_sheet"],
            "source_excel_row": fund["source_excel_row"],
            "source_field": "prfd_attr_cd",
            "raw_value": fund["prfd_attr_cd"],
            "source_row_hash": fund["source_row_hash"],
            "quality_status": fund["quality_status"],
        }
    )
    if fund_attribute[["product_uid", "attribute_code"]].duplicated().any():
        raise RuntimeError("Fund product+attribute key is not unique")

    quarantine_rows: list[pd.DataFrame] = []
    for dataset_id, source in clean_frames.items():
        selected = source[source["quality_status"] == "QUARANTINE"]
        if selected.empty:
            continue
        quarantine_rows.append(
            pd.DataFrame(
                {
                    "source_table_id": dataset_id,
                    "source_excel_row": selected["source_excel_row"],
                    "product_id": selected[ID_FIELD[dataset_id]],
                    "product_name": selected[NAME_FIELD[dataset_id]],
                    "reason": selected["quarantine_reason"],
                    "source_file": selected["source_file"],
                    "source_sheet": selected["source_sheet"],
                    "source_row_hash": selected["source_row_hash"],
                }
            )
        )
    quarantine = pd.concat(quarantine_rows, ignore_index=True)
    if len(quarantine) != 10:
        raise RuntimeError(f"Expected 10 quarantine rows, got {len(quarantine)}")

    metrics = _metric_rows(package_root, clean_frames)
    reconciliation = pd.DataFrame(
        [
            {
                "dataset_id": dataset_id,
                "raw_row_count": counts[0],
                "logical_product_count": counts[1],
                "serving_product_count": counts[2],
                "quarantine_count": counts[3],
            }
            for dataset_id, counts in RECONCILIATION.items()
        ]
        + [
            {
                "dataset_id": "TOTAL",
                "raw_row_count": 145_393,
                "logical_product_count": 60_913,
                "serving_product_count": 60_903,
                "quarantine_count": 10,
            }
        ]
    )
    return catalog, metrics, fund_attribute, source_locator, quarantine, reconciliation


def _register_table(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    frame: pd.DataFrame,
    select_sql: str = "SELECT * FROM _incoming",
) -> None:
    connection.register("_incoming", frame)
    try:
        connection.execute(f"CREATE TABLE {table_name} AS {select_sql}")
    finally:
        connection.unregister("_incoming")


def _create_database(
    database_path: Path,
    package_root: Path,
    verification: SourceVerification,
    raw_frames: dict[str, pd.DataFrame],
    clean_frames: dict[str, pd.DataFrame],
    parquet_dir: Path | None,
    *,
    vector_enabled: bool = False,
) -> BuildResult:
    catalog, metrics, fund_attribute, source_locator, quarantine, reconciliation = (
        _canonical_tables(package_root, clean_frames)
    )
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("PRAGMA threads=4")
        for schema in ("raw", "clean", "canonical", "serving", "metadata", "kg"):
            connection.execute(f"CREATE SCHEMA {schema}")

        for dataset_id in EXPECTED_DATASETS:
            raw_table = RAW_TABLE_NAMES[dataset_id]
            clean_table = raw_table.replace("raw_", "clean_", 1)
            _register_table(connection, f"raw.{raw_table}", raw_frames[dataset_id])
            _register_table(connection, f"clean.{clean_table}", clean_frames[dataset_id])
            connection.execute(f"CREATE VIEW {raw_table} AS SELECT * FROM raw.{raw_table}")

        _register_table(
            connection,
            "canonical.product_catalog",
            catalog,
            """SELECT
                product_uid, scope, source_table_id, product_id, alt_id, isin, name,
                short_name, internal_type, market, currency, trading_currency, manager,
                manager_code, issuer, asset_type, region, country_code, risk_grade,
                public_private, sale_status,
                pension_eligible, pension_risk, hedge_status, listing_date, benchmark, strategy,
                bond_kind, domestic_overseas_class,
                TRY_CAST(snapshot_date AS DATE) AS snapshot_date, source_file, source_sheet,
                CAST(source_excel_row AS BIGINT) AS source_excel_row, source_row_hash,
                quality_status
            FROM _incoming""",
        )
        _register_table(
            connection,
            "canonical.product_metrics",
            metrics,
            """SELECT
                product_uid, metric_id,
                TRY_CAST(REPLACE(value_num, ',', '') AS DECIMAL(38, 10)) AS value_num,
                value_text, unit, period, quality_status, source_table_id, source_file,
                source_sheet, CAST(source_excel_row AS BIGINT) AS source_excel_row,
                source_field, raw_value, TRY_CAST(as_of_date AS DATE) AS as_of_date,
                as_of_status, source_row_hash
            FROM _incoming""",
        )
        _register_table(connection, "canonical.fund_attribute", fund_attribute)
        _register_table(connection, "canonical.source_locator", source_locator)
        _register_table(connection, "canonical.quarantine", quarantine)
        _register_table(connection, "metadata.build_reconciliation", reconciliation)
        build_info = pd.DataFrame(
            [
                {"key": "snapshot_date", "value": SNAPSHOT_DATE},
                {"key": "source_zip_sha256", "value": verification.zip_sha256},
                {"key": "source_manifest_version", "value": verification.manifest_version},
                {"key": "raw_row_count", "value": "145393"},
                {"key": "serving_product_count", "value": "60903"},
            ]
        )
        _register_table(connection, "metadata.build_info", build_info)

        connection.execute(
            """CREATE TABLE serving.product_catalog AS
            SELECT * FROM canonical.product_catalog WHERE quality_status <> 'QUARANTINE'"""
        )
        connection.execute(
            """CREATE TABLE serving.product_metrics AS
            SELECT m.* FROM canonical.product_metrics m
            INNER JOIN serving.product_catalog p USING (product_uid)"""
        )
        connection.execute(
            """CREATE TABLE serving.fund_attribute AS
            SELECT * FROM canonical.fund_attribute WHERE quality_status <> 'QUARANTINE'"""
        )
        connection.execute(
            """CREATE TABLE serving.source_locator AS
            SELECT * FROM canonical.source_locator WHERE quality_status <> 'QUARANTINE'"""
        )
        connection.execute("CREATE TABLE serving.quarantine AS SELECT * FROM canonical.quarantine")
        connection.execute(
            "CREATE TABLE serving.bond_snapshot AS SELECT * FROM serving.product_catalog WHERE scope='bond'"
        )
        connection.execute(
            """CREATE TABLE serving.domestic_etp_snapshot AS
            SELECT * FROM serving.product_catalog WHERE scope='domestic_etp'"""
        )
        connection.execute(
            """CREATE TABLE serving.overseas_etp_snapshot AS
            SELECT * FROM serving.product_catalog WHERE scope='overseas_etp'"""
        )
        connection.execute(
            "CREATE TABLE serving.fund_product AS SELECT * FROM serving.product_catalog WHERE scope='fund'"
        )

        connection.execute(
            "CREATE UNIQUE INDEX product_uid_idx ON serving.product_catalog(product_uid)"
        )
        connection.execute("CREATE INDEX product_id_idx ON serving.product_catalog(product_id)")
        connection.execute("CREATE INDEX product_name_idx ON serving.product_catalog(name)")
        connection.execute(
            "CREATE UNIQUE INDEX product_metric_idx "
            "ON serving.product_metrics(product_uid, metric_id)"
        )
        connection.execute(
            "CREATE INDEX fund_attribute_idx ON serving.fund_attribute(product_uid, attribute_code)"
        )

        from etl.kg import build_kg
        from etl.lexical import build_lexical
        from etl.vectors import build_vectors

        kg_counts = build_kg(connection, package_root)
        lexical_counts = build_lexical(connection)
        vector_counts = build_vectors(connection, package_root, enabled=vector_enabled)

        for view_name in ("product_catalog", "product_metrics", "fund_attribute", "quarantine"):
            connection.execute(f"CREATE VIEW {view_name} AS SELECT * FROM serving.{view_name}")
        connection.execute(
            "CREATE VIEW dataset_stats AS SELECT * FROM metadata.build_reconciliation"
        )

        observed = dict(
            connection.execute(
                "SELECT scope, COUNT(*) FROM serving.product_catalog GROUP BY scope"
            ).fetchall()
        )
        expected_scope_counts = {
            "bond": 42_394,
            "domestic_etp": 1_733,
            "overseas_etp": 5_638,
            "fund": 11_138,
        }
        if observed != expected_scope_counts:
            raise RuntimeError(f"Serving count reconciliation failed: {observed}")
        if (
            connection.execute("SELECT COUNT(*) FROM canonical.source_locator").fetchone()[0]
            != 145_393
        ):
            raise RuntimeError("Source locator reconciliation failed")
        if (
            connection.execute("SELECT COUNT(*) FROM serving.fund_attribute").fetchone()[0]
            != 95_618
        ):
            raise RuntimeError("Fund attribute reconciliation failed")
        if connection.execute("SELECT COUNT(*) FROM serving.quarantine").fetchone()[0] != 10:
            raise RuntimeError("Quarantine reconciliation failed")
        if min(kg_counts.values()) <= 0:
            raise RuntimeError(f"KG reconciliation failed: {kg_counts}")
        if min(lexical_counts.values()) <= 0:
            raise RuntimeError(f"Lexical index reconciliation failed: {lexical_counts}")
        if vector_enabled and vector_counts.get("vec_embeddings", 0) <= 0:
            raise RuntimeError(f"Vector index reconciliation failed: {vector_counts}")

        if parquet_dir is not None:
            parquet_dir.mkdir(parents=True, exist_ok=True)
            export_tables = (
                "product_catalog",
                "product_metrics",
                "fund_attribute",
                "source_locator",
                "quarantine",
                "bond_snapshot",
                "domestic_etp_snapshot",
                "overseas_etp_snapshot",
                "fund_product",
            )
            for table in export_tables:
                output = parquet_dir / f"{table}.parquet"
                escaped = str(output.resolve()).replace("'", "''")
                connection.execute(
                    f"COPY serving.{table} TO '{escaped}' "
                    "(FORMAT PARQUET, COMPRESSION ZSTD, OVERWRITE_OR_IGNORE)"
                )

        result = BuildResult(
            database_path=database_path,
            source_zip_sha256=verification.zip_sha256,
            raw_row_count=145_393,
            logical_product_count=60_913,
            serving_product_count=60_903,
            quarantine_count=10,
            serving_fund_attribute_count=95_618,
            serving_metric_count=connection.execute(
                "SELECT COUNT(*) FROM serving.product_metrics"
            ).fetchone()[0],
            parquet_dir=parquet_dir,
            kg_counts=kg_counts,
            lexical_counts=lexical_counts,
            vector_counts=vector_counts,
        )
        connection.execute("CHECKPOINT")
        return result
    finally:
        connection.close()


def build_database(
    package_root: Path,
    output_database: Path,
    parquet_dir: Path | None = None,
    *,
    vector_enabled: bool = False,
) -> BuildResult:
    """Verify inputs and atomically build the full DuckDB serving database."""

    root = package_root.resolve()
    output = output_database.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    verification = verify_source_bundle(root)
    raw_frames, clean_frames = _prepare_frames(verification)

    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        interim = _create_database(
            temporary,
            root,
            verification,
            raw_frames,
            clean_frames,
            parquet_dir.resolve() if parquet_dir else None,
            vector_enabled=vector_enabled,
        )
        os.replace(temporary, output)
        return BuildResult(
            database_path=output,
            source_zip_sha256=interim.source_zip_sha256,
            raw_row_count=interim.raw_row_count,
            logical_product_count=interim.logical_product_count,
            serving_product_count=interim.serving_product_count,
            quarantine_count=interim.quarantine_count,
            serving_fund_attribute_count=interim.serving_fund_attribute_count,
            serving_metric_count=interim.serving_metric_count,
            kg_counts=interim.kg_counts,
            lexical_counts=interim.lexical_counts,
            vector_counts=interim.vector_counts,
            parquet_dir=interim.parquet_dir,
        )
    except Exception:
        if temporary.exists():
            temporary.unlink()
        wal = temporary.with_suffix(temporary.suffix + ".wal")
        if wal.exists():
            wal.unlink()
        raise
