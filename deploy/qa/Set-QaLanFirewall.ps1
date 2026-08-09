[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [ValidateSet('Audit', 'Install', 'Remove')]
    [string]$Action = 'Audit',
    [Parameter(Mandatory = $true)]
    [string]$BindAddress,
    [Parameter(Mandatory = $true)]
    [string]$RemotePrivateSubnet
)

$ErrorActionPreference = 'Stop'
$ruleName = 'Mirae Human QA HTTPS 8443 - private subnet only'
. (Join-Path $PSScriptRoot 'QaSecurity.ps1')

[void](Assert-QaPrivateIpv4 -Address $BindAddress)
Assert-QaPrivateCidr -Cidr $RemotePrivateSubnet

$assigned = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $BindAddress -ErrorAction SilentlyContinue
if (-not $assigned) {
    throw "BindAddress is not assigned to this Windows host: $BindAddress"
}

$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($Action -eq 'Audit') {
    if (-not $existing) {
        Write-Output 'Firewall rule is absent.'
        exit 0
    }
    $existing | Get-NetFirewallAddressFilter
    $existing | Get-NetFirewallPortFilter
    exit 0
}

if ($Action -eq 'Install') {
    if ($existing) {
        throw 'The exact QA firewall rule already exists. Audit or remove it before reinstalling.'
    }
    if ($PSCmdlet.ShouldProcess("TCP $BindAddress`:8443 from $RemotePrivateSubnet", 'Create firewall allow rule')) {
        $ruleParameters = @{
            DisplayName = $ruleName
            Direction = 'Inbound'
            Action = 'Allow'
            Enabled = 'True'
            Profile = 'Private'
            Protocol = 'TCP'
            LocalAddress = $BindAddress
            LocalPort = 8443
            RemoteAddress = $RemotePrivateSubnet
        }
        New-NetFirewallRule @ruleParameters | Out-Null
        Write-Output "Created firewall rule: $ruleName"
    }
    exit 0
}

if (-not $existing) {
    Write-Output 'Firewall rule is already absent.'
    exit 0
}
if ($PSCmdlet.ShouldProcess($ruleName, 'Remove exact firewall rule')) {
    $existing | Remove-NetFirewallRule
    Write-Output "Removed firewall rule: $ruleName"
}
