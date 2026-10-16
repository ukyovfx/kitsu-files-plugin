# Isolated Kitsu Files UAT unit

For an Ubuntu/server-owner handoff, read [`linux/README.md`](linux/README.md). It defines the separate-daemon, aggregate-cgroup, disk-quota, SSH-forwarding, and restore gates. The Ubuntu host lifecycle has not been validated; do not treat the runbook as Production or Server authorization.

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

Then create the synthetic Manager / Artist A / Artist B / Supervisor, Production, Sequence, Shot, Task and FakeStorage bindings. Enable the Files feature for Files workflow checks. This local package does not install or seed KitsuSync. The native Plugin Production settings provide the Drive OAuth start, app-visible root verification, configuration save, and explicit activation workflow. Keep the synthetic UAT egress blocked: do not start Google OAuth, use a real Drive, or start the Worker for Drive sync from this package.

```powershell
.\deploy\uat\scripts\seed.ps1 -Unit demo
.\deploy\uat\scripts\activate.ps1 -Unit demo
# Start only after the isolated KitsuSync integration handoff is complete.
.\deploy\uat\scripts\start-worker.ps1 -Unit demo
```

The seeded account names and shared synthetic test password are stored in the private local `ACCOUNTS.txt`. Open `http://127.0.0.1:55280` (or the selected port) and use the ordinary Kitsu login, then Open Production → Assets → VFX Files. This package does not run browser/GUI automation. Reuse the exact PR #2 installed-page Playwright evidence only for the source revision it covered; run human UAT before any server trial.

For readiness and a smoke status without revealing settings:

```powershell
docker compose --project-name vfx-files-uat-demo --env-file "$env:LOCALAPPDATA\VfxFilesPluginUAT\demo\uat.env" --file .\deploy\uat\compose.yaml ps
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:55280/api/data -Method Get
```

Stop while preserving synthetic data with `scripts/stop.ps1 -Unit demo`. Restart with `scripts/start.ps1 -Unit demo`; it waits for PostgreSQL, Redis, the egress guard and API before returning, and starts the separate Worker only if it was previously enabled and the restore gate allows it.

## Backup and restore

`scripts/backup.ps1 -Unit demo` stops API and Worker before taking a PostgreSQL custom-format dump and a FakeStorage/staging tar. It records SHA-256 hashes, source commit and image ID but excludes `.env`, passwords, JWTs, cookies and other credentials. The backup directory is kept under the private `%LOCALAPPDATA%` unit directory.

Restore only into a **new** UAT unit initialized with `init.ps1` and using the exact API/Worker image ID captured in the snapshot. Reuse/import that image by digest; a separate rebuild may produce a different digest and is refused. Do not run `init-host.ps1` in the target. `scripts/restore.ps1 -Unit restore-demo -SnapshotPath <backup-directory>` starts only the target PostgreSQL/Redis/egress guard services, waits for their health, verifies the manifest, source and image identities plus every payload hash, restores into fresh database and FakeStorage/staging volumes, then waits for the API health check. It intentionally does not start a Worker.

Before unlocking the restore-gated Worker, independently compare the source and restored sanitized restore-state reports, Latest Working/Published/Approved pointers, manifests and every object byte/checksum; verify the installed page and authorization. Only then use `scripts/confirm-restore.ps1 -Unit restore-demo -ManifestSha256 <snapshot-manifest-sha256> -IntegrityReviewed`. The marker is an operator assertion, not a cryptographic proof. If any value differs, leave the Worker stopped and discard only that new disposable UAT unit.

## Removal

`scripts/remove.ps1 -Unit demo -ConfirmUnit demo` checks the exact Compose project label on every container, then removes only that project's containers, labeled network/volumes, and protected synthetic credentials. Never use global `docker system prune`, `docker volume prune`, or an unscoped Compose project name for UAT cleanup.

## Resource and security limits

Compose's `cpus`, `mem_limit`, and `pids_limit` are per-container limits enforced when the Docker Linux engine supports cgroups. Record `docker inspect` values for every service; Windows Docker Desktop still applies its own WSL2 VM-wide CPU/RAM ceilings. Compose named volumes have no portable maximum-size option, and these files do not assert a disk cap. Server UAT is blocked until a dedicated, quota-enforced data filesystem and its monitoring/restore capacity are supplied.

This deployment template is intended for local review. Any VFX Studio Server UAT still needs a separately authorized, compatible isolated server instance, owner-approved synthetic identity policy, verified backups/restore, disk quota enforcement, patched-host approval, and a scoped change window. No Production or Staging installation is included.

## Human acceptance in the installed Kitsu page

The Kitsu home page at the unit's loopback URL is the normal landing page after standard Kitsu login. Use the Manager email and shared synthetic password from the protected `ACCOUNTS.txt`; never paste the password into an issue or report. The seeded Manager is a member of the seeded synthetic Production with the `manager` project role. The plugin metadata must show `frontend_project_enabled=true`, and API readiness now probes the authenticated Files health route instead of accepting the Kitsu home page alone.

Follow this screen path:

