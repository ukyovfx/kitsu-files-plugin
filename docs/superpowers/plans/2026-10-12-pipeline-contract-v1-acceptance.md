# Pipeline Contract v1 — Implementation Acceptance

Base: PR #9 (`33ccf28a4b1ca5a9fa8402864a2c4433d1cd55ab`), stacked as a separate draft branch.

| Gate | Status | Current branch evidence |
|---|---|---|
| KitsuSync contract source | PASS | Read exact KitsuSync PR #237 commit `55a396bb59c96551852610d9eb5e5819d1838d49`; protocol fixture and event schemas are the authority. KitsuSync repository was read-only. |
| Hierarchy JSON canonicalization | PASS | RFC 8785-style canonical output and published `sync-snapshot-page-0` digest match in `tests/integrations/test_kitsusync_v1_protocol.py`. |
| Snapshot page bounds, duplicate handling, parent checks, completeness and full digest | PASS | Focused unit cases cover 500-object page bound behavior, replay conflict, sorted unique IDs, parent closure, page checksum, page count and complete hierarchy digest. |
| Durable desired-config/snapshot/outbox schema | NOT TESTED | Additive migration `vxf_0009` and corresponding ORM records are present. PostgreSQL application/re-upgrade, populated-data, lock contention and downgrade guard have not run because the local Docker daemon is unavailable. |
| Transactional snapshot persistence helper | NOT TESTED | `plugin/integrations/snapshots.py` stages snapshots under revision/source ETag and verifies pages before complete; no database integration execution. |
| Signed Plugin→KitsuSync event headers | PASS | HMAC-SHA256 test checks exact newline-delimited input and `sha256=` header form from PR #237. |
| Event outbox append idempotency | PASS / NOT TESTED | In-memory repository tests confirm accepted → identical duplicate and conflicting event identity rejection. PostgreSQL `ON CONFLICT` persistence and worker transmission/retry are NOT TESTED / not yet wired to the Worker. |
| Contract event validation | PASS | Synthetic folder and two-recipient file-delivery shapes pass. Unknown top-level fields (including link fields), duplicate event IDs, unsupported event types, malformed identity, and oversized records fail closed. |
| Authenticated KitsuSync management API endpoints | BLOCKED | Coordinated Draft PR #238 defines the short-lived RS256/JWKS service bearer profile and exact Plugin route `/api/plugins/vfx_files/integrations/v1`. The Plugin verifier and authenticated handlers are not implemented, and the coordinating KitsuSync drafts are still open. Do not expose a fallback alias. |
| Desired config revision/ETag API and activation lifecycle | NOT TESTED | Persistent schema exists; authenticated HTTP handlers and config validation/activation are not implemented. |
| OAuth authorization callback and credential lifecycle | NOT TESTED | No OAuth endpoints or credential vault are added. Existing Google Drive operations remain disabled unless separately configured; no real OAuth or Drive request was made. |
| Folder sync preview/apply/retry/reconcile | NOT TESTED | Existing Task folder provisioning remains unchanged. The new snapshot verifier is not yet wired to preview/apply jobs or hierarchy reconciliation. |
| Output Package native OutputFile/Publish/Approval flow | NOT TESTED | PR #9 base behavior remains unchanged; this branch does not add native OutputFile linkage, package Publish/Approval, or Latest Published/Approved integration. |
| Multi-recipient persisted delivery schema | NOT TESTED | Migration and models add independent package recipient rows and immutable history. Creation/respond API, authorization, KitsuSync outbox emission, worker delivery, and FX/Layout DB E2E are not implemented or run. |
| Task detail iframe extension patch | PARTIAL | Reproducible Kitsu 1.0.70 patch uses project-enabled plugin metadata and the existing same-origin `/api/plugins/{id}/frontend/` endpoint; passes `git apply --check` against pinned source commit `d8f420069a6bf24d98c5557e418538eff6173cea`. Kitsu upstream repository is not writable/modified. Kitsu `npm ci` is BLOCKED because installed Node 24.14.1 fails the dependency engine requirement `^24.15.0`; no build/browser validation is claimed. |
| Python focused checks | PASS | `30 passed` across protocol, schema-text, and authorization tests; Python compileall passed. No PostgreSQL-backed test was included. |
| Pinned Zou/API/Worker/FakeStorage E2E | NOT TESTED | Docker Desktop Linux engine pipe is unavailable. No pinned host was started and no changes were made to any existing host or volume. |
| Existing PR #4/#7/#8/#9 | PASS | GitHub read confirmed all four remain OPEN/Draft at their previously recorded heads; no changes were made to them. |
| Production / real Drive | NOT TESTED | Not accessed, per scope. |

