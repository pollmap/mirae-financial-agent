"""Fail-closed source verification and in-memory XLSX loading.

The organizer PDF, ZIP, and the eight XLSX members are immutable inputs.  This
module verifies the published manifests before returning the four datarows
workbooks.  ZIP members are never extracted into the repository.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pandas as pd

EXPECTED_DATASETS: dict[str, tuple[int, int]] = {
    "PRBD01N001": (42_394, 40),
    "PREF01N001": (1_734, 73),
    "PREF02N001": (5_646, 49),
    "PRFD01N001": (95_619, 45),
}


@dataclass(frozen=True)
class VerifiedWorkbook:
    dataset_id: str
    role: str
    archive_member: str
    source_file: str
    size_bytes: int
    sha256: str
    logical_rows: int | None
    logical_columns: int


@dataclass(frozen=True)
class SourceVerification:
    package_root: Path
    zip_path: Path
    zip_sha256: str
    manifest_version: str
    workbooks: tuple[VerifiedWorkbook, ...]

    @property
    def datarows(self) -> tuple[VerifiedWorkbook, ...]:
        return tuple(item for item in self.workbooks if item.role == "datarows")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_member_name(name: str) -> PurePosixPath:
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts:
        raise RuntimeError(f"Unsafe ZIP member path: {name}")
    return member


def verify_source_bundle(package_root: Path) -> SourceVerification:
    """Verify outer sources and every inner XLSX against checked-in manifests."""

    root = package_root.resolve()
    manifest_path = root / "artifacts" / "source_manifest.json"
    inner_manifest_path = root / "artifacts" / "xlsx_file_manifest.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    resolved_by_kind: dict[str, Path] = {}
    for source in manifest["sources"]:
        path = (root / source["source_path"]).resolve()
        if root not in path.parents:
            raise RuntimeError(f"Source escapes package root: {path}")
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size != int(source["size_bytes"]):
            raise RuntimeError(f"Source size mismatch: {source['source_path']}")
        actual_hash = _sha256_file(path)
        if actual_hash != source["sha256"]:
            raise RuntimeError(f"Source SHA-256 mismatch: {source['source_path']}")
        resolved_by_kind[source["kind"]] = path

    zip_path = resolved_by_kind["organizer_data_zip"]
    with inner_manifest_path.open(encoding="utf-8-sig", newline="") as stream:
        expected_rows = list(csv.DictReader(stream))
    if len(expected_rows) != 8:
        raise RuntimeError(f"Expected 8 workbook manifest rows, got {len(expected_rows)}")
    expected = {row["file_name_nfc"]: row for row in expected_rows}

    verified: list[VerifiedWorkbook] = []
    with zipfile.ZipFile(zip_path) as archive:
        xlsx_members = [
            item for item in archive.infolist() if item.filename.lower().endswith(".xlsx")
        ]
        if len(xlsx_members) != 8:
            raise RuntimeError(f"Expected 8 XLSX members, got {len(xlsx_members)}")
        observed: set[str] = set()
        for info in xlsx_members:
            member = _safe_member_name(info.filename)
            file_name_nfc = unicodedata.normalize("NFC", member.name)
            row = expected.get(file_name_nfc)
            if row is None:
                raise RuntimeError(f"Unexpected XLSX member: {file_name_nfc}")
            payload = archive.read(info)
            if len(payload) != int(row["size_bytes"]):
                raise RuntimeError(f"Inner size mismatch: {file_name_nfc}")
            actual_hash = _sha256_bytes(payload)
            if actual_hash != row["sha256"]:
                raise RuntimeError(f"Inner SHA-256 mismatch: {file_name_nfc}")
            logical_rows = int(row["logical_data_rows"]) if row["logical_data_rows"] else None
            verified.append(
                VerifiedWorkbook(
                    dataset_id=row["dataset_id"],
                    role=row["role"],
                    archive_member=info.filename,
                    source_file=file_name_nfc,
                    size_bytes=info.file_size,
                    sha256=actual_hash,
                    logical_rows=logical_rows,
                    logical_columns=int(row["logical_columns"]),
                )
            )
            observed.add(file_name_nfc)
        missing = set(expected) - observed
        if missing:
            raise RuntimeError("Missing XLSX members: " + ", ".join(sorted(missing)))

    datarows = [item for item in verified if item.role == "datarows"]
    if {item.dataset_id for item in datarows} != set(EXPECTED_DATASETS):
        raise RuntimeError("Datarows dataset set does not match the four official datasets")
    for item in datarows:
        expected_rows_count, expected_columns = EXPECTED_DATASETS[item.dataset_id]
        if item.logical_rows != expected_rows_count or item.logical_columns != expected_columns:
            raise RuntimeError(f"Manifest dimensions mismatch for {item.dataset_id}")

    return SourceVerification(
        package_root=root,
        zip_path=zip_path,
        zip_sha256=_sha256_file(zip_path),
        manifest_version=manifest["manifest_version"],
        workbooks=tuple(sorted(verified, key=lambda item: (item.dataset_id, item.role))),
    )


def load_datarows(
    verification: SourceVerification,
) -> dict[str, tuple[pd.DataFrame, VerifiedWorkbook]]:
    """Read all four datarows sheets from the verified ZIP into memory."""

    result: dict[str, tuple[pd.DataFrame, VerifiedWorkbook]] = {}
    by_member = {item.archive_member: item for item in verification.datarows}
    with zipfile.ZipFile(verification.zip_path) as archive:
        for member_name, workbook in by_member.items():
            payload = archive.read(member_name)
            frame = pd.read_excel(
                io.BytesIO(payload),
                sheet_name="datarows",
                dtype=object,
                engine="openpyxl",
                keep_default_na=False,
                na_filter=False,
            )
            frame.columns = [str(column).strip() for column in frame.columns]
            expected_rows, expected_columns = EXPECTED_DATASETS[workbook.dataset_id]
            if frame.shape != (expected_rows, expected_columns):
                raise RuntimeError(
                    f"Workbook dimensions mismatch for {workbook.dataset_id}: "
                    f"expected {(expected_rows, expected_columns)}, got {frame.shape}"
                )
            result[workbook.dataset_id] = (frame, workbook)
    return result
