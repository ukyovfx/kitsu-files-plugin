# VFX Studio Kitsu Files Plugin Design

**Status:** Architecture approved; isolated implementation and validation in progress.
**Date:** 2026-10-11
**Audited base:** `e2788e0af2632630d2533785247c0509fd191035`

## Goal and boundaries

Build an upgrade-resilient production Files extension on official Kitsu Web and Zou. The extension adds one dedicated Production Files page, durable source ingestion and Shot/Task folder provisioning, versioned Publish and QC records, immutable-by-application published artifacts, explicit approval evidence, pinned dependencies, and Task Handoff. Keep Kitsu Web/Zou core, the Task detail drawer, review UI, and KitsuSync unchanged. Discord notifications remain in KitsuSync. Nuke integration and Japanese name-order customization are out of scope.

The selected design is an official Zou plugin plus a separately supervised worker. The plugin owns HTTP routes, frontend assets, plugin tables and migrations. The worker shares the pinned Zou/Python environment and PostgreSQL database, but external I/O is outside database transactions. Every external operation is a durable job with a fenced lease, idempotency key and reconciliation state. Google credentials are optional; fakes support all local tests.

Stable Kitsu UUIDs and Google Drive IDs are identities. Names are display/path hints only. No Production/Staging access, live install, external credential use, production migration, Drive write/delete, merge or release is included in design work or local development.

## Verified host surface and lifecycle constraints

Candidate upstream baseline as of 2026-10-11: Kitsu Web `v1.0.70` and Zou `v1.0.94`. Exact upstream files reviewed include Zou `plugins.py`, `plugins_service.py`, `plugin_alembic_template/env.py`, `WorkingFile`, and `OutputFile` at tag `v1.0.94`. This is a candidate pair, not a verified compatibility pair. Deployed VFX Studio versions and configuration remain `UNKNOWN`; the server is not inspected in this phase.

Zou `v1.0.94` runs plugin migrations through Alembic using version table `alembic_version_<plugin_id>` and filters Alembic tables by `plugin_<plugin_id>_` prefix. The install path updates or creates the core Plugin row before copying files and running migrations; there is no compensating rollback if a later step fails. Plugin hooks catch and log exceptions. Uninstall attempts downgrade, but the downgrade utility catches every exception and continues; uninstall then recursively deletes plugin files and deletes the Plugin row. A successful downgrade can drop plugin-owned tables. Therefore:

- Never use plugin uninstall for rollback or routine disable.
- Do not use plugin skeleton `force=true` on any populated plugin directory.
- Ship strictly increasing plugin versions; Zou logs a warning but does not reject same/older package versions, so deployment must reject them before invoking install.
- Disable by stopping the worker and setting the extension feature gate off; keep code, plugin row, migrations and data installed.
- Upgrade only from a quiesced host with a tested, restorable snapshot of the database, plugin package/migration chain, deployment configuration references, and staged-upload storage. Zou install is not atomic; on failure keep the extension disabled and restore the consistent pre-upgrade snapshot under an approved maintenance procedure. Do not infer success from the plugin row or installer response alone.
- Retain old migration revisions for as long as any supported package can be rolled forward or restored. For incompatible populated schema, use a forward repair or restore the complete consistent snapshot; never downgrade populated data as an ordinary rollback.
- Test failed install and partial uninstall only in a disposable isolated Zou/PostgreSQL fixture. Assert and document the destructive uninstall behavior; do not uninstall a populated local acceptance fixture.

## Product shape

```text
Kitsu Production menu
  └─ isolated Vue 3 + Vite Files page (official plugin static route)
      └─ authenticated Zou plugin API (/api/plugins/vfx_files/... through the official proxy)
          ├─ plugin_vfx_files_* PostgreSQL tables + Alembic history
          ├─ Kitsu file-reference adapter (WorkingFile / OutputFile)
          └─ durable jobs ← separate fenced worker
                              ├─ StorageAdapter (fake / Google Drive)
                              └─ QC / manifest / reconciliation services
```

