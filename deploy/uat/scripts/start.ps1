param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
Invoke-UatCompose $ctx @('up', '-d', 'postgres', 'redis', 'egress-guard')
Wait-UatServicesHealthy -Context $ctx -Services @('postgres', 'redis', 'egress-guard')
Invoke-UatCompose $ctx @('up', '-d', 'api')
Wait-UatServicesHealthy -Context $ctx -Services @('api')
$envMap = Read-UatEnv $ctx
if ($envMap['VFX_FILES_WORKER_ENABLED'] -ceq 'true') {
    & (Join-Path $PSScriptRoot 'start-worker.ps1') -Unit $Unit
}
Write-Output 'UAT services are ready. The Worker starts only when its persisted feature flag is enabled and restore validation permits it.'