## Protocol findings requiring coordination

1. Earlier OpenAPI/auth/route findings are tracked by KitsuSync Draft PR #238 (HEAD `fd75ebdce80e108814c5e477400dc73537115a04`): the service bearer profile uses RS256/JWKS, exact issuer/audience, bounded token lifetime, Production/operation scopes, and the Plugin management base `/api/plugins/vfx_files/integrations/v1`. This PR is still draft and not deployed; the Plugin has not implemented the verifier or handlers.
2. Plugin-to-KitsuSync signed event intake remains a separate route and trust direction from management authentication. The Plugin has HMAC framing/validation foundations but no event transmission worker or Discord integration.

## Migration and release notes

- `vxf_0009` extends `vxf_0008`, advances plugin schema/worker compatibility to epoch/build 8, and adds only plugin-prefixed tables.
- Downgrade refuses when any desired configuration, snapshot, outbox, or package-delivery rows exist. The safe recovery is a consistent database/plugin/artifact snapshot restore, not a destructive downgrade.
- This branch is a protocol/schema foundation, not an integrated production pipeline. All gates marked NOT TESTED or BLOCKED above remain open.

## Confirmed UX / Drive follow-up (2026-10-13)

This section records the narrow implementation added on `codex/confirmed-ux-drive-sync` (stacked on PR #10); it does not replace the original acceptance evidence above.

| Gate | Status | Evidence / limitation |
|---|---|---|
| Working custom file-type labels | PARTIAL | Task Working upload accepts a bounded custom label and stores it in the existing category field on each immutable file record. No schema migration or mutation of prior rows. Labels are scoped by their Task/revision records, not a separately managed Task-Type registry. Extension allowlists and per-type destination-folder mapping remain NOT TESTED / unimplemented. |
| Working and Publish display labels | PARTIAL | UI presents Working and Publish with independent `v###` labels; API/database Working representation remains `w###` and existing Publish `v###` is unchanged. Migration and historical data rename are intentionally absent. |
| Initial Shot / Asset templates | PASS (discovery model) | The shared template setup already offers Shot and Asset mappings separately and discovers user-selected trees under the authorized root. It does not seed fixed studio layouts or add Reference / Turntable / Preview folders. No production or Drive data was used. |
| Drive metadata on tab open | PARTIAL | The Files page calls the authorized folder listing on open and revalidates it every five minutes while mounted. Google adapter now requests provider `modifiedTime` and `md5Checksum`; UI shows the last successful listing time and visible provider modified times. This is a scoped listing observation, not persisted synchronization history and does not prove SHA-256 or detect inaccessible IDs outside the listed folder. |
| Scheduled server-side Drive reconciliation | NOT TESTED | No durable recurring reconciliation scheduler, persisted changed/missing/access-failure observation table, or job worker was added. A browser interval is not a substitute for a server schedule. |
| Latest Working / formal Publish distinction | PASS (existing data model) | The API maintains the Latest Working pointer separately from Latest Published and Latest Approved Publish pointers. A newer Drive modification timestamp does not mutate or infer any pointer. |
| Cross-Task-Type destination links and version selection | PARTIAL | Existing Publish Handoff API supports explicit receiving Tasks/Departments and keeps independent receipts. The UI defaults to the most recent completed Publish in the returned descending list and permits choosing older completed Publishes. A same-Shot configured Task-Type destination list with permission-checked Google Drive links and receiving-task pin/use controls is not implemented. |
| Native Kitsu Task detail Files entry | BLOCKED | Pinned Kitsu 1.0.70 TaskInfo has no official active-Task extension slot. Plugin-side Task context remains available, and Production → Assets → VFX Files remains the supported installed page. The generic same-origin iframe hook is still an upstream Kitsu patch proposal, not installed or browser-accepted. DOM injection remains rejected. |
| KitsuSync v1 management integration | BLOCKED | Coordinated Draft PR #237/#238 remain unmerged. Target management route is `/api/plugins/vfx_files/integrations/v1` with the coordinated RS256/JWKS short-lived bearer profile; the Plugin still lacks that verifier and authenticated route implementation. Separate Plugin→KitsuSync signed event delivery is also not wired. No incompatible alias or Discord sender was added. |
