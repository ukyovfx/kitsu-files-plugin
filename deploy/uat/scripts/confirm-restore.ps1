param(
    [Parameter(Mandatory = $true)][string]$Unit,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ManifestSha256,
    [Parameter(Mandatory = $true)][switch]$IntegrityReviewed
)
. (Join-Path $PSScriptRoot 'common.ps1')
if (-not $IntegrityReviewed) { throw 'Worker unlock requires independent record, pointer, package and artifact-hash verification.' }
$ctx = Get-UatContext $Unit
$manifestPath = Join-Path $ctx.Local 'restore-validation\restore-manifest.sha256'
if (-not (Test-Path -LiteralPath $manifestPath) -or (Get-Content -Raw $manifestPath).Trim() -cne $ManifestSha256) { throw 'Manifest digest does not match this unit restore record.' }
$envMap = Read-UatEnv $ctx
$envMap['VFX_FILES_UAT_RESTORE_EXPECTED_MANIFEST_SHA256'] = $ManifestSha256
$envMap['VFX_FILES_ENABLED'] = 'true'
$envMap['VFX_FILES_WORKER_ENABLED'] = 'true'
[IO.File]::WriteAllLines($ctx.EnvFile, @($envMap.Keys | Sort-Object | ForEach-Object { "$_=$($envMap[$_])" }), [Text.UTF8Encoding]::new($false))
Set-PrivateAcl -Path $ctx.EnvFile
$code = "from pathlib import Path; import json; Path('/var/lib/vfx-files/restore/verified.json').write_text(json.dumps({'integrity_verified': True, 'manifest_sha256': '$ManifestSha256'}), encoding='utf-8')"
Invoke-UatCompose $ctx @('--profile', 'worker', 'run', '--rm', '--no-deps', '--entrypoint', '/opt/zou/env/bin/python', 'worker', '-c', $code)
& (Join-Path $PSScriptRoot 'start-worker.ps1') -Unit $Unit