Use the official Production menu context (`production_id`, optional `episode_id`) only as an untrusted filter hint. Resolve and validate every referenced Production, Episode, Entity and Task from Zou on every request; never authorize from iframe query parameters. Use official static plugin routes and documented frontend constraints (`base: './'`). The page is a Production plugin (`frontend_project_enabled = true`), not a studio-wide sidebar tool; it does not inject into or modify the Task drawer or review interface. Pin all frontend dependencies in the extension lockfile. The pinned Kitsu/Zou host uses same-origin JWT cookies for the browser, so the minimal wrapper sends same-origin credentials, never reads or stores a token, and fetches a short-lived plugin CSRF token for each mutation. Mutations also require JSON or binary content type and an exact configured Origin. Bearer headers remain accepted by Zou but are not required by the page.

The Kitsu file-reference adapter is the only component allowed to create or query core WorkingFile/OutputFile records. Zou `OutputFile` has an integer `revision` and a native uniqueness constraint over name, entity/asset instance, output type, task type, temporal entity, representation and revision. Several fields are nullable, so the plugin must not assume that this native constraint alone serializes all cases. `WorkingFile` likewise has its own revision namespace. Plugin Publish versions and their full SHA-256/64-bit byte counts remain plugin-owned and separate from both native revision fields.

## Plugin-owned data model and constraints

All plugin tables use the `plugin_vfx_files_` prefix and plugin-owned UUID primary keys. Kitsu IDs are external UUID references with no core-table FK, so Kitsu upgrades do not couple schema ownership. Migrations are additive; any destructive data-retention decision is a separate reviewed migration.

