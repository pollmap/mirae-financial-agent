[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'QaSecurity.ps1')

$resolvedParent = [System.IO.Path]::GetFullPath($OutputDirectory).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$repositoryRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..')
).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$insideRepository = (
    $resolvedParent.Equals($repositoryRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $resolvedParent.StartsWith(
        $repositoryRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
)
if ($insideRepository) {
    throw 'Secret output directory must be outside the Git repository.'
}
if (Test-Path -LiteralPath $resolvedParent) {
    $existingItems = @(Get-ChildItem -LiteralPath $resolvedParent -Force)
    if ($existingItems.Count -ne 0) {
        throw 'Refusing to change ACLs on a non-empty secret directory.'
    }
}
else {
    [System.IO.Directory]::CreateDirectory($resolvedParent) | Out-Null
}
Set-QaRestrictedAcl -LiteralPath $resolvedParent -Directory
Assert-QaRestrictedAcl -LiteralPath $resolvedParent
if (@(Get-ChildItem -LiteralPath $resolvedParent -Force).Count -ne 0) {
    throw 'Secret directory changed while it was being secured; refusing to continue.'
}
$secretNames = @('engine_clarification.key', 'qa_transcript.key', 'qa_auth.key')
foreach ($secretName in $secretNames) {
    $candidate = Join-Path $resolvedParent $secretName
    if (Test-Path -LiteralPath $candidate) {
        throw "Refusing to overwrite an existing secret: $candidate"
    }
}

function New-RandomSecretFile {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$ByteCount,
        [switch]$Base64Prefix
    )

    $bytes = [byte[]]::new($ByteCount)
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    $value = [Convert]::ToBase64String($bytes)
    if ($Base64Prefix) {
        $value = "base64:$value"
    }
    $path = Join-Path $resolvedParent $Name
    [System.IO.File]::WriteAllText($path, $value, [System.Text.UTF8Encoding]::new($false))
    try {
        Set-QaRestrictedAcl -LiteralPath $path
        Assert-QaRestrictedAcl -LiteralPath $path
    }
    catch {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        throw
    }
    return $path
}

$created = @(
    New-RandomSecretFile -Name 'engine_clarification.key' -ByteCount 48
    New-RandomSecretFile -Name 'qa_transcript.key' -ByteCount 32 -Base64Prefix
    New-RandomSecretFile -Name 'qa_auth.key' -ByteCount 48 -Base64Prefix
)
$createdHashes = @($created | ForEach-Object {
    (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash
} | Sort-Object -Unique)
if ($createdHashes.Count -ne $created.Count) {
    $created | Remove-Item -Force -ErrorAction SilentlyContinue
    throw 'Generated secrets were not distinct.'
}

Write-Output 'Generated non-provider secrets:'
$created | ForEach-Object { Write-Output "  $_" }
Write-Output 'HCX API credentials were not generated. Store the real key in a separate file.'
Write-Output 'Keep this directory outside the repository and restrict it to the current Windows user.'
