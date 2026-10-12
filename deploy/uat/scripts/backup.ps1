param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
$output = Join-Path $ctx.Local ("backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$tool = Join-Path $ctx.Root 'deploy\uat\tools\snapshot.py'
& python $tool backup --root $ctx.Root --compose $ctx.Compose --env-file $ctx.EnvFile --project $ctx.Project --output $output
if ($LASTEXITCODE -ne 0) { throw 'Snapshot failed. The API and Worker remain stopped for consistency.' }
Set-PrivateAcl -Path $output -Directory
Write-Output "Consistent PostgreSQL/FakeStorage/staging snapshot: $output. API and Worker are stopped; secrets are excluded."
