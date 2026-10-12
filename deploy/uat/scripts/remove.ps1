param(
    [Parameter(Mandatory = $true)][string]$Unit,
    [Parameter(Mandatory = $true)][string]$ConfirmUnit
)
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Unit -cne $ConfirmUnit) { throw 'ConfirmUnit must exactly match Unit.' }
$ctx = Get-UatContext $Unit
$ids = @(& docker compose --project-name $ctx.Project --env-file $ctx.EnvFile --file $ctx.Compose ps --all --quiet)
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the exact Compose project; refusing removal.' }
foreach ($id in $ids) {
    if (-not $id) { continue }
    $owner = (& docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' $id).Trim()
    if ($LASTEXITCODE -ne 0 -or $owner -cne $ctx.Project) { throw 'A container did not carry the exact UAT project label; refusing removal.' }
}
Invoke-UatCompose $ctx @('down', '--volumes', '--remove-orphans')
foreach ($path in @($ctx.EnvFile, (Join-Path $ctx.Local 'ACCOUNTS.txt'))) {
    if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
}
Write-Output "Removed only UAT unit $($ctx.Project), its project-labeled resources, and its protected synthetic credentials. Any local backup directories were preserved."
