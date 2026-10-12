param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
Invoke-UatCompose $ctx @('stop', 'worker', 'api', 'egress-guard', 'redis', 'postgres')
Write-Output 'UAT services stopped gracefully. Named volumes and synthetic data are preserved.'
