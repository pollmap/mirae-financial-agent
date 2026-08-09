[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EngineImage
)

$ErrorActionPreference = 'Stop'
$imageInspectJson = @(
    docker image inspect $EngineImage 2>&1
)
if ($LASTEXITCODE -ne 0) {
    throw 'Could not inspect the engine image.'
}
$imageInspect = @(($imageInspectJson -join "`n") | ConvertFrom-Json)[0]
$imageId = [string]$imageInspect.Id
$revision = [string]$imageInspect.Config.Labels.'org.opencontainers.image.revision'
$hashCode = "import hashlib; from pathlib import Path; p=Path('/app/data/serving/mirae_agent.duckdb'); print('sha256:'+hashlib.sha256(p.read_bytes()).hexdigest())"
$dataHash = @(
    docker run --rm --network none --read-only --entrypoint python `
        $EngineImage -c $hashCode 2>&1
)
if ($LASTEXITCODE -ne 0) {
    throw 'Could not hash the serving database inside the engine image.'
}

[ordered]@{
    engine_image = $EngineImage
    engine_image_digest = $imageId.Trim()
    engine_git_sha = $revision.Trim()
    data_hash = ($dataHash -join "`n").Trim()
} | ConvertTo-Json -Compress
