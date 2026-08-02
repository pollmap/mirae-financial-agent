#!/usr/bin/env python3
"""Fail-closed verifier for immutable organizer inputs and all eight XLSX members."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def sha256_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256_stream(stream)


def fail(message: str) -> None:
    print(json.dumps({"status": "error", "message": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    manifest_path = ROOT / "artifacts/source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified = []
    resolved_sources = {}
    for source in manifest["sources"]:
        path = ROOT / source["source_path"]
        if not path.is_file():
            fail(f"Missing source: {path}")
        actual_size = path.stat().st_size
        actual_hash = sha256_file(path)
        if actual_size != source["size_bytes"]:
            fail(f"Size mismatch for {path.name}: expected {source['size_bytes']}, got {actual_size}")
        if actual_hash != source["sha256"]:
            fail(f"SHA-256 mismatch for {path.name}: expected {source['sha256']}, got {actual_hash}")
        verified.append({"path": str(path.relative_to(ROOT)), "sha256": actual_hash})
        resolved_sources[source["kind"]] = path

    inner_manifest_path = ROOT / "artifacts/xlsx_file_manifest.csv"
    with inner_manifest_path.open(encoding="utf-8-sig", newline="") as stream:
        expected_rows = list(csv.DictReader(stream))
    expected = {row["file_name_nfc"]: row for row in expected_rows}
    zip_path = resolved_sources["organizer_data_zip"]
    with zipfile.ZipFile(zip_path) as archive:
        members = [member for member in archive.infolist() if member.filename.lower().endswith(".xlsx")]
        if len(members) != 8:
            fail(f"Expected 8 XLSX members, got {len(members)}")
        observed_names = set()
        for member in members:
            posix = PurePosixPath(member.filename)
            if posix.is_absolute() or ".." in posix.parts:
                fail(f"Unsafe ZIP member path: {member.filename}")
            name_nfc = unicodedata.normalize("NFC", posix.name)
            observed_names.add(name_nfc)
            row = expected.get(name_nfc)
            if row is None:
                fail(f"Unexpected XLSX member: {name_nfc}")
            with archive.open(member, "r") as stream:
                actual_hash = sha256_stream(stream)
            if member.file_size != int(row["size_bytes"]):
                fail(f"Inner size mismatch: {name_nfc}")
            if actual_hash != row["sha256"]:
                fail(f"Inner SHA-256 mismatch: {name_nfc}")
        missing = set(expected) - observed_names
        if missing:
            fail("Missing XLSX members: " + ", ".join(sorted(missing)))

    print(json.dumps({
        "status": "ok",
        "verified_sources": verified,
        "verified_xlsx_count": len(expected_rows),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
