# Direct installation into an existing Zou service

Status: operator handoff for a future, owner-approved installation. This is not
an installation record, a Production authorization, or a claim of compatibility
with the VFX Studio server. No live host was accessed for this document.

## Supported boundary

The deployable unit is the tracked `plugin/` tree from one exact commit. It
contains `manifest.toml`, Zou routes/models and six plugin-owned Alembic
migrations, the built Production Files page, storage adapters, durable job
handlers, and the Worker entrypoint. The API and Worker must use the same bytes
and the same host Zou Python environment. Do not install a replacement Zou
image, modify Zou core, install dependencies with an unreviewed `pip` command,
or run the UAT Compose installer against a server.

The plugin is registered by Zou's `zou install-plugin` lifecycle. The verified
local candidate's CLI accepts a plugin directory or ZIP and applies migrations
during installation; the server must then restart its API process to load the
routes. The official plugin guide describes this lifecycle and the route prefix
`/plugins/<plugin_id>`; the guide is marked no longer maintained, so the exact
updated server must be checked by its owner before use. [Zou plugin guide](https://zou.cg-wire.com/plugins/)

## Before an install window

The Server担当 must provide and verify, without sharing secret values:

1. Exact Kitsu/Zou versions, source/image digest, and whether this is the
   official build or a separately approved patched candidate.
2. Zou virtualenv path, executable path for `zou`, Python version, Python
   dependency inventory/audit, plugin installation directory, and the
   environment variable or configuration that makes that directory importable
   by both API and Worker.
3. API systemd unit name and restart/readiness procedure; database service and
   backup/restore procedure; current plugin list; PostgreSQL backup freshness
   and a restore rehearsal.
4. Worker service account, systemd policy, environment-file/secret delivery,
   durable staging path, log policy, stop timeout, and how Worker uses the
   same plugin tree and database URL as Zou.
5. A synthetic Production/Task and authorized Manager, Artist A, Artist B,
   Supervisor identities. Do not select a real Production or real media.
6. Approved isolated FakeStorage root. It must not resolve to My Drive, Shared
   Drives, or any production storage. Use no Google credentials in this test.
7. Whether loopback access plus SSH port forwarding is available and whether
   externally reachable Kitsu requires a security exception. Do not change
   public ingress as part of plugin installation.

Stop if the exact installed Python environment lacks a plugin import/runtime
dependency, the official-host vulnerability gate is still being treated as
resolved, the full DB restore path is unproven, plugin paths overlap another
plugin, or the synthetic scope/storage root cannot be isolated.

## Artifact build and verification

From a clean checkout with the source commit already reviewed, build to a new
operator-controlled directory outside the repository:

```sh
python3 tools/build_zou_plugin_package.py \
  --source-commit <40-character-reviewed-commit-sha> \
  --output /secure/handoff/vfx_files-<commit>.zip
```

The command accepts only a full commit SHA, reads only regular tracked Git
objects below `plugin/`, writes plugin files directly at the ZIP root, verifies
the ZIP CRC and byte-compares every archived file against that commit, and
creates a JSON sidecar with per-file SHA-256, source SHA, plugin version, and
migration order. It refuses to overwrite either output. Transfer both files
through the approved internal channel and independently verify the ZIP and
sidecar SHA-256 at the receiving host. The sidecar is integrity evidence, not a
signature or a trust/provenance attestation.

Before installation, record the source SHA and both received hashes in the
change ticket. Compare the manifest version and target host against the reviewed
handoff. Do not accept a branch name, container tag, or local uncommitted
directory as source identity.

## Install and verify sequence

Use an approved change window. Keep the plugin disabled and Worker stopped.
Quiesce any existing VFX Files requests/jobs. Take a consistent, encrypted
backup of PostgreSQL (including all `plugin_vfx_files_*` tables and Zou's
plugin registry record) and the plugin's durable staging/artifact data. Verify
backup readback/restore before proceeding. Record the current plugin package
and configuration for recovery.

The sequence below is illustrative only; substitute paths and service names
after the owner verifies them. It intentionally does not restart or migrate a
server by itself:

```sh
# In the verified Zou virtualenv, as the approved deployment account:
<zou-venv>/bin/zou list-plugins --format json --verbose
sha256sum /secure/handoff/vfx_files-<commit>.zip
<zou-venv>/bin/zou install-plugin --path /secure/handoff/vfx_files-<commit>.zip
<zou-venv>/bin/zou list-plugins --format json --verbose
```

`install-plugin` already performs the plugin migrations. Do not additionally
run `migrate-plugin-db` unless a reviewed recovery procedure specifically
requires it. The local Zou 1.0.94 candidate source inspection found this
installer is not atomic: it registers/updates plugin metadata, copies files and
applies migrations in sequence; a failure can leave partial state. If it fails,
keep API/Worker gated, preserve logs and the backup, and have the Server担当
compare actual DB/filesystem state before recovery. Do not retry with `--force`
or attempt hand-edits as a shortcut.

After successful install, restart only the verified Zou API unit using its
normal operator procedure. Confirm API readiness, plugin registration, route
readiness and browser integration before enabling the feature. The plugin
health route is `/plugins/vfx_files/health` relative to Zou's configured API
mount; it must reject anonymous requests (401). The UAT container checks it at
`http://127.0.0.1:5000/plugins/vfx_files/health`. The public Kitsu prefix or
reverse proxy may add or strip a path prefix, so confirm the real URL mapping
from the owner's reverse proxy and installed Zou configuration rather than
assuming the browser URL equals the internal route.

Set `VFX_FILES_ENABLED=true` only after plugin migration, synthetic user
permissions, CSRF/same-origin configuration, FakeStorage root authorization,
durable staging and Worker compatibility are verified. API and Worker need the
same `VFX_FILES_STAGING_ROOT`; for this synthetic test use
`VFX_FILES_STORAGE_MODE=fake`, a dedicated `VFX_FILES_FAKE_STORAGE_ROOT`, and
the matching `VFX_FILES_FAKE_ROOT_ID`. Worker additionally requires
`VFX_FILES_WORKER_ENABLED=true`. The API's `VFX_FILES_KITSU_ORIGIN` must match
the exact browser origin serving Kitsu, without a path. Keep
`VFX_FILES_DRIVE_CREDENTIALS_DIR` unset. Use the standard Kitsu login and open the
Production page's Assets section, then VFX Files. The installed page is
Production-scoped; Task detail drawer integration is tracked separately under
Issue #5 and is not part of this install.

Start the separate Worker only after API readiness and install/source identity
match. A production service unit must be derived from the host's actual systemd
and environment layout; no unit file is prescribed here. The process contract
is:

```sh
PYTHONPATH=<verified-plugin-parent> <zou-venv>/bin/python \
  -m vfx_files.worker_runtime --poll --idle-seconds 2
```

Use a supervisor with SIGTERM/SIGINT forwarding and a stop timeout longer than
the configured bounded external operation. Worker must run as a dedicated
least-privilege account, read only the required config/credentials, write only
its staging/log locations, and share no broader storage permissions than the
approved FakeStorage root. Keep credentials out of command-line arguments,
unit text, logs and shell history. Worker restarts must resume through durable
job leases; verify a stopped/restarted Worker against synthetic jobs.

## Human UAT checklist

Use the same reviewed package and synthetic test dataset throughout. Save
evidence without cookies, JWTs, passwords, real user data, or media.

1. Standard Kitsu login as synthetic Manager; select synthetic Production →
   Assets → VFX Files. Confirm the page is served within the normal Kitsu shell.
2. Configure an isolated FakeStorage connection and shared template. Preview it
   and activate only when all bindings resolve within the test root.
3. Request provisioning for a synthetic Shot/Task; confirm durable job
   completion and folders/bindings in the refreshed UI.
4. Login as Artist A; upload a small synthetic file as Working `w001` and wait
   for verified completion. Record checksum from the UI/API evidence.
5. Login as Artist B; download `w001`, verify bytes locally, upload `w002`, and
   confirm both revisions remain downloadable and Latest Working is `w002`.
6. Publish the exact Working revision. Confirm immutable artifact, manifest,
   worker result and QC state. Confirm latest published and latest approved are
   displayed separately.
7. As a distinct synthetic Supervisor, approve that exact Publish. Confirm
   self-approval and Artist approval requests are rejected and Latest Approved
   points to the selected immutable Publish.
8. Create a Handoff to the other synthetic artist; test Accept and Needs Fix
   with feedback in separate test handoffs. Confirm unauthorized identities
   cannot read or transition another Production's handoff.
9. Stop Worker cleanly, leave one synthetic durable job pending, restart it,
   and confirm exactly one persisted result with no duplicate provider object.
10. Refresh Kitsu and verify all statuses/pointers remain persisted. Do not run
    uninstall or downgrade as cleanup.

Any failure blocks the Human UAT; do not change real Production data to work
around it. Preserve request IDs and sanitized diagnostics for the implementation
team.

## Rollback and data retention

Disable `VFX_FILES_ENABLED`, stop and drain Worker, prevent new plugin requests,
and take a fresh consistent snapshot. If a code rollback is needed, restore the
previous reviewed plugin source and Worker to the same revision while retaining
the plugin schema and data, then use a tested forward-compatible route. Do not
run `zou uninstall-plugin`: the inspected lifecycle downgrades plugin migrations
and removes plugin files/registration, which deletes plugin data. Do not use
`zou migrate-plugin-db` to downgrade. If schema or DB state must be reversed,
the safe recovery is a coordinated database-and-file restore from the pre-change
snapshot after the owner verifies the impact on unrelated Zou changes. This
requires a maintenance decision and restore rehearsal; it is not a code-only
rollback.

Keep API and Worker on matching source revisions. Keep artifacts and staging
data under the approved retention/backup policy. Retiring the feature means
disable and retain/backup its data unless a separately approved data-retention
decision authorizes deletion.

## Compatibility and security status

- Local evidence at the starting PR #4 commit was: 31 focused tests passed, 2
  skipped; some Docker Linux-engine checks passed on a Windows workstation;
  Ubuntu server testing was not performed. These are not server-install proof.
- Kitsu 1.0.70 / Zou 1.0.94 are the target pins. Official-host dependency
  vulnerability gate remains **FAIL**. A separately patched unofficial
  candidate's local tests do not change that status or authorize use.
- The local candidate image was `sha256:714bc3acf49b2ab76b67b36a2f89190ef1adbebec73b9fb7d28849506478a08f`,
  manifest digest `sha256:34446f6e16ccf5ff0d9bab884c671d7d41a36d6ecb37b03ae1d4963fcdaeb52a0`.
  These are historical local test identities, not server deployment artifacts.
- If the live Kitsu is externally reachable, the Server担当 must separately
  validate TLS, reverse-proxy origin/cookie settings, CSRF, API access control,
  firewall exposure, backups, and the official-host dependency advisories.
  This plugin does not remediate Zou host CVEs. Do not expose the Worker or
  FakeStorage ingress publicly.
- This is owner-managed internal UAT preparation, not formal OSS release
  approval, a license-rights determination, penetration test, or release
  provenance/signature.

## Required Server担当 return package

Return the verified host facts in the internal change record: versions/digests,
Python and Zou executable paths, plugin path/import configuration, API and
database service names, Python dependency audit, current plugin list, backup
and restore evidence, isolated storage root, network/TLS/origin boundary, and
resource/permission evidence. Then schedule a separate owner-approved install
window. This repository preparation performs no server action.
