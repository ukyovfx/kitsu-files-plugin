# Isolated Kitsu Files UAT unit

This is a **local synthetic UAT deployment package**, not a Production installer. It builds the pinned, unofficial patched Kitsu 1.0.70 / Zou 1.0.94 candidate and layers the exact Git `plugin/` tree into one image shared by the API and separate Worker. Runtime containers have no external route and use FakeStorage only. Building may download pinned base/database/Redis images and hash-locked candidate dependencies from their public registries.

## What the package isolates

- A unique Compose project per unit; all project containers and named volumes have UAT labels.
- PostgreSQL 14 (the major version embedded in the pinned host candidate) and Redis 7.4 are separate services with pinned image digests. They have no host-published ports.
- API and the separately supervised `--poll` Worker use the same locally built image, stamped with the source commit. Worker starts only after explicit `start-worker.ps1`.
- FakeStorage, staging, PostgreSQL, Redis, Kitsu preview and restore-guard state live in separate project-scoped named volumes. The unused in-image PostgreSQL data mount is explicitly mapped read-only so Docker cannot create anonymous volumes outside the UAT removal boundary.
- PostgreSQL, Redis and Worker only join the internal backend network. A minimal egress-guard container shares the API network namespace, binds the UI port to `127.0.0.1`, and drops new IPv4/IPv6 outbound traffic except to that unit's unique backend subnet. The guard alone receives `NET_ADMIN`; API receives only `NET_BIND_SERVICE`, `CHOWN`, `SETUID` and `SETGID` for the pinned host's Nginx startup/drop-privilege behavior, and Worker drops all capabilities. Runtime acceptance probes both the egress rule and loopback UI before the unit is usable.
- API and Worker feature flags default off. FakeStorage is the only storage mode. Test users are synthetic and use `.example.invalid` email addresses.
- Per-container CPU, memory and process ceilings are set in Compose. Docker Engine/cgroups must enforce them; inspect the created containers before claiming host enforcement. Named-volume disk quotas are **not** enforced by Compose. For a server UAT, provide a dedicated quota-enforced filesystem or stop here; Docker Desktop's WSL disk cap is a host-wide setting and is not set by this package.

The candidate image is unofficial and its inherited host dependencies do not clear the official host security gate. It is not Production-approved. The current development preview container is intentionally not reused or changed because it is attached to its existing network and volumes.

## Windows PowerShell quick start

Run from a clean, committed source worktree. `init.ps1` and `build.ps1` reject dirty or untracked source so the image revision label cannot claim a different source tree. Pick a new short unit name each time. Secrets are randomly generated into an ACL-protected file under `%LOCALAPPDATA%\VfxFilesPluginUAT\<unit>`; commands do not print its contents.

```powershell
.\deploy\uat\scripts\init.ps1 -Unit demo
.\deploy\uat\scripts\build.ps1 -Unit demo
.\deploy\uat\scripts\init-host.ps1 -Unit demo
```

`init-host.ps1` initializes the separate PostgreSQL database, loads Zou's minimal VFX domain data, creates a synthetic admin using the locally protected test password without placing it in process arguments or output, and starts only the API. Install the plugin through the pinned host's supported lifecycle:

```powershell
.\deploy\uat\scripts\install-plugin.ps1 -Unit demo
```

Then create the synthetic Manager / Artist A / Artist B / Supervisor, Production, Sequence, Shot, Task and FakeStorage bindings. Enable the Files feature, log in as the Manager, and complete the template and permission configuration in the Files page before starting the Worker:

```powershell
.\deploy\uat\scripts\seed.ps1 -Unit demo
.\deploy\uat\scripts\activate.ps1 -Unit demo
# Configure and review the template through the Kitsu Files page first.
.\deploy\uat\scripts\start-worker.ps1 -Unit demo
```

The seeded account names and shared synthetic test password are stored in the private local `ACCOUNTS.txt`. Open `http://127.0.0.1:55280` (or the selected port) and use the ordinary Kitsu login, then Open Production → Assets → VFX Files. This package does not run browser/GUI automation. Reuse the exact PR #2 installed-page Playwright evidence only for the source revision it covered; run human UAT before any server trial.

For readiness and a smoke status without revealing settings:

```powershell
docker compose --project-name vfx-files-uat-demo --env-file "$env:LOCALAPPDATA\VfxFilesPluginUAT\demo\uat.env" --file .\deploy\uat\compose.yaml ps
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:55280/api/data -Method Get
```

Stop while preserving synthetic data with `scripts/stop.ps1 -Unit demo`. A reboot can use the same project with `docker compose ... up -d api` and the `worker` profile only after checking its configuration.

## Backup and restore

`scripts/backup.ps1 -Unit demo` stops API and Worker before taking a PostgreSQL custom-format dump and a FakeStorage/staging tar. It records SHA-256 hashes, source commit and image ID but excludes `.env`, passwords, JWTs, cookies and other credentials. The backup directory is kept under the private `%LOCALAPPDATA%` unit directory.

Restore only into a **new** UAT unit initialized with `init.ps1` and using the exact API/Worker image ID captured in the snapshot. Reuse/import that image by digest; a separate rebuild may produce a different digest and is refused. Do not run `init-host.ps1` in the target. `scripts/restore.ps1 -Unit restore-demo -SnapshotPath <backup-directory>` starts only the target PostgreSQL/Redis/egress guard services, waits for their health, verifies the manifest, source and image identities plus every payload hash, restores into fresh database and FakeStorage/staging volumes, then waits for the API health check. It intentionally does not start a Worker.

Before unlocking the restore-gated Worker, independently compare the source and restored sanitized restore-state reports, Latest Working/Published/Approved pointers, manifests and every object byte/checksum; verify the installed page and authorization. Only then use `scripts/confirm-restore.ps1 -Unit restore-demo -ManifestSha256 <snapshot-manifest-sha256> -IntegrityReviewed`. The marker is an operator assertion, not a cryptographic proof. If any value differs, leave the Worker stopped and discard only that new disposable UAT unit.

## Removal

`scripts/remove.ps1 -Unit demo -ConfirmUnit demo` checks the exact Compose project label on every container, then removes only that project's containers, labeled network/volumes, and protected synthetic credentials. Never use global `docker system prune`, `docker volume prune`, or an unscoped Compose project name for UAT cleanup.

## Resource and security limits

Compose's `cpus`, `mem_limit`, and `pids_limit` are per-container limits enforced when the Docker Linux engine supports cgroups. Record `docker inspect` values for every service; Windows Docker Desktop still applies its own WSL2 VM-wide CPU/RAM ceilings. Compose named volumes have no portable maximum-size option, and these files do not assert a disk cap. Server UAT is blocked until a dedicated, quota-enforced data filesystem and its monitoring/restore capacity are supplied.

This deployment template is intended for local review. Any VFX Studio Server UAT still needs a separately authorized, compatible isolated server instance, owner-approved synthetic identity policy, verified backups/restore, disk quota enforcement, patched-host approval, and a scoped change window. No Production or Staging installation is included.