- **StorageConnection:** provider (`google_drive`), account/ownership mode (`my_drive` or `shared_drive`), owner subject or Shared Drive ID, and administrator-managed credential reference. Tokens/secrets are never stored here.
- **AllowedRoot:** non-null `storage_connection_id`, Drive ID where applicable, root folder ID, verified root ancestry/capabilities, and administrator-set allowed operations. No path/name defines its identity.
- **FolderBinding:** non-null `storage_connection_id`, non-null `allowed_root_id`, non-null canonical scope identity derived from Kitsu object type plus Kitsu UUID, provider Drive ID and folder ID, optional parent binding, state and audit timestamps. Enforce `UNIQUE(storage_connection_id, allowed_root_id, canonical_scope_key)` and `UNIQUE(storage_connection_id, drive_id, folder_id)`. No nullable column participates in the logical identity constraint. Parent may be null for a root binding.
- **UploadSession / UploadPart:** Kitsu task/production UUIDs, authenticated source `person_id`, server-generated session UUID, expected byte length, chunk size, accepted non-overlapping byte ranges and SHA-256 per part, whole-file SHA-256, state, expiry and staging object key. Defaults: 100 GiB maximum file, 500 GiB active staged bytes per Production, 64 MiB chunks, seven-day resumable session expiry. Administrators may lower these limits. Over-limit or unconfigured quota fails closed. The staging object key resolves only inside a dedicated server-owned durable volume/object-store namespace; no local user path or arbitrary URL is accepted.
- **Job:** unique idempotency key, operation/payload reference, Kitsu scope UUIDs, authorization actor, explicit state, attempt count, next retry, lease owner UUID, monotone lock generation, DB-clock lease expiry and sanitized failure class. Claims use short PostgreSQL transactions with `FOR UPDATE SKIP LOCKED`; external I/O occurs after claim commit. Heartbeat and completion updates require matching owner + generation and unexpired DB-clock lease. A stale worker cannot complete after takeover. Use bounded exponential backoff with jitter, per-provider concurrency and rate limits.
- **OutputFamily:** plugin-owned stable UUID and configuration mapping to Zou output type/task type/representation semantics. Never key output family by a mutable display name.
- **Publish:** task UUID, OutputFamily UUID, plugin `publish_version`, source WorkingFile UUID if present, native OutputFile UUID when linked, source owner, state/checkpoints, artifact Drive IDs, full SHA-256, bigint sizes, manifest hash, QC decision, timestamps and error state. Enforce unique `(task_id, output_family_id, publish_version)` and allocate under a PostgreSQL row lock/unique constraint. Do not reuse a reservation after a failed attempt; mark it failed/abandoned and allocate the next number.
- **PublishArtifact / Manifest / QCRecord:** immutable-by-application records. Manifests include exact Kitsu UUIDs, plugin publish version, source and output Drive IDs, byte counts, SHA-256 hashes, QC policy/version/results, timestamps and tool/version metadata. Canonicalize before hashing; store exact manifest bytes/content-address and SHA-256. Do not store secrets or tokens.
- **OutputFileLink:** mapping from one Publish to exact native OutputFile UUID and captured native revision. Reserve/query native revision through the adapter scoped to the complete native family tuple; serialize concurrent operations in the plugin DB. On native unique conflict, re-query/reconcile by deterministic plugin Publish identity in `OutputFile.data` when that supported field is verified; otherwise keep the Publish blocked for audited reconciliation. Never equate native revision with Publish version. Existing native outputs remain untouched and are included in native revision allocation queries.
- **ApprovalEvent:** append-only decision (`approved`, `revoked`) bound to exact Publish UUID and exact manifest SHA-256, reviewer Kitsu UUID, policy ID/version/hash, reason and timestamp. No update/delete API. Latest Approved is a materialized cache rebuilt from the most recent still-active approval event for eligible completed publish; revocation invalidates only its exact approval, so an earlier still-active approval may again be the latest. Task status and Kitsu review previews are never approval evidence. Self-approval of one's own Publish is denied.
- **PublishPointer:** independent Latest Published and Latest Approved caches, each pointing to a Publish UUID and updated transactionally from their separate eligibility rules. Pinned dependencies store exact Publish UUID, manifest hash and artifact IDs; they never resolve through a latest pointer.
- **Handoff / HandoffEvent:** handoff against exact Task and Publish, sender/recipient UUIDs, state (`pending`, `accepted`, `needs_fix`), feedback and append-only transition events. Transitions never mutate artifact bytes or global Kitsu Task status.
- **ReconciliationFinding / AuditEvent:** append-only findings/actions with IDs and sanitized classifications; no raw provider response or credentials.

If a Kitsu object UUID no longer resolves, retain plugin records as `orphaned`; do not delete, relink by name, or continue writes. Keep immutable artifacts, manifests and approval history readable by authorized administrators. Any explicit remap requires an audited administrator action and exact identity proof.

## Core workflows

### Authorization and request context

All routes require a valid Zou JWT and resolve target UUID relationships server-side. The extension permission adapter maps these logical capabilities to exact pinned Zou permission helpers and fails closed for unknown roles:

| Capability | Initial policy |
|---|---|
| Read Files, publish metadata, Handoff and authorized download | Active Production member with Zou view access to the resolved Task/Production |
| Start/resume upload and create Handoff | Task assignee or Production supervisor/manager with Zou edit access |
| Publish | Task assignee or Production supervisor/manager with Zou edit access |
| Approve/revoke | Production supervisor/manager with explicit approval permission; publisher cannot approve own Publish |
| Folder provisioning, connection/root configuration, reconciliation override | Zou administrator or explicitly configured Production manager |
| Worker execution | Re-check persisted actor permission and object relationship before each queued privileged stage; revoked, deleted or changed authorization moves job to `blocked_authorization` without external mutation |

Mutations use the host's same-origin JWT cookie or an explicit bearer header, plus a plugin-signed short-lived CSRF token bound to the authenticated person. Validate `Origin` against the configured Kitsu origin for all mutations and require the CSRF header; reject cross-origin requests and simple/form content types. The browser uses same-origin fetch credentials and never extracts the host cookie. Apply server-side rate limits and object-level authorization to download URLs; do not expose public links.

