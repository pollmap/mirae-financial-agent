from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from qa_chat import admin

ROOT = Path(__file__).resolve().parents[2]
QA_DEPLOY = ROOT / "deploy" / "qa"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _powershell(command: str) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("PowerShell deployment tests require Windows PowerShell or pwsh")
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_all_qa_powershell_scripts_parse() -> None:
    script = (
        "$failed=$false; "
        f"Get-ChildItem -LiteralPath {_quote(QA_DEPLOY)} -Filter '*.ps1' | ForEach-Object {{ "
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile($_.FullName,"
        "[ref]$tokens,[ref]$errors) | Out-Null; "
        "if($errors.Count -gt 0){$failed=$true; $errors | ForEach-Object {$_.Message}} }; "
        "if($failed){exit 9}"
    )
    result = _powershell(script)
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize(
    "cidr",
    ["10.0.0.0/8", "10.20.30.0/24", "172.16.0.0/12", "172.30.1.0/24", "192.168.1.0/24"],
)
def test_private_cidr_accepts_only_canonical_contained_ranges(cidr: str) -> None:
    security = QA_DEPLOY / "QaSecurity.ps1"
    result = _powershell(f". {_quote(security)}; Assert-QaPrivateCidr -Cidr '{cidr}'")
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize(
    "cidr",
    [
        "172.16.0.0/8",
        "192.168.1.0/8",
        "172.30.1.20/24",
        "192.168.0.0/15",
        "8.8.8.0/24",
        "10.1/16",
        "167772160/8",
    ],
)
def test_private_cidr_rejects_partial_public_or_noncanonical_ranges(cidr: str) -> None:
    security = QA_DEPLOY / "QaSecurity.ps1"
    result = _powershell(
        f". {_quote(security)}; Assert-QaPrivateCidr -Cidr '{cidr}'; exit 0"
    )
    assert result.returncode != 0


def test_secret_generator_applies_fail_closed_acl_and_distinct_values(tmp_path: Path) -> None:
    output = tmp_path / "protected-secrets"
    generator = QA_DEPLOY / "New-QaSecrets.ps1"
    security = QA_DEPLOY / "QaSecurity.ps1"
    result = _powershell(
        f"& {_quote(generator)} -OutputDirectory {_quote(output)}; "
        f". {_quote(security)}; "
        f"Assert-QaRestrictedAcl -LiteralPath {_quote(output)}; "
        f"Get-ChildItem -LiteralPath {_quote(output)} | ForEach-Object {{ "
        "Assert-QaRestrictedAcl -LiteralPath $_.FullName }"
    )
    assert result.returncode == 0, result.stderr + result.stdout
    files = sorted(output.glob("*.key"))
    assert [path.name for path in files] == [
        "engine_clarification.key",
        "qa_auth.key",
        "qa_transcript.key",
    ]
    assert len({path.read_bytes() for path in files}) == 3

    broad_acl = _powershell(
        f". {_quote(security)}; $path={_quote(files[0])}; "
        "& icacls.exe $path /grant '*S-1-1-0:(R)' | Out-Null; "
        "if($LASTEXITCODE -ne 0){exit 8}; Assert-QaRestrictedAcl -LiteralPath $path"
    )
    assert broad_acl.returncode != 0


def test_secret_generator_refuses_repository_or_nonempty_directory(tmp_path: Path) -> None:
    generator = QA_DEPLOY / "New-QaSecrets.ps1"
    inside_repo = _powershell(
        f"& {_quote(generator)} -OutputDirectory {_quote(QA_DEPLOY)}"
    )
    assert inside_repo.returncode != 0

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    marker = nonempty / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    existing = _powershell(
        f"& {_quote(generator)} -OutputDirectory {_quote(nonempty)}"
    )
    assert existing.returncode != 0
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_compose_keeps_gateway_available_and_binds_engine_revision() -> None:
    compose = (ROOT / "compose.qa.yaml").read_text(encoding="utf-8")
    gateway = compose.split("  qa-gateway:", 1)[1].split("  qa-local-edge:", 1)[0]
    assert re.search(r"depends_on:\s+engine:[\s\S]*?condition:\s+service_started", gateway)
    assert "condition: service_healthy" not in gateway
    assert "ENGINE_GIT_SHA: ${ENGINE_GIT_SHA:?" in compose
    assert compose.count("pull_policy: never") >= 2

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG ENGINE_GIT_SHA" in dockerfile
    assert "LABEL org.opencontainers.image.revision=$ENGINE_GIT_SHA" in dockerfile


def test_qa_image_switches_to_production_react_before_frontend_build() -> None:
    dockerfile = (ROOT / "Dockerfile.qa").read_text(encoding="utf-8")
    install = dockerfile.index("RUN npm ci --ignore-scripts")
    production = dockerfile.index("ENV NODE_ENV=production")
    build = dockerfile.index("RUN npm run build")
    assert install < production < build


def test_host_preflight_checks_actual_image_data_gate_and_secrets() -> None:
    preflight = (QA_DEPLOY / "Test-QaReleaseDeployment.ps1").read_text(encoding="utf-8")
    required_fragments = (
        "'status', '--porcelain=v1', '--untracked-files=all'",
        "'image', 'inspect', $EngineImage",
        "org.opencontainers.image.revision",
        "/app/data/serving/mirae_agent.duckdb",
        "release_evidence.py",
        "Assert-QaDistinctSecretFiles",
        "RunningEngineContainer",
        "'inspect', '--format', '{{.Image}}'",
    )
    for fragment in required_fragments:
        assert fragment in preflight


def test_host_preflight_passes_only_when_all_bound_identities_match(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "preflight-secrets"
    gate = tmp_path / "qa_release_gate.json"
    gate.write_text("{}", encoding="utf-8")
    generator = QA_DEPLOY / "New-QaSecrets.ps1"
    security = QA_DEPLOY / "QaSecurity.ps1"
    preflight = QA_DEPLOY / "Test-QaReleaseDeployment.ps1"
    git_sha = "a" * 40
    image_digest = "sha256:" + "b" * 64
    data_hash = "sha256:" + "c" * 64
    image_json = (
        '[{"Id":"'
        + image_digest
        + '","Config":{"Labels":{"org.opencontainers.image.revision":"'
        + git_sha
        + '"}}}]'
    )
    command = (
        f"& {_quote(generator)} -OutputDirectory {_quote(secrets_dir)} | Out-Null; "
        f". {_quote(security)}; $hcx=Join-Path {_quote(secrets_dir)} 'hcx_api.key'; "
        "[IO.File]::WriteAllText($hcx,('hcx-' + ('x' * 64))); "
        "Set-QaRestrictedAcl -LiteralPath $hcx; "
        f"function global:git {{ if($args -contains 'rev-parse'){{'{git_sha}'}}; "
        "$global:LASTEXITCODE=0 }; "
        f"function global:docker {{ if($args[0] -eq 'image'){{'{image_json}'}} "
        f"elseif($args[0] -eq 'inspect'){{'{image_digest}'}} "
        f"elseif($args -contains '-c'){{'{data_hash}'}} else{{'{{}}'}}; "
        "$global:LASTEXITCODE=0 }; "
        f"& {_quote(preflight)} -ProjectRoot {_quote(ROOT)} -EngineImage 'engine:test' "
        f"-ExpectedEngineGitSha '{git_sha}' -ExpectedEngineImageDigest '{image_digest}' "
        f"-ExpectedDataHash '{data_hash}' -ExpectedHcxModelId 'HCX-TEST' "
        "-ExpectedHcxBaseUrl 'https://clovastudio.stream.ntruss.com' "
        f"-ReleaseGatePath {_quote(gate)} -HcxApiKeyFile $hcx "
        f"-EngineClarificationSigningKeyFile {_quote(secrets_dir / 'engine_clarification.key')} "
        f"-QaTranscriptKeyFile {_quote(secrets_dir / 'qa_transcript.key')} "
        f"-QaAuthSecretFile {_quote(secrets_dir / 'qa_auth.key')} "
        "-RunningEngineContainer 'engine-running'"
    )
    result = _powershell(command)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "QA_RELEASE_DEPLOYMENT_PREFLIGHT=PASS" in result.stdout


def test_admin_revoke_can_read_invite_from_stdin_without_command_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FakeRepository:
        revoked: str | None = None

        def revoke_invite(self, code: str) -> bool:
            self.revoked = code
            return True

        def close(self) -> None:
            return None

    repository = FakeRepository()
    monkeypatch.setattr(admin.QASettings, "from_env", lambda: object())
    monkeypatch.setattr(admin, "_repository", lambda _: repository)
    monkeypatch.setattr(sys, "argv", ["qa-admin", "revoke-invite"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("one-time-secret\n"))

    assert admin.main() == 0
    assert repository.revoked == "one-time-secret"
    assert '"revoked": true' in capsys.readouterr().out


@pytest.mark.parametrize("use_live_path", [False, True])
def test_admin_backup_refuses_overwrite_or_live_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_live_path: bool
) -> None:
    live_database = tmp_path / "qa.sqlite3"
    live_database.write_bytes(b"live")
    destination = live_database if use_live_path else tmp_path / "existing-backup.sqlite3"
    if not use_live_path:
        destination.write_bytes(b"preserve")

    class FakeRepository:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        admin.QASettings,
        "from_env",
        lambda: SimpleNamespace(database_path=live_database),
    )
    monkeypatch.setattr(admin, "_repository", lambda _: FakeRepository())
    monkeypatch.setattr(sys, "argv", ["qa-admin", "backup", str(destination)])

    with pytest.raises(SystemExit):
        admin.main()
    assert destination.read_bytes() in {b"live", b"preserve"}
