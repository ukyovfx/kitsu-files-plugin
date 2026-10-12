param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
$envMap = Read-UatEnv $ctx
$envMap['VFX_FILES_ENABLED'] = 'true'
$keys = @($envMap.Keys | Sort-Object)
$lines = foreach ($key in $keys) { "$key=$($envMap[$key])" }
[IO.File]::WriteAllLines($ctx.EnvFile, $lines, [Text.UTF8Encoding]::new($false))
Set-PrivateAcl -Path $ctx.EnvFile
Invoke-UatCompose $ctx @('up', '-d', '--force-recreate', 'api')
Wait-UatServicesHealthy -Context $ctx -Services @('api')
Write-Output 'Files API feature flag enabled for this synthetic UAT unit. Worker remains disabled.'