### Source ingestion

Browser uploads use `POST /uploads` to create an owner/task-scoped session with declared length and expected SHA-256, then `PUT /uploads/{session_id}/parts/{index}` using fixed 64 MiB chunks and byte-range/checksum headers. Server records accepted non-overlapping ranges transactionally and makes repeated identical part requests idempotent; conflicting bytes/ranges fail. Resume is permitted only to the same actor or an authorized supervisor. Finalize requires contiguous coverage from byte 0 through declared length, expected per-part checksums, and server-computed whole-file SHA-256. A successful DB state is not evidence of durable bytes: finalize fsyncs/stages to the durable staging adapter, verifies byte length and full SHA-256 by read-back, then queues work. Worker and web service share an explicit durable storage contract. Incomplete sessions expire after seven days; cleanup deletes only staging objects whose expired session state and exact object key are transactionally claimed. Never clean an object referenced by a non-expired/active session.

### Folder provisioning and Google Drive identity

Provision deterministic Shot/Task folder relationships from stable Kitsu UUIDs. Each target reservation is inserted under the non-null unique key before provider I/O. Google Drive documents that IDs from `files.generateIds` can be used to create files and folders (including `application/vnd.google-apps.folder`); after successful creation, repeated creates with that ID return `409 Conflict` and do not create duplicates. Reserve the ID in PostgreSQL before `files.create` and use it as provider identity. Shared Drive requests include `supportsAllDrives=true`; list/search uses the correct `driveId`, `corpora` and `includeItemsFromAllDrives` parameters. My Drive identity is scoped to the authenticated owner subject; Shared Drive identity is scoped to exact drive ID and validated capability/role. Verify the target root's Drive ID and parent ancestry before creating anything, and reject traversal outside the configured root. Isolated validation remains required for the selected My Drive/Shared Drive authentication and capability configuration.

If provider response is lost, `files.get` by the persisted reserved ID determines whether the intended object exists. `404` is absence only when the same authenticated connection can prove visibility of the exact drive/root and the provider's semantics make that result authoritative; permission errors, partial listing, timeout or other ambiguity leave the reservation `reconcile_required` and block retries that could create another object. Never infer absence from name search. Store optional `appProperties` correlation in addition to ID, but never rely on them as the sole identity. No automated delete, move, rename or public sharing is provided. Do not create or update permissions; inherit configured root access and inspect capabilities.

### Publish, QC and native file records

The publish job follows persisted checkpoints exactly: `reserved → source_ready → artifact_verified → qc_recorded → manifest_verified → native_linked → completed`. Each checkpoint is committed only after evidence is persisted for the operation immediately before it. Artifacts use a unique new Drive ID/object per publish and are never updated/deleted by this app. Source WorkingFile is linked when it exists and is relevant; otherwise its ID is null and manifest explains provenance. Source upload ownership remains attributed to the authenticated Kitsu actor.

After storage upload, read back metadata and bytes (or a provider checksum plus streamed content verification where available) and persist Drive ID, size and full SHA-256. Record QC result and policy before manifest generation. A failed QC publish remains a visible failed/ineligible version unless an authorized, audited waiver policy explicitly allows completion. Verify the exact manifest bytes/hash and artifact references before the native OutputFile bridge. Native OutputFile creation uses its own revision and native uniqueness tuple; plugin stores the native mapping only after querying and verifying the returned OutputFile. Add deterministic plugin Publish identity to supported `data` JSON only after the exact Zou service/path is verified.

