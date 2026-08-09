[CmdletBinding()]
param(
    [string]$ContainerName = 'mirae-human-qa-chatbot-qa-lan-edge-1',
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$parent = Split-Path -Parent $resolvedOutput
if ($parent) {
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
}

docker cp "${ContainerName}:/data/caddy/pki/authorities/local/root.crt" $resolvedOutput
if ($LASTEXITCODE -ne 0) {
    throw "Could not export the Caddy internal CA from $ContainerName"
}

$certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($resolvedOutput)
Write-Output "Exported Caddy internal CA: $resolvedOutput"
Write-Output "SHA-256 fingerprint: $($certificate.GetCertHashString([System.Security.Cryptography.HashAlgorithmName]::SHA256))"
Write-Output 'Verify this fingerprint out-of-band before trusting the CA on a team device.'
Write-Output 'The CA is stored on tmpfs. Re-export and re-trust it whenever the LAN edge container is recreated.'
