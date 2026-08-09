[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$StateDirectory,

    [Parameter(Mandatory = $true)]
    [string]$SecretDirectory,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-f0-9]{40}$')]
    [string]$EngineGitSha,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^sha256:[a-f0-9]{64}$')]
    [string]$DataHash,

    [string]$EngineBaseUrl = 'http://127.0.0.1:8081',
    [string]$Origin = 'http://127.0.0.1:8090',
    [int]$Port = 8090
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '..\mirae-financial-agent\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "The configured development Python runtime was not found: $python"
}

$state = (Resolve-Path -LiteralPath $StateDirectory).Path
$secrets = (Resolve-Path -LiteralPath $SecretDirectory).Path
$gate = Join-Path $state 'qa_release_gate.json'
foreach ($required in @(
    $gate,
    (Join-Path $secrets 'qa_transcript.key'),
    (Join-Path $secrets 'qa_auth.key')
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Fixture preview input is missing: $required"
    }
}

# This launcher is excluded from Docker images and always advertises fixture mode.
# It exists only for local Browser/UI QA; the team-pilot Compose stack uses HCX.
$env:QA_DATABASE_PATH = Join-Path $state 'qa.sqlite3'
$env:QA_TRANSCRIPT_KEY_FILE = Join-Path $secrets 'qa_transcript.key'
$env:QA_AUTH_SECRET_FILE = Join-Path $secrets 'qa_auth.key'
$env:QA_ENGINE_BASE_URL = $EngineBaseUrl
$env:QA_ALLOWED_ORIGINS = $Origin
$env:QA_COOKIE_SECURE = 'false'
$env:QA_REQUIRE_ORIGIN = 'true'
$env:PILOT_CHAT_ENABLED = 'true'
$env:QA_ALLOW_FIXTURE_PREVIEW = 'true'
$env:QA_RELEASE_GATE_FILE = $gate
$env:HCX_MODEL_ID = 'HCX-FIXTURE-NO-LIVE'
$env:APPROVED_HCX_MODEL_ID = 'HCX-FIXTURE-NO-LIVE'
$env:HCX_BASE_URL = 'https://clovastudio.stream.ntruss.com'
$env:APPROVED_HCX_BASE_URL = 'https://clovastudio.stream.ntruss.com'
$env:ENGINE_GIT_SHA = $EngineGitSha
$env:ENGINE_IMAGE_DIGEST = 'sha256:' + ('f' * 64)
$env:DATA_HASH = $DataHash
$env:PLANNER_STAGE = 'two_stage'
$env:VECTOR_STATUS = 'DISABLED_FIXTURE_NO_LIVE_CACHE'

& $python -m uvicorn qa_chat.main:app `
    --host 127.0.0.1 `
    --port $Port `
    --workers 1 `
    --no-access-log
exit $LASTEXITCODE