Native OutputFile create is a non-idempotent single-flight operation unless the pinned Zou API proves otherwise. Before calling Zou, atomically consume a one-shot `native_create_attempt` for the Publish under the current lease generation and commit that state. Disable automatic HTTP/client retries for create. No worker, including a lease successor, may issue a second create once the attempt is consumed. The worker then calls the verified in-process Zou service and records the returned OutputFile ID/revision. On exception, lost response, worker death or lease loss, the Publish becomes `native_create_ambiguous`; reconciliation may read the complete native family tuple and the deterministic marker, but a missing marker is not enough to retry while an original call could still be executing. If outcome remains uncertain, keep the publish blocked for audited operator resolution. Only after proving the original process/request is terminated and the authoritative native query shows no OutputFile may an explicit audited reconciliation authorize a new one-shot attempt. This intentionally trades automatic recovery for duplicate prevention. Add this single-flight behavior before implementation; generic job fencing alone does not fence a side effect already in flight.

Latest Published means the highest plugin publish version whose state is `completed`, `manifest_verified`, `native_linked`, and whose QC is `pass` or valid policy `waived`; it advances in the same DB transaction as completion. It is monotone: an older late completion cannot replace a higher eligible version. Failed/ineligible newer versions do not block a lower eligible publish from remaining Latest Published. Latest Approved is derived separately from active approval events and never inferred from native revision/status. Pinned dependencies remain exact Publish IDs even after later publishes or approval revocation.

### Disable, migration, backup and recovery

Disable is operational: flip `VFX_FILES_ENABLED=false`, stop/drain the worker and wait for all leases to expire; API mutators return a stable disabled response and the Files page explains the disabled state. Do not uninstall/unload plugin files. Upgrade requires host write quiescence, worker drain, verified backup and a tested manifest of host/plugin/worker versions. Snapshot: full Zou PostgreSQL database including plugin schema and core Plugin row, exact plugin package and migration chain, deployment/worker image digests, non-secret configuration and credential references, staged upload store, manifest/QC/pointer records (in DB and any exported copies), and all published artifact bytes in a separate operator-managed encrypted backup target with hashes. Drive alone is not backup and no WORM claim is made.

During restore, keep Files disabled, stop all workers, restore database + plugin package + staging snapshot + backup artifacts to a consistent checkpoint, verify hashes and host/plugin/worker schema epoch, then run read-only Drive reconciliation before re-enabling jobs. Do not overwrite or delete external Drive state during reconciliation; any mismatch blocks the affected publish and requires an audited repair decision. Google Drive's `keepForever` is optional retention support subject to its API limits, not immutability. The application-level write-once promise covers only operations made through this extension; Drive owners/admins can still alter external artifacts.

## Compatibility and version policy

| Surface | Current evidence | Candidate target | Compatibility rule |
|---|---|---|---|
| VFX Studio Kitsu Web | `UNKNOWN`; Production not inspected | upstream latest checked 2026-10-11: `v1.0.70` | current official release verified; Production runtime unknown |
| VFX Studio Zou | `UNKNOWN`; Production not inspected | highest official tag checked 2026-10-11: `v1.0.94` | current official source tag verified; Production runtime unknown |
| Kitsu/Zou pairing | no official pair matrix found | `v1.0.70` + `v1.0.94` tested in the disposable official image | plugin install, auth, migrations and upload E2E pass; no general or Production compatibility claim |
| `kitsu-client-js` | upstream repository has no published releases; sample package points to mutable GitHub ref | immutable commit pin chosen only after compatibility spike | never ship moving `main`; if auth integration cannot be pinned and tested, replace with minimal reviewed API wrapper |
| Kitsu file models | exact Zou models inspected: native OutputFile revision and nullable uniqueness fields; WorkingFile has own revision | adapter via Zou-supported routes/services | no direct core schema edits; exact linking semantics must pass fixture tests |
| Plugin schema | no live plugin schema inspected | plugin-scoped Alembic version table | additive migrations only; reject mixed schema epoch/worker |
| Google Drive | no credentials or external calls | fake by default; My Drive + Shared Drive adapters | mocked contract tests local; real provider support requires later explicit isolated-account acceptance |
| KitsuSync / Discord | separate repository/release lifecycle | no change | no shared version, deployment or notification path |

