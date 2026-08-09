[CmdletBinding()]
param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot '..\..'),

    [Parameter(Mandatory = $true)]
    [string]$EngineImage,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-f0-9]{40}$')]
    [string]$ExpectedEngineGitSha,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^sha256:[a-f0-9]{64}$')]
    [string]$ExpectedEngineImageDigest,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^sha256:[a-f0-9]{64}$')]
    [string]$ExpectedDataHash,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^HCX-[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$ExpectedHcxModelId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https://[^?#]+$')]
    [string]$ExpectedHcxBaseUrl,

    [Parameter(Mandatory = $true)]
    [string]$ReleaseGatePath,

    [Parameter(Mandatory = $true)]
    [string]$HcxApiKeyFile,

    [Parameter(Mandatory = $true)]
    [string]$EngineClarificationSigningKeyFile,

    [Parameter(Mandatory = $true)]
    [string]$QaTranscriptKeyFile,

    [Parameter(Mandatory = $true)]
    [string]$QaAuthSecretFile,

    [string]$RunningEngineContainer
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'QaSecurity.ps1')

function Invoke-QaCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    $output = @(& $Executable @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit $LASTEXITCODE)"
    }
    return ($output -join "`n").Trim()
}

$resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$resolvedGate = (Resolve-Path -LiteralPath $ReleaseGatePath).Path
$releaseValidator = (Resolve-Path -LiteralPath (
    Join-Path $resolvedRoot 'deploy\qa\release_evidence.py'
)).Path

$repositoryPrefix = $resolvedRoot + [System.IO.Path]::DirectorySeparatorChar
foreach ($protectedInput in @(
    $resolvedGate,
    $HcxApiKeyFile,
    $EngineClarificationSigningKeyFile,
    $QaTranscriptKeyFile,
    $QaAuthSecretFile
)) {
    $resolvedInput = (Resolve-Path -LiteralPath $protectedInput).Path
    if (
        $resolvedInput.Equals($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $resolvedInput.StartsWith(
            $repositoryPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Release evidence and secrets must remain outside the repository: $resolvedInput"
    }
}

if ($ExpectedEngineGitSha -eq ('0' * 40)) {
    throw 'ExpectedEngineGitSha cannot be an all-zero placeholder.'
}
if ($ExpectedEngineImageDigest -eq ('sha256:' + ('0' * 64))) {
    throw 'ExpectedEngineImageDigest cannot be an all-zero placeholder.'
}
if ($ExpectedDataHash -eq ('sha256:' + ('0' * 64))) {
    throw 'ExpectedDataHash cannot be an all-zero placeholder.'
}

$head = Invoke-QaCommand -Executable 'git' -Arguments @(
    '-C', $resolvedRoot, 'rev-parse', 'HEAD'
) -FailureMessage 'Could not read the repository HEAD'
if ($head -ne $ExpectedEngineGitSha) {
    throw "Repository HEAD does not match ExpectedEngineGitSha: $head"
}
$worktreeStatus = Invoke-QaCommand -Executable 'git' -Arguments @(
    '-C', $resolvedRoot, 'status', '--porcelain=v1', '--untracked-files=all'
) -FailureMessage 'Could not inspect the repository worktree'
if ($worktreeStatus) {
    throw 'Repository worktree is not clean; build and release evidence are not immutable.'
}

$imageInspectJson = Invoke-QaCommand -Executable 'docker' -Arguments @(
    'image', 'inspect', $EngineImage
) -FailureMessage 'Could not inspect the engine image'
$imageInspect = @($imageInspectJson | ConvertFrom-Json)[0]
$imageId = [string]$imageInspect.Id
if ($imageId -ne $ExpectedEngineImageDigest) {
    throw "Engine image ID mismatch: $imageId"
}
$revision = [string]$imageInspect.Config.Labels.'org.opencontainers.image.revision'
if ($revision -ne $ExpectedEngineGitSha) {
    throw "Engine image revision label mismatch: $revision"
}

$hashCode = "import hashlib; from pathlib import Path; p=Path('/app/data/serving/mirae_agent.duckdb'); print('sha256:'+hashlib.sha256(p.read_bytes()).hexdigest())"
$actualDataHash = Invoke-QaCommand -Executable 'docker' -Arguments @(
    'run', '--rm', '--network', 'none', '--read-only',
    '--entrypoint', 'python', $EngineImage, '-c', $hashCode
) -FailureMessage 'Could not hash the serving database inside the engine image'
if ($actualDataHash -ne $ExpectedDataHash) {
    throw "Serving database hash mismatch: $actualDataHash"
}

$validatorMount = "type=bind,source=$releaseValidator,target=/audit/release_evidence.py,readonly"
$gateMount = "type=bind,source=$resolvedGate,target=/audit/qa_release_gate.json,readonly"
[void](Invoke-QaCommand -Executable 'docker' -Arguments @(
    'run', '--rm', '--network', 'none', '--read-only',
    '--entrypoint', 'python',
    '--mount', $validatorMount,
    '--mount', $gateMount,
    $EngineImage,
    '/audit/release_evidence.py', 'validate', '/audit/qa_release_gate.json',
    '--expect-engine-git-sha', $ExpectedEngineGitSha,
    '--expect-engine-image-digest', $ExpectedEngineImageDigest,
    '--expect-data-hash', $ExpectedDataHash,
    '--expect-hcx-model-id', $ExpectedHcxModelId,
    '--expect-hcx-base-url', $ExpectedHcxBaseUrl
) -FailureMessage 'The bound QA release evidence did not validate')

Assert-QaDistinctSecretFiles -LiteralPaths @(
    $HcxApiKeyFile,
    $EngineClarificationSigningKeyFile,
    $QaTranscriptKeyFile,
    $QaAuthSecretFile
)

if ($RunningEngineContainer) {
    $runningImageId = Invoke-QaCommand -Executable 'docker' -Arguments @(
        'inspect', '--format', '{{.Image}}', $RunningEngineContainer
    ) -FailureMessage 'Could not inspect the running engine container'
    if ($runningImageId -ne $ExpectedEngineImageDigest) {
        throw "Running engine container image mismatch: $runningImageId"
    }
}

Write-Output 'QA_RELEASE_DEPLOYMENT_PREFLIGHT=PASS'
Write-Output "engine_git_sha=$ExpectedEngineGitSha"
Write-Output "engine_image_digest=$ExpectedEngineImageDigest"
Write-Output "data_hash=$ExpectedDataHash"
Write-Output "hcx_model_id=$ExpectedHcxModelId"
if ($RunningEngineContainer) {
    Write-Output "running_engine_container=$RunningEngineContainer"
}
