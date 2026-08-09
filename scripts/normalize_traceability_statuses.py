#!/usr/bin/env python3
"""Normalize traceability status labels to the v4 release vocabulary."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACEABILITY = ROOT / "artifacts" / "requirements_traceability.csv"


def normalize_status(value: str) -> str:
    status = value.strip().upper()
    if status in {
        "VERIFIED_LOCAL",
        "VERIFIED_FIXTURE",
        "PENDING_EXTERNAL",
        "HISTORICAL",
        "NOT_APPLICABLE",
    }:
        return status
    if any(
        marker in status
        for marker in (
            "WAITING",
            "NOT_RUN",
            "PENDING",
            "DRAFT",
            "BASELINED",
        )
    ):
        return "PENDING_EXTERNAL"
    if "VERIFIED" in status or status.startswith("IMPLEMENTED_"):
        return "VERIFIED_LOCAL"
    raise ValueError(f"unmapped traceability status: {value!r}")


def main() -> None:
    with TRACEABILITY.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fieldnames = reader.fieldnames
    if not fieldnames or "status" not in fieldnames:
        raise SystemExit("traceability CSV must contain a status column")
    for row in rows:
        row["status"] = normalize_status(row["status"])
    with TRACEABILITY.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