Plugin code declares `schema_epoch` and host compatibility range. Worker startup compares its build's minimum/maximum schema epoch with the DB's recorded epoch and pinned Zou version; mismatch refuses all claims. Deploy one worker build at a time. Before migration, pause claims and drain in-flight leases; after schema change, old workers must stay stopped. Resume only after host, plugin and worker versions match. Never use object names to reconnect a missing Kitsu entity.

## Local validation and release gates

All development uses an isolated checkout, isolated Python env, isolated PostgreSQL, synthetic fixtures and the candidate exact host tags. Fake storage is the default. No real Kitsu/Drive credentials, browser profiles, Staging/Production resources, or external Drive writes are used.

Before implementation approval, independent review must pass this corrected design and plan. Before production readiness, require:

1. Exact `v1.0.70` / `v1.0.94` install, plugin page, auth and API integration pass in an isolated host; otherwise revise candidate pins.
2. Fresh install, populated-data upgrade, failed install recovery, partial uninstall behavior and migration/restore rehearsals pass in disposable PostgreSQL. Installer success alone is insufficient.
3. Auth matrix, context spoofing, object relationship checks, CSRF, authorization revocation and worker rechecks pass.
4. Fake-adapter tests cover all provider failures; an explicitly authorized, isolated test account later verifies Google ID reservation, Shared Drive behavior, resumable upload, ambiguous responses, visibility and capabilities. No Production or shared Drive test.
5. Concurrency tests cover folder reservations, job lease takeover/fencing, publish numbering, OutputFile collision and pointer monotonicity.
6. Every publish checkpoint has a crash/lost-response recovery test. Changed/missing/inaccessible/deleted Drive artifacts are detected and blocked without auto-write.
7. Backup/restore includes Zou DB, plugin package/migrations, staged bytes, published artifact bytes, manifests and deployment records; workers remain stopped until hash verification and read-only reconciliation pass.
8. Frontend unit/static build and authenticated local host UI smoke checks pass. No GUI automation without separate explicit permission.
9. Security review confirms no secrets in plugin DB, browser, manifests, fixtures or logs; no public sharing or destructive API surface.

## Primary sources

- [Kitsu plugin development](https://dev.kitsu.cloud/kitsu-plugins/development.html)
- [Kitsu plugin installation and uninstall guide](https://dev.kitsu.cloud/kitsu-plugins/installation.html)
- [Kitsu Web `v1.0.70` package lock](https://github.com/cgwire/kitsu/blob/v1.0.70/package-lock.json)
- [Zou `v1.0.94` plugin lifecycle utility](https://github.com/cgwire/zou/blob/v1.0.94/zou/app/utils/plugins.py)
- [Zou `v1.0.94` plugin install/uninstall service](https://github.com/cgwire/zou/blob/v1.0.94/zou/app/services/plugins_service.py)
- [Zou `v1.0.94` plugin Alembic environment](https://github.com/cgwire/zou/blob/v1.0.94/zou/app/utils/plugin_alembic_template/env.py)
- [Zou `v1.0.94` OutputFile model](https://github.com/cgwire/zou/blob/v1.0.94/zou/app/models/output_file.py)
- [Zou `v1.0.94` WorkingFile model](https://github.com/cgwire/zou/blob/v1.0.94/zou/app/models/working_file.py)
- [Google Drive `files.generateIds`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/generateIds)
- [Google Drive create files and folders with pre-generated IDs](https://developers.google.com/workspace/drive/api/guides/create-file)
- [Google Drive Shared Drive support](https://developers.google.com/workspace/drive/api/guides/enable-shareddrives)
- [Google Drive revision retention](https://developers.google.com/workspace/drive/api/guides/manage-revisions)
- [Google Drive permissions and sharing](https://developers.google.com/workspace/drive/api/guides/manage-sharing)
- [Kitsu client JS upstream](https://github.com/cgwire/kitsu-client-js)
