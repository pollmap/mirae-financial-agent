from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from app.config import Settings
from app.execution.engine import DuckDBEngine
from app.planner.deterministic import DeterministicPlanner
from app.service import AgentService

ROOT = Path(__file__).resolve().parents[2]


def _schema(name: str) -> dict[str, object]:
    return json.loads((ROOT / "contracts" / name).read_text(encoding="utf-8"))


def test_query_plan_v11_matches_local_model() -> None:
    plan = asyncio.run(
        DeterministicPlanner().plan("국내 ETF만 대상으로 1년 수익률이 높은 3개를 알려줘")
    )
    jsonschema.validate(plan.model_dump(mode="json"), _schema("query-plan-v1.schema.json"))


def test_evidence_and_provisional_response_match_contracts() -> None:
    database = ROOT / "data" / "serving" / "mirae_agent.duckdb"
    settings = Settings(environment="test", database_path=database, planner_mode="deterministic")
    service = AgentService(settings, DeterministicPlanner(), DuckDBEngine(database))
    response = asyncio.run(
        service.answer(
            question_id="SCHEMA-1",
            question="국내 ETF만 대상으로 1년 수익률이 높은 3개를 알려줘",
        )
    )
    context = json.loads(response.retrieved_context)
    jsonschema.validate(
        context,
        _schema("evidence-bundle-v1.schema.json"),
        format_checker=jsonschema.FormatChecker(),
    )
    jsonschema.validate(
        response.model_dump(mode="json"),
        _schema("provisional-api-response.schema.json"),
    )


def test_clarification_evidence_matches_contract() -> None:
    database = ROOT / "data" / "serving" / "mirae_agent.duckdb"
    settings = Settings(environment="test", database_path=database, planner_mode="deterministic")
    service = AgentService(settings, DeterministicPlanner(), DuckDBEngine(database))
    response = asyncio.run(
        service.answer(question_id="SCHEMA-C", question="수익률 높은 ETF 3개 알려줘")
    )
    jsonschema.validate(
        json.loads(response.retrieved_context),
        _schema("evidence-bundle-v1.schema.json"),
        format_checker=jsonschema.FormatChecker(),
    )


def test_provisional_request_requires_clarification_follow_up_pair() -> None:
    schema = _schema("provisional-api-request.schema.json")
    base = {"question_id": "Q1", "question": "국내 ETF를 보여줘"}
    jsonschema.validate(base, schema)
    jsonschema.validate(
        {
            **base,
            "clarification_token": "signed-token",
            "clarification_response": "국내",
        },
        schema,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**base, "clarification_token": "signed-token"}, schema)


def test_generated_release_manifest_matches_contract() -> None:
    manifest = json.loads(
        (ROOT / "artifacts" / "release_manifest.generated.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(
        manifest,
        _schema("release-manifest.schema.json"),
        format_checker=jsonschema.FormatChecker(),
    )


def test_release_manifest_hashes_the_explicit_database_artifact(tmp_path: Path) -> None:
    database = ROOT / "data" / "serving" / "mirae_agent.duckdb"
    output = tmp_path / "manifest.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_release_manifest.py"),
            "--output",
            str(output),
            "--serving-database",
            str(database),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    with database.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    assert manifest["serving_database_sha256"] == digest.hexdigest()


def test_release_manifest_rejects_an_empty_database_artifact(tmp_path: Path) -> None:
    database = tmp_path / "empty.duckdb"
    database.touch()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_release_manifest.py"),
            "--output",
            str(tmp_path / "manifest.json"),
            "--serving-database",
            str(database),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "non-empty file" in result.stderr


def test_release_manifest_rejects_a_non_duckdb_artifact(tmp_path: Path) -> None:
    database = tmp_path / "not-a-database.duckdb"
    database.write_bytes(b"not empty but invalid")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_release_manifest.py"),
            "--output",
            str(tmp_path / "manifest.json"),
            "--serving-database",
            str(database),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "readiness validation" in result.stderr


def test_release_manifest_rejects_test_count_mismatch(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "status": "LOCAL_PASS_EXTERNAL_GATES_PENDING",
                "pytest_summary": {"passed": 3, "failed": 0, "skipped": 0},
                "checks": [{"command": "pytest", "status": "PASS"}],
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_release_manifest.py"),
            "--output",
            str(tmp_path / "manifest.json"),
            "--serving-database",
            str(ROOT / "data" / "serving" / "mirae_agent.duckdb"),
            "--test-report",
            str(report),
            "--passed",
            "2",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "do not match" in result.stderr
