from __future__ import annotations

import csv
import hashlib
import re
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl import build_database, verify_source_bundle  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def built_database(tmp_path_factory: pytest.TempPathFactory):
    verification = verify_source_bundle(ROOT)
    original_hash = _sha256(verification.zip_path)
    output = tmp_path_factory.mktemp("etl") / "test.duckdb"
    result = build_database(ROOT, output, parquet_dir=None)
    assert _sha256(verification.zip_path) == original_hash
    return result


def test_official_source_manifest_is_verified() -> None:
    verification = verify_source_bundle(ROOT)
    assert (
        verification.zip_sha256
        == "c3809aca73396f57242ded0188fa06a3d271bd4ad65010e53d5533efc7c18163"
    )
    assert len(verification.workbooks) == 8
    assert len(verification.datarows) == 4


def test_raw_and_serving_reconciliation(built_database) -> None:
    result = built_database
    assert result.raw_row_count == 145_393
    assert result.logical_product_count == 60_913
    assert result.serving_product_count == 60_903
    assert result.quarantine_count == 10
    assert result.serving_fund_attribute_count == 95_618
    assert result.serving_metric_count == 1_156_332

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        raw_counts = {
            "PRBD01N001": connection.execute("SELECT COUNT(*) FROM raw_prbd01n001").fetchone()[0],
            "PREF01N001": connection.execute("SELECT COUNT(*) FROM raw_pref01n001").fetchone()[0],
            "PREF02N001": connection.execute("SELECT COUNT(*) FROM raw_pref02n001").fetchone()[0],
            "PRFD01N001": connection.execute("SELECT COUNT(*) FROM raw_prfd01n001").fetchone()[0],
        }
        assert raw_counts == {
            "PRBD01N001": 42_394,
            "PREF01N001": 1_734,
            "PREF02N001": 5_646,
            "PRFD01N001": 95_619,
        }
        serving = dict(
            connection.execute(
                "SELECT scope, COUNT(*) FROM product_catalog GROUP BY scope"
            ).fetchall()
        )
        assert serving == {
            "bond": 42_394,
            "domestic_etp": 1_733,
            "overseas_etp": 5_638,
            "fund": 11_138,
        }
        assert connection.execute("SELECT COUNT(*) FROM fund_attribute").fetchone()[0] == 95_618
        assert connection.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0] == 10
    finally:
        connection.close()


def test_serving_contract_and_source_lineage(built_database) -> None:
    connection = duckdb.connect(str(built_database.database_path), read_only=True)
    try:
        catalog_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('product_catalog')").fetchall()
        }
        assert {
            "product_uid",
            "scope",
            "product_id",
            "name",
            "trading_currency",
            "manager_code",
            "country_code",
            "bond_kind",
            "domestic_overseas_class",
            "public_private",
            "source_excel_row",
            "source_row_hash",
            "quality_status",
        } <= catalog_columns
        metric_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('product_metrics')").fetchall()
        }
        assert (
            set(
                [
                    "product_uid",
                    "metric_id",
                    "value_num",
                    "value_text",
                    "quality_status",
                    "source_table_id",
                    "source_excel_row",
                    "source_field",
                    "raw_value",
                    "as_of_date",
                    "as_of_status",
                    "source_row_hash",
                ]
            )
            <= metric_columns
        )
        assert connection.execute(
            "SELECT COUNT(*) = COUNT(DISTINCT product_uid) FROM product_catalog"
        ).fetchone()[0]
        locator_count = connection.execute(
            "SELECT COUNT(*) FROM canonical.source_locator"
        ).fetchone()[0]
        assert locator_count == 145_393
        hashes = connection.execute(
            "SELECT source_row_hash FROM canonical.source_locator USING SAMPLE 100 ROWS"
        ).fetchall()
        assert hashes and all(re.fullmatch(r"[a-f0-9]{64}", value) for (value,) in hashes)
        identifiers = connection.execute(
            """SELECT product_id, product_uid, trading_currency, public_private
            FROM product_catalog
            WHERE product_id IN ('KR7305080004', 'EES', 'KR5114420158')
            ORDER BY product_id"""
        ).fetchall()
        assert identifiers == [
            ("EES", "GLOBAL_ETP:PREF02N001:EES", "USD", None),
            ("KR5114420158", "FUND:PRFD01N001:KR5114420158", "KRW", "공모"),
            ("KR7305080004", "KR_ETP:PREF01N001:KR7305080004", "CURR_CD_KRW", None),
        ]
        bond_semantics = connection.execute(
            """SELECT manager, issuer, region, country_code, strategy, bond_kind
            FROM product_catalog WHERE product_id='KR101501DA16'"""
        ).fetchone()
        assert bond_semantics == (None, "대한민국", None, "KR", None, "국민주택1종")
        fund_semantics = connection.execute(
            """SELECT manager, manager_code, region, strategy, domestic_overseas_class
            FROM product_catalog WHERE product_id='KR5114420158'"""
        ).fetchone()
        assert fund_semantics == (None, "00040010", "국내", None, "국내")
    finally:
        connection.close()


