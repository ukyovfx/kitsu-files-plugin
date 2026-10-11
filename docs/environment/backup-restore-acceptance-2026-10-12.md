# Isolated Backup / Restore Acceptance — 2026-10-12

## Scope

This rehearsal used only synthetic records and the pinned disposable image `cgwire/cgwire:1.0.70-1.0.94-01` (`sha256:e6d9021241b12f88616762b4c7600a5e84d646119cba4a153ce7908fbd4f300a`). The source and restore hosts were separate containers. No Production, Staging, Escape files, KitsuSync database, or Google account was accessed. The plugin worker ran only as an explicit one-shot process; it was not left running as a daemon.

## Snapshot contents and verification

The consistent snapshot was taken after quiescing the source API and Worker. Its manifest covers the full Zou PostgreSQL database dump, plugin package, pinned host lock, non-secret plugin settings, shared-template and binding records, staged upload chunks, FakeStorage metadata and object bytes, and a sanitized source-state report. Credential values are deliberately excluded. The snapshot itself and all listed files were SHA-256 checked before restore.

Restoration occurred in a fresh container of the same pinned image. Gunicorn and the worker were stopped while PostgreSQL, the plugin package, staged chunks, FakeStorage metadata and object blobs were restored. No Alembic downgrade was run; `vxf_0006` is not a Production recovery mechanism.

The restored database report matched the source report exactly. It contained two completed Working revisions, one completed Publish with passed QC and a native Zou OutputFile, one approval, one accepted Handoff, four completed durable jobs and 19 FakeStorage objects. All Working and Publish artifacts passed byte-count/SHA-256 verification; latest Working, Latest Published and Latest Approved IDs matched. The installed Files page then reloaded from the restored host, displayed `w002` as Latest Working, downloaded both historical revisions with their expected SHA-256 values, and showed the Published/Approved pointers. A Task artist's attempt to approve the exact Publish returned HTTP 403, and a subsequent database report remained identical.

Before writing the restore-validation marker, a separate Worker process failed closed with `Restore integrity verification is required before worker start`; the restored job state remained unchanged. After database, package, staging, artifact, UI, permission and pointer verification, the marker was written with the expected manifest SHA-256. A separate one-shot Worker then started and returned idle without claiming work. This proves the guard sequence for this plugin build; the marker is a local operator assertion and must only be written by a trusted restore procedure after independent checks.

## Result and remaining boundaries

| Gate | Status | Evidence |
| --- | --- | --- |
| Full isolated backup coverage | **PASS** | PostgreSQL dump, plugin package, settings, host lock, staging, FakeStorage metadata/bytes and source report covered by file hashes |
| Restore into separate disposable environment | **PASS** | Pinned Kitsu 1.0.70 / Zou 1.0.94 host; no downgrade; source and restored state reports equal |
| Worker held until verification | **PASS** | Pre-marker one-shot refused before claim; post-marker one-shot returned idle |
| Installed-page readback, historical downloads and permission check | **PASS** | Browser on restored host; refreshed UI, byte hashes, pointers, and denied approval verified |
| Google My Drive / Shared Drive API | **BLOCKED** | No authorized isolated Drive roots or credentials; contract tests use an injected fake HTTP transport and do not establish live provider behavior |
| Production Kitsu/Zou compatibility | **BLOCKED** | No sanitized read-only Production host evidence was supplied; the pinned pair is not a Production compatibility claim |
| Production rollback using `vxf_0006` downgrade | **NOT TESTED / PROHIBITED** | Destructive downgrade is excluded; recovery requires a consistent snapshot restore |

The rehearsal is an isolated restore proof, not a Production backup or deployment readiness claim. Production compatibility requires read-only confirmation of Kitsu and Zou version/build/commit/image digest, plugin registry and migration state, PostgreSQL version/encoding/extensions, reverse-proxy plugin route and upload limits, authentication/cookie/origin behavior, worker process configuration, durable staging mount, and sanitized Production/Task permission fixtures. Secrets and credential values must remain redacted.
