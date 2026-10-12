# VFX Files plugin and Worker package

The isolated implementation ships one code tree: `plugin/`. It contains the Zou plugin, the Files page, durable job handlers, and `worker_runtime.py`. There is no second Worker source archive to version separately. The API process and Worker process must run the same plugin revision.

## Verified isolated installation shape

For the pinned disposable Zou host, place the contents of `plugin/` at `/opt/zou/plugins/vfx_files` and make `/opt/zou/plugins` importable by both the API and a separate Worker process. Register the plugin through Zou's supported plugin lifecycle. Keep the feature disabled until the plugin migrations, permissions, storage configuration, and restore checks have been reviewed.

The acceptance Worker ran as a separate process in the same pinned Zou Python environment:

```text
PYTHONPATH=/opt/zou/plugins /opt/zou/env/bin/python -m vfx_files.worker_runtime --once
```

The isolated entrypoint accepts only `--once`. It checks the restore-validation marker before creating the app, requires the explicit Worker enable flag, and currently accepts only the persistent FakeStorage adapter with configured staging and fake-storage roots. The FakeStorage settings and roots are for disposable tests; they are not production Google Drive settings.

The installed-page E2E and Worker used the same installed `plugin/` bytes. A separate Worker process claimed and completed durable jobs; the stopped-host readback is recorded in `DISTRIBUTION-READBACK-2026-10-12.md`.

## Deployment boundary

This describes the tested isolated package shape, not a production installation procedure. The current Worker entrypoint is one-shot, and the candidate Worker runtime does not wire the Google Drive adapter. No continuous Worker supervisor, production Google Drive Worker configuration, official patched-host release, or Production-compatible installation path is included. Do not use this package to modify a live Kitsu/Zou host.
