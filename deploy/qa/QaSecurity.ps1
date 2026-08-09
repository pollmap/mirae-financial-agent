function Get-QaCurrentUserSid {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $identity.User) {
        throw 'Could not resolve the current Windows user SID.'
    }
    return $identity.User
}

function Get-QaAllowedSecretSids {
    $currentUser = Get-QaCurrentUserSid
    return @(
        $currentUser,
        [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
        [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    )
}

function Set-QaRestrictedAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,
        [switch]$Directory
    )

    $currentUser = Get-QaCurrentUserSid
    $security = if ($Directory) {
        [System.Security.AccessControl.DirectorySecurity]::new()
    }
    else {
        [System.Security.AccessControl.FileSecurity]::new()
    }
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($currentUser)

    $inheritance = if ($Directory) {
        [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    }
    else {
        [System.Security.AccessControl.InheritanceFlags]::None
    }
    foreach ($sid in Get-QaAllowedSecretSids) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void]$security.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $LiteralPath -AclObject $security
}

function Assert-QaRestrictedAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    $acl = Get-Acl -LiteralPath $LiteralPath
    if (-not $acl.AreAccessRulesProtected) {
        throw "Secret ACL still inherits permissions: $LiteralPath"
    }
    $allowed = @{}
    foreach ($sid in Get-QaAllowedSecretSids) {
        $allowed[$sid.Value] = $true
    }
    $currentUserSid = (Get-QaCurrentUserSid).Value
    $currentUserCanRead = $false
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
            continue
        }
        try {
            $sid = $rule.IdentityReference.Translate(
                [System.Security.Principal.SecurityIdentifier]
            ).Value
        }
        catch {
            throw "Secret ACL contains an unresolvable identity: $LiteralPath"
        }
        if (-not $allowed.ContainsKey($sid)) {
            throw "Secret ACL grants access outside the approved identities: $LiteralPath"
        }
        if ($sid -eq $currentUserSid -and (
            $rule.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::Read
        )) {
            $currentUserCanRead = $true
        }
    }
    if (-not $currentUserCanRead) {
        throw "Current Windows user cannot read the protected secret: $LiteralPath"
    }
}

function Assert-QaDistinctSecretFiles {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$LiteralPaths
    )

    if ($LiteralPaths.Count -ne 4) {
        throw 'Exactly four secret files are required: HCX, engine signing, transcript, and auth.'
    }
    $resolved = @()
    $hashes = @{}
    foreach ($path in $LiteralPaths) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required secret file is missing: $path"
        }
        $item = Get-Item -LiteralPath $path
        if ($item.Length -lt 32) {
            throw "Required secret file is unexpectedly short: $path"
        }
        $fullPath = $item.FullName
        if ($resolved -contains $fullPath) {
            throw "Secret paths must be distinct: $fullPath"
        }
        $resolved += $fullPath
        Assert-QaRestrictedAcl -LiteralPath $fullPath
        $digest = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash
        if ($hashes.ContainsKey($digest)) {
            throw "Secret file contents must be distinct: $fullPath"
        }
        $hashes[$digest] = $true
    }
}

function ConvertTo-QaIpv4Number {
    param([Parameter(Mandatory = $true)][System.Net.IPAddress]$Address)
    $octets = $Address.GetAddressBytes()
    if ($octets.Length -ne 4) {
        throw "IPv6 is not supported by this QA LAN profile: $Address"
    }
    return (
        ([uint64]$octets[0] * 16777216) +
        ([uint64]$octets[1] * 65536) +
        ([uint64]$octets[2] * 256) +
        [uint64]$octets[3]
    )
}

function Assert-QaPrivateIpv4 {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Address)

    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$parsed)) {
        throw "Not a literal IP address: $Address"
    }
    if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
        $parsed.ToString() -cne $Address) {
        throw "Address must use canonical dotted-decimal IPv4 notation: $Address"
    }
    $value = ConvertTo-QaIpv4Number -Address $parsed
    $private = (
        ($value -ge 167772160 -and $value -le 184549375) -or
        ($value -ge 2886729728 -and $value -le 2887778303) -or
        ($value -ge 3232235520 -and $value -le 3232301055)
    )
    if (-not $private) {
        throw "Address is not RFC1918 private space: $Address"
    }
    return $parsed
}

function Assert-QaPrivateCidr {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Cidr)

    if ($Cidr -notmatch '^(?<address>[^/]+)/(?<prefix>0|[1-9]|[12][0-9]|3[0-2])$') {
        throw 'RemotePrivateSubnet must be a canonical IPv4 CIDR such as 192.168.1.0/24'
    }
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Matches.address, [ref]$parsed)) {
        throw "Not a literal IPv4 CIDR address: $Cidr"
    }
    if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
        $parsed.ToString() -cne $Matches.address) {
        throw "CIDR must use canonical dotted-decimal IPv4 notation: $Cidr"
    }
    $value = ConvertTo-QaIpv4Number -Address $parsed
    $prefix = [int]$Matches.prefix
    $blockSize = [uint64][Math]::Pow(2, 32 - $prefix)
    $networkStart = [uint64]([Math]::Floor($value / $blockSize) * $blockSize)
    $networkEnd = $networkStart + $blockSize - 1
    if ($value -ne $networkStart) {
        throw "RemotePrivateSubnet must use its canonical network address: $Cidr"
    }
    $contained = (
        ($networkStart -ge 167772160 -and $networkEnd -le 184549375) -or
        ($networkStart -ge 2886729728 -and $networkEnd -le 2887778303) -or
        ($networkStart -ge 3232235520 -and $networkEnd -le 3232301055)
    )
    if (-not $contained) {
        throw "The entire CIDR must remain inside one RFC1918 private range: $Cidr"
    }
}
