param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
Invoke-UatCompose $ctx @('up', '-d', 'postgres', 'redis', 'egress-guard')
$deadline = (Get-Date).AddMinutes(3)
do {
    $ids = @(& docker compose --project-name $ctx.Project --env-file $ctx.EnvFile --file $ctx.Compose ps --quiet postgres redis)
    $healthy = $LASTEXITCODE -eq 0 -and $ids.Count -eq 2
    foreach ($id in $ids) {
        if (-not $id) { $healthy = $false; continue }
        $health = (& docker inspect --format '{{.State.Health.Status}}' $id).Trim()
        if ($LASTEXITCODE -ne 0 -or $health -cne 'healthy') { $healthy = $false }
    }
    if ($healthy) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
if (-not $healthy) { throw 'Isolated PostgreSQL/Redis did not become healthy.' }
Invoke-UatCompose $ctx @('run', '--rm', '--no-deps', 'api', '/opt/zou/env/bin/zou', 'init-db')
Invoke-UatCompose $ctx @('run', '--rm', '--no-deps', 'api', '/opt/zou/env/bin/zou', 'init-data', '--domain', 'vfx')
Invoke-UatCompose $ctx @('run', '--rm', '--no-deps', '--entrypoint', '/bin/sh', 'api', '-ec', '/opt/zou/env/bin/zou create-admin admin@example.com --password "$VFX_FILES_E2E_TEST_PASSWORD"')
Invoke-UatCompose $ctx @('up', '-d', 'api')
Write-Output 'The Kitsu API is running on loopback. The UAT-only plugin install command is documented in deploy/uat/README.md.'
