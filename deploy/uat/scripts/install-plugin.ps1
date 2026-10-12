param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
Invoke-UatCompose $ctx @('run', '--rm', '--no-deps', 'api', '/opt/zou/env/bin/zou', 'install-plugin', '--path', '/opt/vfx-files-uat/plugin-source')
Invoke-UatCompose $ctx @('up', '-d', '--force-recreate', 'api')
Wait-UatServicesHealthy -Context $ctx -Services @('api')
Write-Output 'The exact bundled plugin was installed through Zou and the API was restarted to load the registered routes.'
