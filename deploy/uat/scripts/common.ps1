Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-UatContext([string]$Unit) {
    if ($Unit -notmatch '^[a-z0-9][a-z0-9-]{0,23}$') {
        throw 'Unit must be 1-24 lowercase letters, numbers, or hyphens.'
    }
    $root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
    $local = Join-Path $env:LOCALAPPDATA "VfxFilesPluginUAT\$Unit"
    $envFile = Join-Path $local 'uat.env'
    $project = "vfx-files-uat-$Unit"
    return [pscustomobject]@{ Root = $root; Local = $local; EnvFile = $envFile; Project = $project; Compose = (Join-Path $PSScriptRoot '..\compose.yaml') }
}

function Invoke-UatCompose($Context, [string[]]$Arguments) {
    if (-not (Test-Path -LiteralPath $Context.EnvFile -PathType Leaf)) { throw 'UAT unit is not initialized; run init.ps1 first.' }
    & docker compose --project-name $Context.Project --env-file $Context.EnvFile --file $Context.Compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed with exit code $LASTEXITCODE." }
}

function Read-UatEnv($Context) {
    $map = @{}
    foreach ($line in Get-Content -LiteralPath $Context.EnvFile) {
        if ($line -match '^([A-Z0-9_]+)=(.*)$') { $map[$matches[1]] = $matches[2] }
    }
    return $map
}

function Wait-UatServicesHealthy($Context, [string[]]$Services, [int]$TimeoutSeconds = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $ids = @(& docker compose --project-name $Context.Project --env-file $Context.EnvFile --file $Context.Compose ps --quiet @Services)
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect UAT service readiness.' }
        $healthy = $ids.Count -eq $Services.Count
        foreach ($id in $ids) {
            if (-not $id) { $healthy = $false; continue }
            $state = (& docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $id).Trim()
            if ($LASTEXITCODE -ne 0 -or $state -cne 'running|healthy') { $healthy = $false }
        }
        if ($healthy) { return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "UAT service readiness timed out: $($Services -join ', ')."
}

function Set-PrivateAcl([string]$Path, [switch]$Directory) {
    $principal = "$($env:USERDOMAIN)\$($env:USERNAME)"
    if ($Directory) {
        & icacls $Path /inheritance:r /grant:r "$($principal):(OI)(CI)F" 'NT AUTHORITY\SYSTEM:(OI)(CI)F' | Out-Null
    } else {
        & icacls $Path /inheritance:r /grant:r "$($principal):F" 'NT AUTHORITY\SYSTEM:F' | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not apply the private Windows ACL to UAT state.' }
}

function New-RandomHex([int]$Bytes = 32) {
    $buffer = [byte[]]::new($Bytes)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    return [Convert]::ToHexString($buffer).ToLowerInvariant()
}

function ConvertTo-Ipv4Range([string]$Cidr) {
    $parts = $Cidr.Split('/')
    if ($parts.Count -ne 2) { return $null }
    $address = [Net.IPAddress]::Parse($parts[0])
    if ($address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { return $null }
    $prefix = [int]$parts[1]
    if ($prefix -lt 0 -or $prefix -gt 32) { return $null }
    $bytes = $address.GetAddressBytes()
    [Array]::Reverse($bytes)
    $value = [BitConverter]::ToUInt32($bytes, 0)
    $block = [UInt64][Math]::Pow(2, 32 - $prefix)
    $start = [UInt64][Math]::Floor([double]$value / $block) * $block
    return @($start, ($start + $block - 1))
}

function Get-UatSubnets([string]$Project) {
    $occupied = @()
    $hostRoutes = @(Get-NetRoute -AddressFamily IPv4 -ErrorAction Stop | Where-Object { $_.DestinationPrefix -ne '0.0.0.0/0' })
    foreach ($route in $hostRoutes) {
        $range = ConvertTo-Ipv4Range $route.DestinationPrefix
        if ($range) { $occupied += ,$range }
    }
    $ids = @(& docker network ls --quiet)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Docker network allocations.' }
    foreach ($id in $ids) {
        $cidrs = @(& docker network inspect --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' $id)
        if ($LASTEXITCODE -ne 0) { throw 'Could not read an existing Docker network allocation.' }
        foreach ($cidr in $cidrs) {
            $range = ConvertTo-Ipv4Range $cidr
            if ($range) { $occupied += ,$range }
        }
    }

    $allocated = @()
    for ($slot = 0; $slot -lt 2; $slot++) {
        $chosen = $null
        for ($attempt = 0; $attempt -lt 4096 -and -not $chosen; $attempt++) {
            $octet = Get-Random -Minimum 24 -Maximum 32
            $third = Get-Random -Minimum 0 -Maximum 256
            $subnet = "172.$octet.$third.0/24"
            $range = ConvertTo-Ipv4Range $subnet
            $overlap = $false
            foreach ($other in @($occupied) + @($allocated)) {
                if ($range[0] -le $other[1] -and $other[0] -le $range[1]) { $overlap = $true; break }
            }
            if (-not $overlap) { $chosen = [pscustomobject]@{ Cidr = $subnet; Range = $range } }
        }
        if (-not $chosen) { throw 'Could not allocate two non-overlapping private Docker subnets for this UAT unit.' }
        $allocated += ,$chosen.Range
        if ($slot -eq 0) { $backend = $chosen.Cidr } else { $ingress = $chosen.Cidr }
    }
    return [pscustomobject]@{ Backend = $backend; Ingress = $ingress }
}