def test_quarantine_and_fund_dedup_are_exact(built_database) -> None:
    connection = duckdb.connect(str(built_database.database_path), read_only=True)
    try:
        by_table = dict(
            connection.execute(
                "SELECT source_table_id, COUNT(*) FROM quarantine GROUP BY source_table_id"
            ).fetchall()
        )
        assert by_table == {"PREF01N001": 1, "PREF02N001": 8, "PRFD01N001": 1}
        domestic = connection.execute(
            "SELECT source_excel_row, product_id FROM quarantine WHERE source_table_id='PREF01N001'"
        ).fetchone()
        assert domestic == (1_155, "KR")
        damaged = connection.execute(
            "SELECT source_excel_row, product_id FROM quarantine WHERE source_table_id='PRFD01N001'"
        ).fetchone()
        assert damaged == (84_563, '"')
        overseas = {
            row[0]
            for row in connection.execute(
                "SELECT product_id FROM quarantine WHERE source_table_id='PREF02N001'"
            ).fetchall()
        }
        assert overseas == {"XW", "BTCK.K", "BAY", "BZZ", "AV", "ONX", "PINC.K", "OWN"}
        assert (
            connection.execute("SELECT COUNT(*) FROM serving.fund_product").fetchone()[0] == 11_138
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM serving.fund_attribute").fetchone()[0]
            == 95_618
        )
    finally:
        connection.close()


def test_metric_long_table_supports_grounded_lookup(built_database) -> None:
    connection = duckdb.connect(str(built_database.database_path), read_only=True)
    try:
        rows = connection.execute(
            """SELECT metric_id, value_text, source_field, source_excel_row
            FROM product_metrics
            WHERE product_uid='BOND:PRBD01N001:KR101501DA16'
              AND metric_id IN ('product.id', 'product.name')
            ORDER BY metric_id"""
        ).fetchall()
        assert rows == [
            ("product.id", "KR101501DA16", "PD_NO", 2),
            ("product.name", "국민주택1종채권 20-01", "PD_NM", 2),
        ]
        metric_ids = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT metric_id FROM product_metrics"
            ).fetchall()
        }
        assert {
            "bond.buy_yield",
            "domestic_etp.return_1y",
            "overseas_etp.aum_last",
            "fund.return_1y",
            "product.id",
            "product.name",
        } <= metric_ids
        with (ROOT / "registry" / "metric_policy_v1.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            policy_metric_ids = {row["metric_id"] for row in csv.DictReader(handle)}
        assert len(policy_metric_ids) == 59
        assert metric_ids == {*policy_metric_ids, "product.id", "product.name"}
        assert len(metric_ids) == 61
        assert connection.execute("SELECT COUNT(*) FROM product_metrics").fetchone()[0] == 1_156_332
        assert (
                connection.execute(
                    "SELECT COUNT(*) FROM product_metrics "
                    "WHERE metric_id='domestic_etp.return_1y' "
                    "AND raw_value IS NOT NULL AND as_of_date='2026-06-15' "
                    "AND as_of_status='AVAILABLE'"
                ).fetchone()[0]
                == 1_235
            )
        assert connection.execute(
            "SELECT raw_value, CAST(as_of_date AS VARCHAR) FROM product_metrics "
            "WHERE product_uid='GLOBAL_ETP:PREF02N001:SPY' "
            "AND metric_id='overseas_etp.aum_last'"
        ).fetchone() == ("783071880000.00", "2026-06-14")
        assert connection.execute(
            """SELECT COUNT(*) FROM product_metrics
            WHERE raw_value IS NULL
              AND (as_of_date IS NOT NULL OR as_of_status <> 'UNAVAILABLE')"""
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT COUNT(*) FROM product_metrics
            WHERE raw_value IS NOT NULL AND as_of_date IS NOT NULL
              AND as_of_status <> 'AVAILABLE'"""
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT COUNT(*) FROM product_metrics
            WHERE raw_value IS NOT NULL AND as_of_date IS NULL
              AND as_of_status <> 'DATASET_SNAPSHOT_ONLY'"""
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT COUNT(*) FROM product_metrics
            WHERE metric_id IN ('product.id', 'product.name')
              AND raw_value IS NOT NULL
              AND as_of_status <> 'DATASET_SNAPSHOT_ONLY'"""
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT COUNT(*) FROM product_metrics
            WHERE metric_id='domestic_etp.return_1y'
              AND value_num=-100 AND quality_status <> 'SENTINEL'"""
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT COUNT(*) FROM product_metrics
            WHERE metric_id='domestic_etp.return_1y'
              AND value_num=0 AND quality_status <> 'ZERO_UNKNOWN'"""
        ).fetchone()[0] == 0
    finally:
        connection.close()
