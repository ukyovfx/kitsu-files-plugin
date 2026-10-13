# VFX Files plugin and Worker package

The implementation ships one code tree: `plugin/`. It contains the Zou plugin, the Files page, durable job handlers, the Google Drive adapter, and `worker_runtime.py`. There is no second Worker source archive to version separately. The API process and Worker process must run the same plugin revision.

## Verified isolated installation shape

For the pinned disposable Zou host, place the contents of `plugin/` at `/opt/zou/plugins/vfx_files` and make `/opt/zou/plugins` importable by both the API and a separate Worker process. Register the plugin through Zou's supported plugin lifecycle. Keep the feature disabled until the plugin migrations, permissions, storage configuration, and restore checks have been reviewed.

The acceptance Worker ran as a separate process in the same pinned Zou Python environment:

```text
PYTHONPATH=/opt/zou/plugins /opt/zou/env/bin/python -m vfx_files.worker_runtime --once
```

The entrypoint retains `--once` for scheduler-driven execution and adds `--poll` for a supervised long-running process. `--once` is simple to schedule and preserves the prior contract, but has process-start overhead and pickup latency equal to the scheduler interval. `--poll` reuses the same durable claim loop and picks up jobs after a bounded idle wait, but requires a process supervisor to restart unexpected exits. The minimal operating choice is supervised `--poll` with a 2-second idle wait; use `--once` where the host already standardizes on a periodic scheduler. Poll mode accepts a bounded `--idle-seconds` delay and stops between jobs on SIGTERM or SIGINT. A service supervisor should allow the current bounded HTTP operation to finish during graceful shutdown. The restore-validation marker is checked before the app or Worker is created.

For isolated tests, `VFX_FILES_STORAGE_MODE=fake` requires the configured persistent FakeStorage path and matching authorized root ID. The fake root and bytes must remain disposable test data.

For a future Google Drive host, set `VFX_FILES_STORAGE_MODE=google_drive`, `VFX_FILES_DRIVE_CREDENTIALS_DIR` to an absolute protected directory, and `VFX_FILES_STAGING_ROOT` to durable local storage. Each database `StorageConnection.credential_ref` selects `<credential_ref>.json` in that directory. The JSON object contains only `client_id`, `client_secret`, and `refresh_token`; do not put access tokens in it. On POSIX hosts, the directory must be private to the Worker and each credential file must be a regular file owned by the Worker with mode `0600`. Store different Production credentials under different references. The secret is not stored in PostgreSQL or logged; access tokens remain process memory only. The API process and Worker must receive the same authorized secret mount and root configuration.

The Google adapter verifies the current My Drive owner or Shared Drive ID and the configured root before writes, constrains operations to that root, refreshes an expired access token once, fails closed on authorization denial, and sends bounded 429/5xx retry delays to the durable queue. Stable reserved provider IDs let retried folder/upload/Publish jobs reconcile before another write. Contract tests use injected Fake HTTP transports; they do not contact Google.

## KitsuSync business-event delivery

The Worker may deliver the Plugin-owned immutable event outbox to KitsuSync's v1 receiver at the exact path `/api/integrations/vfx-files/v1/events`. Set `VFX_FILES_KITSUSYNC_EVENT_ENDPOINT`, `VFX_FILES_KITSUSYNC_EVENT_KEY_ID`, and `VFX_FILES_KITSUSYNC_EVENT_HMAC_KEY_FILE` only in the Worker environment. The key ID must be issued by the KitsuSync owner specifically for producer `vfx-files-plugin`; the HMAC key file is a separate secret from RS256 management credentials. The API process and browser do not receive this key. The Worker requires an HTTPS endpoint, the exact contract path, a regular key file owned by its effective user with POSIX mode `0600` or stricter, and at least 32 key bytes. Incomplete or unsafe configuration prevents Worker startup; with all three settings absent, delivery remains disabled.

Each request is canonical contract-v1 JSON signed over the exact body bytes with the v1 timestamp/nonce/body-digest/idempotency framing. A stable event UUID is both the idempotency key and the receiver result identity; a response is acknowledged only for HTTP 202 with the exact matching event result. Accepted and duplicate results mark the row delivered. Transient transport errors, 408/425/429, 5xx, or malformed acknowledgements use at most eight bounded exponential retries (5 seconds up to 1 hour); permanent 4xx, explicit receiver quarantine, exhausted retries, stale activation/root binding, or invalid events are quarantined. Persisted diagnostics contain only stable response codes, not bodies, event payloads, HMAC material, or raw exceptions. A lease generation and unexpired DB-clock lease fence every state update; delivery uses the existing Worker process and PostgreSQL outbox, not a second queue.

Events are emitted only when the event-producing transition was created under the active KitsuSync-selected, root-verified connection and activation epoch. Historical Output Packages and task-folder jobs created before activation are not backfilled, and hierarchy bootstrap uses its existing silent sync path. Current producer coverage is `folder.provisioning.completed`, `output_package.created`, and `output_package.ready`. The created event identifies the package and manifest but lists no Drive objects until verification; the ready event lists only verified package object IDs. Package events whose complete object list exceeds the contract's 100-object limit are suppressed rather than truncated. Existing native Publish/Handoff records do not have the contract's OutputPackageDelivery identity and are not mislabeled as `file_delivery` events. No Plugin code sends Discord messages.

The additive `vxf_0014` migration binds outbox rows to the selected storage connection and captures each Output Package's notification epoch. Its downgrade refuses while either binding contains data; restore a consistent snapshot rather than dropping those records. PostgreSQL migration and live HTTP/Worker receiver acceptance must be verified in an isolated pinned host before enabling delivery.

The installed-page E2E and Worker used the same installed `plugin/` bytes. A separate Worker process claimed and completed durable jobs; the stopped-host readback is recorded in `DISTRIBUTION-READBACK-2026-10-12.md`.

## Offline review bundle

Build a deterministic, unsigned review bundle from a clean Git commit (never from working-tree or untracked bytes):

```powershell
python tools/build_offline_bundle.py --source-ref HEAD --output "$env:TEMP\vfx-files-review.zip"
```

The builder reads every input with `git show`, checks that the distribution SBOM has a matching SHA-256 entry for every tracked `plugin/` file, writes an embedded bundle manifest with source commit and pinned host/Python/frontend identities, reopens the ZIP, and byte-compares every payload entry with its Git object. It refuses to overwrite an existing output path. The archive is for offline review only; it is not signed, provenance-attested, rights-cleared, or approved for publication/deployment. Keep its output outside the repository and publish its digest only with the matching commit and review evidence.

## Deployment boundary

This documents the executable configuration boundary, not proof of a live Google Drive integration or Production compatibility. No real Drive credentials, authorized test root, supervisor unit, official patched-host release, or Production-compatible installation path was supplied or tested. The deployment owner must provision and scope OAuth credentials, select the correct My Drive or Shared Drive root, and configure a service supervisor in an approved isolated environment before considering live use. Do not use this package to modify a live Kitsu/Zou host.
