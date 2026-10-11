# VFX Studio Kitsu Files Plugin Design

**Status:** Design approved in conversation; awaiting spec review before implementation planning.
**Date:** 2026-10-11

## Intent and constraints

Build an upgrade-resilient Kitsu production files extension on official Kitsu Web and Zou. The extension adds a dedicated production-level Files page, durable file/folder workflows, versioned publishes and QC, and task handoff tracking. It must not change Kitsu Web or Zou core, the existing Task detail drawer, or the review interface. Discord delivery remains in KitsuSync. No live installation, Production change, production migration, destructive Drive operation, merge, or release is in scope.

Stable Kitsu UUIDs and Google Drive file/folder IDs are identity. Names are display/path hints only. Google credentials are optional for development; fake storage must cover every workflow. Nuke integration and Japanese name-order customization are out of scope.

## Options considered

1. **Zou plugin plus separate worker (selected):** use Zou's plugin system for an embedded production page, authenticated API routes, and plugin-owned tables/migrations. A separate worker polls plugin job rows and performs storage/QC/publish operations. This matches the official extension surface and isolates long-running work from Zou web workers. It adds a separately deployed process and shared-DB coordination.
2. **Zou plugin with in-process background work:** fewer deployment components, but job execution and recovery depend on Zou process lifecycle and worker topology. Rejected for durable transfers and reconciliation.
3. **External standalone application:** strong process isolation, but loses Kitsu's native production menu and requires separate authentication and production-context plumbing. Rejected for the requested dedicated plugin page.

## System shape

```text
Kitsu production menu
  └─ embedded Vue 3 + Vite Files page
      └─ authenticated Zou plugin routes (/api/plugins/vfx-files/...)
          ├─ plugin-owned PostgreSQL tables and Alembic revisions
          ├─ Kitsu file-reference adapter (WorkingFile / OutputFile)
          └─ durable job table ← separate worker polls and reconciles
                                  └─ StorageAdapter
                                      ├─ Fake adapter (default in tests/dev)
                                      └─ Google Drive adapter (My Drive + Shared Drives)
```

The plugin frontend uses the official production-menu context (`production_id`, `episode_id`) and the documented static frontend constraints (`base: './'`, hash routing). It does not add controls to the task drawer or review UI.

The Zou plugin owns all extension tables; names use `plugin_vfx_files_` prefixes. It does not alter Kitsu/Zou core models or database schema. API resources require JWT authentication and enforce project/task authorization on every object access. The Kitsu file-reference adapter is the only layer allowed to interact with the existing file models/services. Its contract is covered by compatibility tests against the pinned local Zou host.

The worker shares the plugin package and Zou application environment so it can load plugin configuration and models, but runs as its own process. It consumes jobs with transactional claims, leases, attempt counts, and idempotency keys. Network transfers happen outside DB transactions. Job completion is recorded only after the adapter confirms the external object and the worker verifies its size and locally computed SHA-256. Lost or ambiguous responses trigger lookup/reconciliation by stable Drive ID or application metadata before retrying; they do not mark a transfer complete.

## Plugin data model

- **FolderBinding:** production, episode/sequence/entity/task Kitsu UUIDs as applicable; parent binding; Drive kind, drive ID, folder ID, and last verified state. Unique constraints on the Kitsu scope and on `(drive_id, folder_id)`. Folder names are not keys.
- **Job:** operation type, idempotency key, Kitsu scope IDs, payload reference, state (`queued`, `running`, `retryable`, `succeeded`, `failed`), lease, attempt count, next retry, and sanitized error class. Duplicate event/job keys are rejected by a unique constraint.
- **Publish:** task ID, reserved integer version, state, source WorkingFile ID when present, OutputFile ID when linked, manifest/QC references, immutable artifact Drive IDs, content hash/size, author and timestamps. `(task_id, version)` is unique; reservation occurs under a DB row lock.
- **Task pointers:** separate references for Latest Published and Latest Approved. Pinned dependency records point to explicit publish IDs and never follow either latest pointer implicitly.
- **Handoff:** task ID, publish ID, sender/recipient IDs, state (`pending`, `accepted`, `needs_fix`), response/comment, and append-only transition history. Accept/Needs Fix never rewrites the publish or changes the source artifact.

