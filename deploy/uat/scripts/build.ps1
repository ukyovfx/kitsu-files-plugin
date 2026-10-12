param([Parameter(Mandatory = $true)][string]$Unit)
. (Join-Path $PSScriptRoot 'common.ps1')
$ctx = Get-UatContext $Unit
$envMap = Read-UatEnv $ctx
$fullSha = (& git -C $ctx.Root rev-parse HEAD).Trim()
$sha = $fullSha.Substring(0, 12)
if ($fullSha -ne $envMap['VFX_FILES_UAT_SOURCE_HEAD'] -or $sha -ne $envMap['VFX_FILES_UAT_SOURCE_SHORT']) { throw 'Source HEAD changed since init.ps1. Initialize a new UAT unit so source and image labels cannot drift.' }
$worktreeChanges = @(& git -C $ctx.Root status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $worktreeChanges.Count -gt 0) { throw 'Build UAT images only from a clean, committed source worktree.' }
$candidateDockerfile = Join-Path $ctx.Root 'environment\patched-candidate.Dockerfile'
$candidateContext = Join-Path $ctx.Root 'environment'
& docker build --file $candidateDockerfile --tag $envMap['VFX_FILES_UAT_CANDIDATE_IMAGE'] $candidateContext
if ($LASTEXITCODE -ne 0) { throw 'Pinned patched-candidate image build failed.' }
Invoke-UatCompose $ctx @('build', 'api', 'egress-guard')
$appTag = "vfx-files-uat-app:$sha"
$raw = & docker image inspect $appTag --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
if ($LASTEXITCODE -ne 0) { throw 'Built application image could not be inspected.' }
$parts = $raw -split ' '
if ($parts.Count -ne 2 -or $parts[0] -notmatch '^sha256:[0-9a-f]{64}$' -or $parts[1] -cne $fullSha) { throw 'Built application image source label/readback did not match the exact Git commit.' }
Write-Output "Built pinned isolated candidate and API/Worker image for source $sha. Image identity: $($raw.Split(' ')[0])."
