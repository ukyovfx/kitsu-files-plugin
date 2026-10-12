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

The installed-page E2E and Worker used the same installed `plugin/` bytes. A separate Worker process claimed and completed durable jobs; the stopped-host readback is recorded in `DISTRIBUTION-READBACK-2026-10-12.md`.

## Deployment boundary

This documents the executable configuration boundary, not proof of a live Google Drive integration or Production compatibility. No real Drive credentials, authorized test root, supervisor unit, official patched-host release, or Production-compatible installation path was supplied or tested. The deployment owner must provision and scope OAuth credentials, select the correct My Drive or Shared Drive root, and configure a service supervisor in an approved isolated environment before considering live use. Do not use this package to modify a live Kitsu/Zou host.
