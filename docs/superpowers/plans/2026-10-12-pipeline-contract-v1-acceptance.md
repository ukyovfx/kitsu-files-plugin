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
| Authenticated KitsuSync management API endpoints | BLOCKED | The contract says bearer service authentication and an actor header but does not define issuer, audience, key distribution/rotation or verified claim semantics. No verifier is invented or exposed unauthenticated. Additionally, Zou’s official plugin route registration mounts beneath its plugin prefix; exact `/api/integrations/v1` routing requires a reviewed reverse-proxy mapping or host-supported route registration. |
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

1. KitsuSync’s `OperatorCommand` OpenAPI component contains `null` in its `required` array. JSON Schema/OpenAPI requires each `required` item to be a string property name. Proposed smallest KitsuSync amendment: use `required: ["command"]`; keep the optional `reason` property optional. This branch does not edit KitsuSync.
2. The service bearer scheme lacks issuer, audience, token lifetime, key source/rotation, and claim requirements. Publish those before enabling the Plugin API verifier.
3. KitsuSync’s server base path is `/api/integrations/v1`; Zou plugin routes are registered inside the plugin route namespace. Agree on a reverse-proxy mapping or an official plugin route registration mechanism before exposing the contract endpoints.

## Migration and release notes

- `vxf_0009` extends `vxf_0008`, advances plugin schema/worker compatibility to epoch/build 8, and adds only plugin-prefixed tables.
- Downgrade refuses when any desired configuration, snapshot, outbox, or package-delivery rows exist. The safe recovery is a consistent database/plugin/artifact snapshot restore, not a destructive downgrade.
- This branch is a protocol/schema foundation, not an integrated production pipeline. All gates marked NOT TESTED or BLOCKED above remain open.
