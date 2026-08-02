from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.build import (  # noqa: E402
    _catalog_for_dataset,
    _metric_value_quality,
    _value_as_of,
)


def _source_row(**values: str | int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source_file": "official.xlsx",
                "source_sheet": "Sheet1",
                "source_excel_row": 2,
                "source_row_hash": "a" * 64,
                "quality_status": "VALID",
                **values,
            }
        ]
    )


def test_catalog_keeps_bond_country_issuer_and_kind_semantically_distinct() -> None:
    catalog = _catalog_for_dataset(
        "PRBD01N001",
        _source_row(
            PD_NO="KR101501DA16",
            PD_ABRV_NM="국민주택20-01",
            PD_NM="국민주택1종채권 20-01",
            PD_PBCM="대한민국",
            PD_CTRY_CD="KR",
            BD_KND="국민주택1종",
        ),
    )

    row = catalog.iloc[0]
    assert row["issuer"] == "대한민국"
    assert row["country_code"] == "KR"
    assert row["bond_kind"] == "국민주택1종"
    assert row["manager"] is None
    assert row["region"] is None
    assert row["strategy"] is None


def test_catalog_keeps_fund_codes_and_classes_out_of_name_fields() -> None:
    catalog = _catalog_for_dataset(
        "PRFD01N001",
        _source_row(
            itm_no="KR5114420158",
            std_itm_no="K55114BA2015",
            itm_nm="미래에셋펀드",
            itm_abrv_nm="미래에셋펀드",
            or_co_xtn_itt_cd="00040010",
            fd_ivst_rgn_desc="국내",
            ovrs_fd_desc="국내",
        ),
    )

    row = catalog.iloc[0]
    assert row["manager_code"] == "00040010"
    assert row["domestic_overseas_class"] == "국내"
    assert row["region"] == "국내"
    assert row["manager"] is None
    assert row["strategy"] is None


def test_catalog_preserves_existing_etp_regions() -> None:
    domestic = _catalog_for_dataset(
        "PREF01N001",
        _source_row(pd_itm_no="KR7000000001", pd_nm="국내 ETF", wu_inv_rgn="한국"),
    )
    overseas = _catalog_for_dataset(
        "PREF02N001",
        _source_row(pd_itm_no="SPY", pd_nm="SPY", wu_inv_rgn="미국"),
    )

    assert domestic.iloc[0]["region"] == "한국"
    assert overseas.iloc[0]["region"] == "미국"
    assert domestic.iloc[0]["country_code"] is None
    assert overseas.iloc[0]["country_code"] is None


def test_value_as_of_requires_both_a_present_value_and_actual_field_date() -> None:
    assert _value_as_of("du_er_1y", "12.34", "2026-06-15") == (
        "2026-06-15",
        "AVAILABLE",
    )
    assert _value_as_of("du_er_1y", "12.34", None) == (
        None,
        "DATASET_SNAPSHOT_ONLY",
    )
    assert _value_as_of("du_er_1y", None, "2026-06-15") == (None, "UNAVAILABLE")
    assert _value_as_of(None, "12.34", "2026-06-15") == (None, "UNAVAILABLE")


def test_minus_100_open_policy_does_not_treat_zero_as_the_sentinel() -> None:
    args = {
        "row_quality": "VALID",
        "source_field": "du_er_1y",
        "period": "1y",
        "zero_policy": "SENTINEL_MINUS_100_OPEN",
        "registry_quality": "USABLE_WITH_OUTLIER_REVIEW",
    }

    assert _metric_value_quality("-100.00", -100.0, **args) == "SENTINEL"
    assert _metric_value_quality("0.00", 0.0, **args) == "ZERO_UNKNOWN"
    assert _metric_value_quality("12.34", 12.34, **args) == "VALID"
