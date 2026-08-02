#!/usr/bin/env python3
"""Build the verified organizer snapshot into DuckDB and Parquet serving data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from etl import build_database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=PACKAGE_ROOT / "data" / "serving" / "mirae_agent.duckdb",
        help="DuckDB output path",
    )
    parser.add_argument(
        "--parquet-dir",
        type=Path,
        default=PACKAGE_ROOT / "data" / "parquet",
        help="Serving Parquet output directory",
    )
    parser.add_argument(
        "--no-parquet",
        action="store_true",
        help="Skip Parquet export and build only DuckDB",
    )
    args = parser.parse_args()
    result = build_database(
        package_root=PACKAGE_ROOT,
        output_database=args.output,
        parquet_dir=None if args.no_parquet else args.parquet_dir,
    )
    print(json.dumps({"status": "ok", **result.as_dict()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
