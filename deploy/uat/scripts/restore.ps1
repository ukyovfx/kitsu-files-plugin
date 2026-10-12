param(
    [Parameter(Mandatory = $true)][string]$Unit,
    [Parameter(Mandatory = $true)][string]$SnapshotPath
)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
$envMap = Read-UatEnv $ctx
$snapshot = (Resolve-Path -LiteralPath $SnapshotPath -ErrorAction Stop).Path
$restoreOut = Join-Path $ctx.Local 'restore-validation'
if (Test-Path -LiteralPath $restoreOut) { throw 'Restore validation directory already exists; use a fresh target unit.' }
$tool = Join-Path $ctx.Root 'deploy\uat\tools\snapshot.py'
& python $tool restore --root $ctx.Root --compose $ctx.Compose --env-file $ctx.EnvFile --project $ctx.Project --source-short $envMap['VFX_FILES_UAT_SOURCE_SHORT'] --snapshot $snapshot --output $restoreOut
if ($LASTEXITCODE -ne 0) { throw 'Restore failed closed. Worker remains stopped.' }
$envMap = Read-UatEnv $ctx
$envMap['VFX_FILES_ENABLED'] = 'true'
[IO.File]::WriteAllLines($ctx.EnvFile, @($envMap.Keys | Sort-Object | ForEach-Object { "$_=$($envMap[$_])" }), [Text.UTF8Encoding]::new($false))
Set-PrivateAcl -Path $ctx.EnvFile
Invoke-UatCompose $ctx @('up', '-d', '--force-recreate', 'api')
Wait-UatServicesHealthy -Context $ctx -Services @('api')
Write-Output 'Restore completed into a separate UAT unit. Worker remains stopped until state and artifact verification succeeds.'
