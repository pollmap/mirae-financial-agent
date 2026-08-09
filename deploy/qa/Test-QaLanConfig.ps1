[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BindAddress,
    [Parameter(Mandatory = $true)]
    [string]$AllowedOrigin,
    [Parameter(Mandatory = $true)]
    [bool]$CookieSecure
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'QaSecurity.ps1')
[void](Assert-QaPrivateIpv4 -Address $BindAddress)

$assigned = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $BindAddress -ErrorAction SilentlyContinue
if (-not $assigned) {
    throw "QA_LAN_BIND_IP is not assigned to this Windows host: $BindAddress"
}

$expectedOrigin = "https://${BindAddress}:8443"
if ($AllowedOrigin.TrimEnd('/') -ne $expectedOrigin) {
    throw "QA_ALLOWED_ORIGINS must be exactly $expectedOrigin for the LAN profile"
}
if (-not $CookieSecure) {
    throw 'QA_COOKIE_SECURE must be true for the LAN profile'
}

Write-Output "LAN binding verified: $BindAddress"
Write-Output "Allowed origin verified: $expectedOrigin"
Write-Output 'This check does not change Windows Firewall or router settings.'
