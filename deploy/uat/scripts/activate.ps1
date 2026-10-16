param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
$envMap = Read-UatEnv $ctx
$envMap['VFX_FILES_ENABLED'] = 'true'
$envMap['VFX_FILES_WORKER_ENABLED'] = 'true'
$keys = @($envMap.Keys | Sort-Object)
$lines = foreach ($key in $keys) { "$key=$($envMap[$key])" }
[IO.File]::WriteAllLines($ctx.EnvFile, $lines, [Text.UTF8Encoding]::new($false))
Set-PrivateAcl -Path $ctx.EnvFile
Invoke-UatCompose $ctx @('up', '-d', '--force-recreate', 'api')
Wait-UatServicesHealthy -Context $ctx -Services @('api')
Write-Output 'Files API and durable Worker feature flags are enabled for this synthetic UAT unit. This command does not start a Worker; start-worker.ps1 or an explicit one-shot runner remains separate.'