Use plugin-owned UUID primary keys and store Kitsu UUIDs as stable external references. Relationships to core Kitsu data are checked through the compatibility adapter rather than by changing core tables.

## Workflows

### Folder provisioning

An authorized Kitsu event or explicit production-page action creates an idempotent provisioning job. The worker resolves or creates each folder through the configured storage adapter using Kitsu IDs as metadata and persists discovered Drive IDs. On a timeout, it searches/reconciles by the stable metadata before creating again. It never deletes or renames external folders automatically. A periodic reconciliation detects missing or mismatched external IDs and queues repair work.

### Publish and QC

1. The API validates authorization and creates a publish reservation with a per-task version allocated under a DB lock.
2. A durable job transfers the source to a new version-specific artifact location; it never overwrites an existing published artifact.
3. The worker verifies the external object, size, and SHA-256 before recording transfer success.
4. A manifest records Kitsu IDs, version, source identity/hash, artifact IDs/hashes, QC results, timestamps, and tool metadata. QC failures remain visible and cannot advance Latest Approved.
5. The Kitsu file-reference adapter creates/links an OutputFile only after the artifact is verified; the source WorkingFile link is preserved when applicable.
6. Latest Published advances only for a completed publish. Latest Approved advances only through an authorized approval action. Pinned dependencies remain bound to their selected publish IDs.

All external artifacts are write-once from this application: each publish gets new Drive files and IDs, and normal workflows never update or delete them. For Drive binary revisions, the adapter may request `keepRevisionForever` where appropriate, but the product's immutability guarantee comes from never overwriting/deleting the published artifact and recording/verifying its ID and hash. Google Drive's retained-revision behavior is not treated as a WORM guarantee.

### Task handoff

An authorized user creates a handoff against one exact publish. The recipient can mark it Accepted or Needs Fix, with optional feedback. Transitions are idempotent and audited. A Needs Fix transition does not mutate Kitsu's global task status or the current review interface in the first release; any status mapping can be added later through an explicit, tested Kitsu adapter.

## Storage adapter

The interface covers folder lookup/creation, resumable upload, metadata lookup, content verification, and capability reporting. It accepts stable external IDs and idempotency keys, returns provider IDs and observed metadata, and never exposes credentials to the frontend or logs.

The Google Drive adapter supports My Drive and Shared Drives. Shared Drive calls use the required support/listing parameters and treat the Drive ID as part of the identity. OAuth credentials and target root IDs are supplied only through external local/deployment configuration; absent credentials disable the adapter cleanly. Automated destructive operations are not implemented. The fake adapter simulates success, duplicate requests, timeouts, partial transfers, mismatched hashes, permission failures, and rate limits.

## Migrations, upgrade, and rollback

Migrations are additive and plugin-scoped. Each revision documents its data impact and has a tested downgrade for an empty synthetic database. A downgrade that would discard plugin data must fail closed; production rollback uses the previous plugin code only when its schema compatibility is proven, otherwise it uses a forward repair migration. Zou's documented uninstall path does not drop plugin tables, so uninstall is not a rollback mechanism and table removal is a separate data-retention decision.

Before any target-server install, confirm its Kitsu Web and Zou versions and test the exact pair in an isolated environment. The current upstream candidate baseline is Kitsu Web `1.0.70` and Zou `1.0.94` (both published 2026-10-07); official sources reviewed do not publish a compatible-version pairing matrix. The deployed VFX Studio versions and configuration remain unknown. The prototype must not install or migrate against that server.

## Authentication, security, and operations

