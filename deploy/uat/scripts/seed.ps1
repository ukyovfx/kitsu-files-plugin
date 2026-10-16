param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
$envMap = Read-UatEnv $ctx
$output = & docker compose --project-name $ctx.Project --env-file $ctx.EnvFile --file $ctx.Compose exec --no-TTY api /opt/zou/env/bin/python /opt/vfx-files-uat/seed_installed_page_flow.py 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Synthetic UAT seeder failed. Its output was suppressed; inspect container logs only after redaction.' }
try { $seed = ($output -join "`n") | ConvertFrom-Json } catch { throw 'Seeder returned an invalid result.' }
if (-not $seed.production_id -or -not $seed.task_id -or -not $seed.login_emails) { throw 'Seeder readback is incomplete.' }
$accountLines = @(
    "Production: $($seed.production_name)"
    "Production ID: $($seed.production_id)"
    "Task ID: $($seed.task_id)"
    "Recipient Task ID: $($seed.recipient_task_id)"
    "Receiving Department ID: $($seed.recipient_department_id)"
    "Receiving Department: $($seed.recipient_department_name)"
    "Artist A ID: $($seed.artist_a_id)"
    "Artist C ID: $($seed.artist_c_id)"
    "Manager: $($seed.login_emails.manager)"
    "Artist A: $($seed.login_emails.artist_a)"
    "Artist B: $($seed.login_emails.artist_b)"
    "Artist C: $($seed.login_emails.artist_c)"
    "Supervisor: $($seed.login_emails.supervisor)"
    "Synthetic account password: $($envMap['VFX_FILES_UAT_TEST_PASSWORD'])"
    'All accounts and Production/Task records are synthetic and belong only to this local unit.'
)
$accounts = Join-Path $ctx.Local 'ACCOUNTS.txt'
if (Test-Path -LiteralPath $accounts) { throw 'Refusing to overwrite an existing UAT account record.' }
[IO.File]::WriteAllLines($accounts, $accountLines, [Text.UTF8Encoding]::new($false))
Set-PrivateAcl -Path $accounts
Write-Output "Synthetic Production and accounts are ready. Credentials were saved to the ACL-protected file: $accounts"