1. In Kitsu, open **Productions** and select `VFX Files Installed E2E <suffix>`.
2. Open the Production's **Assets** section and choose **VFX Files**. If the menu link is absent, use the authenticated fallback `/api/plugins/vfx_files/frontend/?production_id=<Production UUID>` on the same loopback origin. The ordinary `/` page is only the Kitsu home page.
3. Use **Drive settings** from the Files page to view sanitized Production authority status. The native Plugin page can start server-side OAuth, select a currently app-authorized My Drive root, save the immutable template catalog and sync scope, and explicitly activate storage. OAuth keeps the `openid` and `drive.file` scopes and server-held credentials. This egress-blocked UAT has no Google account and cannot prove live OAuth or Drive access; use FakeStorage-only tests for root and activation behavior.
4. Once KitsuSync is configured and a supported template has been materialized for the Plugin worker, select the seeded Kitsu Task. The Files page displays only persisted bindings verified against the selected connection/root; it offers no folder-creation mutation. Refresh after KitsuSync sync progress until verified **Open** links appear.
5. Sign out and sign in as Artist A. In **Working Files**, choose the seeded Task and upload a small synthetic text file. Confirm `w001` is completed and Latest Working. Do not use Production media.
6. Sign out and sign in as Artist B. Download Artist A's `w001`, then upload a distinct synthetic text file for the same Task. Confirm `w002` becomes Latest Working while `w001` remains listed and downloadable.
7. Sign in as Artist A. In **Publish and hand off**, select the Task, a completed file from `w002`, and **Synthetic Main**; choose **Publish Working File**. Wait for the immutable Publish to complete and QC to show `passed`.
8. Sign in as the separate Supervisor, refresh Files, and approve that exact Publish. Confirm Latest Approved and Latest Published identify the expected version. A publisher attempting to approve their own work must be rejected.
9. As an authorized Task participant, create a Handoff for the completed Publish addressed to Artist B. Sign in as Artist B, open the Production's Files page, and use **Accept** (or **Needs Fix** with feedback if testing rejection).
10. Refresh after each identity change. Confirm `w001`, `w002`, the Publish manifest and both pointers remain persisted. Worker restart and snapshot restore are separate operational checks; restore into a new disposable unit and keep its Worker stopped until the snapshot integrity review is complete.

### KitsuSync integration handoff (synthetic service only)

- The management base path is `/api/plugins/vfx_files/integrations/v1`; it uses the agreed KitsuSync service bearer/JWKS contract. Legacy KitsuSync authority markers remain in force until an explicit approved import. Plugin-native OAuth start and sanitized status exist, but verified root selection, templates and activation are incomplete, so the KitsuSync consumer must not become read-only yet.
- The API-side service verifier requires `VFX_FILES_KITSUSYNC_ISSUER` and `VFX_FILES_KITSUSYNC_AUDIENCE`; it fetches keys only from `<issuer>/.well-known/vfx-files-jwks.json` over HTTPS. Use the synthetic issuer/JWKS and scoped RS256 tokens for isolated tests. Never print or share signing keys or bearer tokens.
- The Plugin-to-KitsuSync business-event receiver is a separate trust direction: configure an HTTPS endpoint ending at `/api/integrations/vfx-files/v1/events`, producer key ID `vfx-files-plugin`, and a synthetic HMAC key file readable only by the Worker. Never reuse the RS256 management bearer as the event key.
- OAuth server settings are `VFX_FILES_GOOGLE_CLIENT_ID`, `VFX_FILES_GOOGLE_CLIENT_SECRET`, `VFX_FILES_GOOGLE_OAUTH_REDIRECT_URI`, and the fixed `VFX_FILES_KITSU_PLUGIN_RETURN_URI` / `VFX_FILES_KITSUSYNC_RETURN_URI`. Return URIs are server allowlist values with no query or fragment; the client cannot select a redirect URL. The native callback remains server-side with PKCE and exactly `openid + drive.file`.
- The Plugin API and Worker must use the same exact source revision/image. Readiness reports the installed schema epoch; the single-authority branch advances the Plugin schema to epoch 17 after `vxf_0018`. The worker command in the packaged UAT image is `/opt/zou/env/bin/python -m vfx_files.worker_runtime --poll --idle-seconds 2`.
- Keep the Plugin API on the isolated loopback ingress. The management service needs authenticated reachability to the Plugin API; the signed event receiver needs authenticated TLS reachability from the Worker. Do not bridge either path to Production, Staging, real Drive, or live Discord.
- Current integration blocker: this bundle does not include or seed a KitsuSync service, so signed event delivery, read-only KitsuSync status consumption and Discord routing are not exercised here. Plugin materialization resolves the exact supported catalog identity, revision and safe overrides into a persisted, config-revision-fenced `SharedTemplateConfiguration`; activation requires a verified writable root and non-empty compatible scope. Legacy KitsuSync settings require a manager-confirmed, revision-fenced import; import retains the legacy source and template structure, advances the activation epoch and leaves the Plugin inactive. End-to-end integrated readiness still requires a synthetic KitsuSync v1 instance and a current-source PostgreSQL/API/separate-Worker run. Do not substitute this Files page or legacy routes for KitsuSync's signed-event/Discord dispatcher responsibilities.

For resource capture while performing the UI stages, run the read-only sampler from the repository root in a separate PowerShell window, once per stage:

```powershell
python .\deploy\uat\tools\measure_resources.py --project vfx-files-uat-<unit> --phase working-upload --duration-seconds 60 --interval-seconds 1 --output "$env:LOCALAPPDATA\VfxFilesPluginUAT\<unit>\working-upload.json"
```

Replace `<unit>` and `--phase` with `publish`, `restore`, or another stage label. The sampler reads Docker stats, inspect metadata, project volume sizes, writable layers, image virtual sizes, and log byte counts. It does not read log contents. Docker image virtual sizes may share layers; daemon-wide `docker system df` must not be reported as this unit's unique disk use.