- Every route requires JWT and checks permissions against the resolved production/task before reading or mutating plugin data.
- Validate UUIDs and request schemas; use explicit allowlists for job types and state transitions.
- Store no Google tokens in plugin tables, manifests, fixtures, or logs. Secret injection is external and optional.
- Bound upload size, retry rate, job concurrency, and provider error detail. Logs include job IDs and sanitized error classes, not file content, tokens, or raw responses.
- Worker uses leases and conditional state transitions so concurrent workers cannot complete the same job twice.
- No plugin code sends Discord notifications.

## Local validation plan

Use a completely isolated local environment pinned to the recorded Kitsu/Zou baseline, an isolated PostgreSQL test DB, and synthetic fixtures. Do not reuse Production/Staging DBs, credentials, Drive folders, browser profiles, or KitsuSync services. Validation covers:

- Plugin registration, frontend packaging, production context, auth and task/project authorization.
- Fresh migration, upgrade migration, tested safe downgrade, and refusal to discard populated plugin data.
- Fake adapter success/failure and reconciliation; optional Google adapter contract tests with mocked HTTP and no real credentials.
- Duplicate events, retries after timeouts, concurrent job claims, concurrent version reservation, and separately maintained latest/approved/pinned pointers.
- Accepted / Needs Fix transition authorization and audit history.
- Frontend component/unit checks and static build. Browser GUI automation and any authenticated live browser inspection remain out of scope without separate permission.

## Compatibility and rollback matrix

| Surface | Current verified state | Design target | Rollback/compatibility handling |
|---|---|---|---|
| Kitsu Web | Deployed version unknown | Upstream `1.0.70` | Production page is a documented plugin frontend; test iframe/context against exact target before install |
| Zou API/plugin host | Deployed version unknown | Upstream `1.0.94` | Use documented plugin routes/models/migrations; pin and test exact host version; do not modify core |
| Plugin DB | No Production migration performed | Additive `plugin_vfx_files_*` schema | Test upgrade/downgrade locally; refuse destructive downgrade when rows exist; forward-fix if older code cannot safely read schema |
| Google Drive | No credentials or external calls | Optional My Drive + Shared Drives adapter | Fake adapter is default; never overwrite/delete; reconcile ambiguous outcomes by external IDs and hashes |
| Kitsu WorkingFile/OutputFile | Present in current upstream Zou source | Link when semantically appropriate | Isolate integration in adapter; create OutputFile only after verified transfer; test against the exact Zou baseline |
| KitsuSync / Discord | Separate lifecycle | No change | Notifications stay in KitsuSync; no shared release or deployment step |

## Open gates

1. Review and approve this written design before implementation planning.
2. Identify the VFX Studio server's non-secret configuration source and verify deployed Kitsu/Zou versions through the authorized read-only path. If unavailable, retain `UNKNOWN` and continue local-only.
3. Resolve API-version compatibility with a local isolated test pair; latest tags alone do not establish compatibility.
4. Define a least-privilege identity for background Kitsu file references and the storage root/Shared Drive at a later deployment boundary. No real credentials are needed for the local prototype.
5. Review the worker service lifecycle and deployment packaging separately before any installation. No live installation, DB migration, Production change, merge, or release is approved by this design.

## Sources

- Kitsu plugin development: https://dev.kitsu.cloud/kitsu-plugins/development.html
- Kitsu plugin installation and uninstall behavior: https://dev.kitsu.cloud/kitsu-plugins/installation.html
- Kitsu release history: https://github.com/cgwire/kitsu/releases
- Zou package release history: https://pypi.org/project/zou/
- Zou WorkingFile: https://github.com/cgwire/zou/blob/main/zou/app/models/working_file.py
- Zou OutputFile: https://github.com/cgwire/zou/blob/main/zou/app/models/output_file.py
- Google Drive Shared Drive support: https://developers.google.com/workspace/drive/api/guides/enable-shareddrives
- Google Drive revisions and retention: https://developers.google.com/workspace/drive/api/guides/manage-revisions
- Google Drive custom properties: https://developers.google.com/workspace/drive/api/guides/properties
