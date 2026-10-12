param(
    [Parameter(Mandatory = $true)][string]$Unit,
    [ValidateRange(1024, 65535)][int]$HttpPort = 55280
)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
if (Test-Path -LiteralPath $ctx.EnvFile) { throw 'Refusing to overwrite existing UAT credentials or state.' }
$existingContainers = @(& docker ps --all --quiet --filter "label=com.docker.compose.project=$($ctx.Project)")
$existingVolumes = @(& docker volume ls --quiet --filter "label=com.docker.compose.project=$($ctx.Project)")
if ($existingContainers.Count -or $existingVolumes.Count) { throw 'Refusing to reuse an existing Docker project; choose a fresh UAT unit name.' }
$worktreeChanges = @(& git -C $ctx.Root status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $worktreeChanges.Count -gt 0) { throw 'Initialize UAT only from a clean, committed source worktree.' }
New-Item -ItemType Directory -Path $ctx.Local -Force | Out-Null
Set-PrivateAcl -Path $ctx.Local -Directory
$fullSha = (& git -C $ctx.Root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $fullSha -notmatch '^[0-9a-f]{40}$') { throw 'Could not resolve the exact source commit.' }
$sha = $fullSha.Substring(0, 12)
$subnets = Get-UatSubnets $ctx.Project
$candidate = "vfx-files-host-candidate:uat-$sha"
$body = @(
    "VFX_FILES_UAT_PROJECT=$($ctx.Project)"
    "VFX_FILES_UAT_SOURCE_SHORT=$sha"
    "VFX_FILES_UAT_SOURCE_HEAD=$fullSha"
    "VFX_FILES_UAT_BACKEND_SUBNET=$($subnets.Backend)"
    "VFX_FILES_UAT_INGRESS_SUBNET=$($subnets.Ingress)"
    "VFX_FILES_UAT_CANDIDATE_IMAGE=$candidate"
    "VFX_FILES_UAT_DB_PASSWORD=$(New-RandomHex)"
    "VFX_FILES_UAT_REDIS_PASSWORD=$(New-RandomHex)"
    "VFX_FILES_UAT_SECRET_KEY=$(New-RandomHex)"
    "VFX_FILES_UAT_TEST_PASSWORD=$(New-RandomHex)"
    "VFX_FILES_UAT_HTTP_PORT=$HttpPort"
    'VFX_FILES_ENABLED=false'
    'VFX_FILES_WORKER_ENABLED=false'
    'VFX_FILES_UAT_RESTORE_EXPECTED_MANIFEST_SHA256='
)
[IO.File]::WriteAllLines($ctx.EnvFile, $body, [Text.UTF8Encoding]::new($false))
Set-PrivateAcl -Path $ctx.EnvFile
Write-Output "Created isolated unit $($ctx.Project). Credential file is private to the current Windows user; its contents were not displayed."
