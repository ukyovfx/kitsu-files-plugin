param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
Invoke-UatCompose $ctx @('up', '-d', 'postgres', 'redis', 'egress-guard')
Wait-UatServicesHealthy -Context $ctx -Services @('postgres', 'redis', 'egress-guard')
Invoke-UatCompose $ctx @('run', '--rm', '--no-deps', 'api', '/opt/zou/env/bin/zou', 'init-db')
Invoke-UatCompose $ctx @('run', '--rm', '--no-deps', 'api', '/opt/zou/env/bin/zou', 'init-data', '--domain', 'vfx')
Invoke-UatCompose $ctx @('run', '--rm', '--no-deps', '--entrypoint', '/bin/sh', 'api', '-ec', '/opt/zou/env/bin/zou create-admin admin@example.com --password "$VFX_FILES_E2E_TEST_PASSWORD"')
Invoke-UatCompose $ctx @('up', '-d', 'api')
Write-Output 'The Kitsu API is running on loopback. The UAT-only plugin install command is documented in deploy/uat/README.md.'
