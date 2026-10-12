param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
$envMap = Read-UatEnv $ctx
if ((Test-Path -LiteralPath (Join-Path $ctx.Local 'restore-validation\restore-manifest.sha256')) -and -not $envMap['VFX_FILES_UAT_RESTORE_EXPECTED_MANIFEST_SHA256']) {
    throw 'This unit was restored. Complete confirm-restore.ps1 before starting any Worker.'
}
$envMap['VFX_FILES_ENABLED'] = 'true'
$envMap['VFX_FILES_WORKER_ENABLED'] = 'true'
[IO.File]::WriteAllLines($ctx.EnvFile, @($envMap.Keys | Sort-Object | ForEach-Object { "$_=$($envMap[$_])" }), [Text.UTF8Encoding]::new($false))
Set-PrivateAcl -Path $ctx.EnvFile
if ($envMap['VFX_FILES_UAT_RESTORE_EXPECTED_MANIFEST_SHA256']) {
    $override = Join-Path $PSScriptRoot '..\restore-guard.compose.yaml'
    $composeArgs = @('--project-name', $ctx.Project, '--env-file', $ctx.EnvFile, '--file', $ctx.Compose, '--file', $override, '--profile', 'worker', 'run', '--rm', '--no-deps', 'worker', '/opt/zou/env/bin/python', '-c', 'from vfx_files.worker_runtime import assert_restore_validation_complete; assert_restore_validation_complete()')
    & docker compose @composeArgs
    if ($LASTEXITCODE -ne 0) { throw 'Restore marker did not verify. Worker remains stopped.' }
    $composeArgs = @('--project-name', $ctx.Project, '--env-file', $ctx.EnvFile, '--file', $ctx.Compose, '--file', $override, '--profile', 'worker', 'up', '-d', 'worker')
    & docker compose @composeArgs
    if ($LASTEXITCODE -ne 0) { throw 'Restore-gated Worker failed to start.' }
} else {
    Invoke-UatCompose $ctx @('--profile', 'worker', 'up', '-d', 'worker')
}
Write-Output 'Separate supervised Worker started with the same immutable UAT image as the API and FakeStorage only.'
