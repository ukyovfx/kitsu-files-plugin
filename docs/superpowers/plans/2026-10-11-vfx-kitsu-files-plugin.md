# VFX Studio Kitsu Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the isolated VFX Files Zou plugin and fenced worker with durable Working File snapshots, shared folder provisioning, versioned publishes, explicit approvals, and task handoff.

**Architecture:** Keep all product code in an official Zou plugin with plugin-owned PostgreSQL tables and Alembic revisions. A separate process in the pinned Zou environment claims durable jobs and performs external I/O through a StorageAdapter; native WorkingFile/OutputFile access stays behind one compatibility adapter. The frontend is a standalone Vue/Vite build mounted as the official Production plugin page. It uses same-origin host cookies with plugin-signed CSRF proof, based on the pinned host configuration and integration tests.

**Tech Stack:** Python in the exact pinned Zou runtime; Flask plugin routes; SQLAlchemy/Alembic; PostgreSQL; pytest; Vue 3 + Vite; Node/npm lockfile; Google Drive API v3 adapter; fake storage adapter. Frontend uses same-origin host cookies without reading/storing tokens, plus a short-lived plugin CSRF proof; this was exercised against the pinned host.

**Spec:** `docs/superpowers/specs/2026-10-11-vfx-kitsu-files-plugin-design.md`

## Global Constraints

- Kitsu Web candidate is `v1.0.70`; Zou candidate is `v1.0.94`; do not call this pair supported until isolated integration tests pass.
- Never install, migrate, uninstall, or test against VFX Studio Production or Staging.
- Never change Kitsu Web/Zou core, the Task detail drawer, review interface, or KitsuSync.
- Never use plugin uninstall as rollback or `force=true` against a populated plugin directory.
- Never create public Drive permissions, overwrite/delete published artifacts, or infer missing objects from names/search alone.
- Use fake storage and synthetic data for local tests; Google credentials remain optional.
- Use stable Kitsu UUIDs and provider IDs for identity; no identity from names.
- Keep full SHA-256 and byte counts in plugin tables; never place secrets in plugin tables, frontend, manifests, fixtures or logs.
- Keep KitsuSync and this extension's release/deployment lifecycles separate.
- Stop before any live install, external credential use, Production migration, merge or release.

## Review Focus

- Lost Drive create/upload response: prove lookup by reserved ID or durable upload session prevents duplicate folders/artifacts; ambiguity blocks retry.
- Authorization revoked after queueing: prove the worker rechecks permission and performs no external mutation.
- Stale worker after lease takeover: prove generation fencing rejects heartbeat/completion from the old worker.
- Zou plugin install failure after Plugin row update or file overlay: prove local restore procedure recovers the snapshot without calling uninstall.
- OutputFile uniqueness with nullable dimensions and late older publish: prove native mapping is reconciled and latest pointers remain monotone.

---

### Task 1: Pin and prove the isolated host compatibility boundary

**Files:**
- Create: `environment/host-lock.yaml`
- Create: `environment/README.md`
- Create: `tests/compat/test_host_compatibility.py`
- Create: `tests/compat/fixtures/kitsu_plugin_context.json`
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/src/kitsuClient.ts`

**Interfaces:**
- Produces `HostLock` fields `kitsu_tag`, `kitsu_commit`, `zou_tag`, `zou_commit`, `official_test_image_digest`, `python_version`, `node_version`, `npm_version`, `vue_version`, and `vite_version`.
- Produces `createKitsuClient({ apiBase, getBearerToken }): KitsuApiClient` and a host compatibility test that confirms the plugin's JWT/API calls work on the exact isolated host pair.

- [ ] **Step 1: Write failing compatibility and client-auth tests** named `test_candidate_host_pair_loads_plugin_context`, `test_plugin_api_client_uses_bearer_token`, and `test_lock_contains_immutable_refs`; assert the Production context IDs are parsed as untrusted strings and the client never stores a token.
- [ ] **Step 2: Run the focused tests** with `pytest tests/compat -q`; expected: fail because the lock/client harness is absent.
- [ ] **Step 3: Create an isolated host environment** from exact upstream `v1.0.70` and `v1.0.94` refs, record immutable commit SHAs and runtime versions, and test Zou plugin registration/auth route locally. No Production/Staging credentials or browser profile.
- [x] **Step 4: Pin frontend dependencies** in an npm lockfile. The client SDK transport was not proven against this host, so use the minimal bearer-only API wrapper; no SDK dependency is included.
- [x] **Step 5: Run focused tests** with `pytest tests/compat -q`, `npm --prefix frontend ci`, `npm --prefix frontend test`, and npm audit; tests pass, with zero production dependency vulnerabilities. The Vite build is deferred until Task 6 adds the application entry point.
- [x] **Step 6: Commit** as `build: pin isolated Kitsu and Zou compatibility host` (`e49b885`).

### Task 2: Plugin shell, auth boundary, schema epoch and migrations

**Files:**
- Create: `plugin/manifest.toml`
- Create: `plugin/routes.py`
- Create: `plugin/authz.py`
- Create: `plugin/models.py`
- Create: `plugin/migrations/versions/0001_plugin_foundation.py`
- Create: `plugin/compat.py`
- Create: `tests/api/test_authz.py`
- Create: `tests/migrations/test_plugin_schema.py`

**Interfaces:**
- `resolve_request_scope(actor_id: UUID, production_id: UUID, task_id: UUID | None) -> ResolvedScope` verifies authoritative Zou relationships and returns `NotFound` for missing/mismatched objects.
- `authorize(actor: Actor, capability: Capability, scope: ResolvedScope) -> None` maps the design permission matrix to verified Zou helpers and fails closed.
- `get_schema_epoch(session) -> int` and `assert_compatible_host(host_version, schema_epoch, worker_build) -> None` gate route and worker startup.

- [ ] **Step 1: Write failing tests** `test_iframe_context_is_not_authority`, `test_task_must_belong_to_requested_production`, `test_unknown_role_denied`, `test_cookie_only_mutation_rejected`, `test_cross_origin_mutation_rejected`, and `test_worker_rejects_schema_epoch_mismatch`.
- [ ] **Step 2: Run** `pytest tests/api/test_authz.py tests/migrations/test_plugin_schema.py -q`; expected: fail on missing routes/models/helpers.
- [ ] **Step 3: Implement** plugin manifest/static route, JWT-only mutation guard, origin/CSRF checks, server-side task/production resolver, permission adapter, schema epoch and additive plugin-owned foundation tables using `plugin_vfx_files_` names.
- [ ] **Step 4: Run tests** against isolated Zou + PostgreSQL; expected: exact allowed/denied outcomes and only plugin-prefixed tables in Alembic diff.
- [ ] **Step 5: Commit** as `feat: add authenticated VFX Files plugin foundation`.

### Task 3: Durable jobs, upload staging, and StorageAdapter / folder provisioning

**Files:**
- Create: `plugin/storage/base.py`
- Create: `plugin/storage/fake.py`
- Create: `plugin/storage/google_drive.py`
- Create: `plugin/uploads/service.py`
- Create: `plugin/jobs/queue.py`
- Create: `worker/main.py`
- Create: `worker/leases.py`
- Create: `plugin/folders/service.py`
- Create: `tests/storage/test_storage_contract.py`
- Create: `tests/uploads/test_upload_sessions.py`
- Create: `tests/jobs/test_worker_fencing.py`
- Create: `tests/folders/test_folder_provisioning.py`

**Interfaces:**
- `StorageAdapter.reserve_id(scope_key: str) -> ReservedProviderId`, `create_folder(parent_id: str, reserved_id: str, correlation: Mapping[str, str]) -> ProviderObject`, `upload(source: BinaryIO, reserved_id: str, size: int, sha256: str, parent_id: str) -> ProviderObject`, `get(file_id: str) -> ProviderObject | None`, `verify(file_id: str, size: int, sha256: str) -> Verification`, and `capabilities(root_id: str) -> StorageCapabilities`.
- `create_upload(actor: Actor, task_id: UUID, size: int, sha256: str) -> UploadSession`; `put_part(session_id: UUID, index: int, content_range: ByteRange, part_sha256: str, body: BinaryIO) -> UploadPart`; `finalize_upload(session_id: UUID) -> Job`.
- `claim_job(worker_id: UUID, now_from_db: bool = True) -> LeasedJob | None`, `heartbeat(job_id: UUID, worker_id: UUID, generation: int) -> bool`, and `complete_job(job_id: UUID, worker_id: UUID, generation: int, result: JobResult) -> bool`.
- Folder identity is unique on `(storage_connection_id, allowed_root_id, canonical_scope_key)`; all columns are non-null.

- [ ] **Step 1: Write failing tests** `test_upload_rejects_size_and_quota_excess`, `test_upload_parts_are_idempotent_and_contiguous`, `test_finalize_verifies_server_hash_and_staging_readback`, `test_expired_cleanup_only_removes_claimed_session_key`, `test_claim_uses_skip_locked_generation`, `test_stale_worker_cannot_complete_after_takeover`, `test_folder_reservation_serializes_concurrent_create`, `test_lost_create_response_uses_reserved_drive_id`, and `test_incomplete_listing_does_not_prove_absence`.
- [ ] **Step 2: Run** the four focused pytest files; expected: fail before implementation.
- [ ] **Step 3: Implement** PostgreSQL transaction boundaries, `FOR UPDATE SKIP LOCKED` leases, DB-clock expiry, generation fencing, bounded retries/rate limits, fake adapter fault injection, upload limits/defaults, durable staging, resumable range/checksum behavior, and Drive ID reservation before create. Include `supportsAllDrives=true`, drive-scoped listing and ancestry/capability checks. Ambiguous outcomes become `reconcile_required`.
- [ ] **Step 4: Run focused tests** with two concurrent PostgreSQL workers and fake storage; expected: one reservation/claim wins, stale completion fails, and no duplicate external create is issued on ambiguous visibility.
- [ ] **Step 5: Commit** as `feat: add durable uploads and fenced storage jobs`.

### Task 3B: Separate shared Drive templates and immutable Working revisions

**Files:**
- Extend: `plugin/models.py`, additive plugin migration, `plugin/resources.py`, worker runtime/handlers, `frontend/src/api/files.js`, Files page/components
- Create: `plugin/working_files/service.py`, `plugin/working_files/worker.py`, `plugin/folders/templates.py`
- Create: `tests/working_files/test_revision_lifecycle.py`, isolated PostgreSQL/API/worker tests and browser E2E

**Interfaces:**
- `reserve_working_revision(task_id, actor_id, base_revision_id, manifest, dependencies, idempotency_key) -> WorkingRevision`; allocates `w###` per Task under a lock and records the base revision.
- `complete_working_revision(revision_id, worker_id, generation, verified_files, manifest) -> bool`; completes only when every declared file is transferred and verified and the captured base still matches Latest Working.
- `download_working_file(actor_id, revision_id, file_id) -> authorized byte stream`; re-resolves Task/Production authorization and streams exact immutable provider bytes.
- Keep local workstation templates and shared Drive templates as separate versioned configurations. Shared template defaults are configurable and contain collaborative inputs, Working, Publish, Review, Handoff, and Delivery roles only; they must not reproduce the Escape local production tree.

- [ ] **Step 1: Add failing tests** for per-Task `w###` uniqueness, stale-base conflicts, same-file immutable retries, complete multi-file manifests, incomplete snapshot refusal, pointer separation, download authorization/integrity, temp/cache default exclusion, and worker recovery after partial/lost-response transfer.
- [ ] **Step 2: Prove red** against plugin-owned PostgreSQL tables and the isolated API before implementation.
- [ ] **Step 3: Implement** additive schema, resumable snapshot grouping, provider-ID reservations, transfer/checksum verification, recovery/reconciliation, native WorkingFile adapter calls, authorized download, and Latest Working transaction. Files UI must list revisions and download each file without relying on mocked API state.
- [ ] **Step 4: Run isolated browser/API/PostgreSQL/worker/FakeStorage acceptance**: Artist A uploads w001 and sees it; Artist B downloads w001, bases w002 on it and uploads a multi-file snapshot; both remain retrievable and Latest Working is w002. Inject concurrent/stale uploads, incomplete files, interruption, ambiguous storage response and permission denial. Test native references against the pinned Zou host.
- [ ] **Step 5: Commit** the verified Working File vertical slice separately from folder template configuration.

### Task 4: Publish numbering, QC/manifests and Kitsu native file bridge

**Files:**
- Create: `plugin/publishes/service.py`
- Create: `plugin/publishes/checkpoints.py`
- Create: `plugin/publishes/manifest.py`
- Create: `plugin/publishes/qc.py`
- Create: `plugin/kitsu_files/adapter.py`
- Create: `tests/publishes/test_publish_numbering.py`
- Create: `tests/publishes/test_checkpoint_recovery.py`
- Create: `tests/publishes/test_output_file_adapter.py`
- Create: `tests/publishes/test_pointer_monotonicity.py`

**Interfaces:**
- `reserve_publish(task_id: UUID, output_family_id: UUID, source_working_file_id: UUID | None, actor_id: UUID) -> Publish` allocates plugin version with row lock + unique constraint.
- `advance_checkpoint(publish_id: UUID, expected: PublishState, next_state: PublishState, evidence: CheckpointEvidence) -> Publish` uses conditional state transition and persisted external IDs/hashes.
- `consume_native_create_attempt(publish_id: UUID, worker_id: UUID, generation: int) -> NativeCreatePermit` transactionally consumes the one-shot permit before side effects; `create_native_output(permit: NativeCreatePermit, publish: Publish) -> NativeOutputLink` invokes the verified in-process Zou service once, with retries disabled, then records the exact ID/revision.
- Any uncertain outcome becomes `native_create_ambiguous`; no automatic worker retry can issue another create. `authorize_native_create_retry(actor: Actor, publish_id: UUID, proof: ReconciliationProof) -> NativeCreatePermit` requires the original worker/request to be terminated and authoritative native absence to be proven, and writes an audit event.
- `advance_latest_published(task_id: UUID, output_family_id: UUID, publish_id: UUID) -> None` only advances to a higher eligible plugin publish version.

- [ ] **Step 1: Write failing tests** `test_concurrent_publish_reservations_are_unique`, `test_publish_version_not_native_revision`, `test_existing_native_output_revisions_are_considered`, `test_outputfile_unique_conflict_is_reconciled`, `test_slow_create_lost_lease_never_invokes_create_twice`, `test_uncertain_native_create_blocks_automatic_retry`, `test_audited_retry_requires_terminated_original_and_authoritative_absence`, `test_checkpoint_recovers_after_each_external_operation`, `test_manifest_hash_covers_exact_bytes`, `test_late_older_publish_does_not_replace_latest`, and `test_failed_qc_cannot_advance_latest`.
- [ ] **Step 2: Run** the three focused pytest files; expected: fail before services exist.
- [ ] **Step 3: Implement** version/family allocation, `reserved → source_ready → artifact_verified → qc_recorded → manifest_verified → native_linked → completed` checkpoints, 64-bit sizes/full SHA-256, canonical exact manifest bytes, QC policy records and the Kitsu adapter. Atomically consume a one-shot native-create permit before the in-process Zou OutputFile call; disable create retries and block ambiguous outcomes for audited reconciliation. Gate native OutputFile `data` marker use on verified Zou support.
- [ ] **Step 4: Run** focused Postgres/fake adapter tests, including injected process death before/after each external operation; expected: replay is idempotent and pointers remain monotone.
- [ ] **Step 5: Commit** as `feat: add versioned publish and native Kitsu file links`.

### Task 5: Approval evidence, dependency pins, handoff and orphan handling

**Files:**
- Create: `plugin/approvals/service.py`
- Create: `plugin/handoffs/service.py`
- Create: `plugin/objects/reconciliation.py`
- Create: `tests/approvals/test_approval_history.py`
- Create: `tests/handoffs/test_handoff_transitions.py`
- Create: `tests/objects/test_orphan_handling.py`

**Interfaces:**
- `approve_publish(actor: Actor, publish_id: UUID, policy_id: str, policy_hash: str) -> ApprovalEvent`; `revoke_approval(actor: Actor, approval_id: UUID, reason: str) -> ApprovalEvent` append events and update only the Latest Approved cache.
- `pin_dependency(task_id: UUID, publish_id: UUID, manifest_hash: str) -> PinnedDependency` stores immutable exact version identity.
- `transition_handoff(actor: Actor, handoff_id: UUID, target: Literal['accepted','needs_fix'], feedback: str | None) -> HandoffEvent` validates sender/recipient policy and idempotency.
- `mark_orphaned(kitsu_object_id: UUID, object_type: str) -> ReconciliationFinding` never deletes or name-relinks.

- [ ] **Step 1: Write failing tests** `test_approval_binds_exact_publish_and_manifest_hash`, `test_approval_and_revocation_are_append_only`, `test_self_approval_denied`, `test_revoking_latest_falls_back_to_prior_active_approval`, `test_pinned_dependency_never_follows_latest`, `test_handoff_accept_and_needs_fix_are_idempotent`, `test_handoff_cannot_change_task_status_or_artifact`, and `test_missing_kitsu_entity_is_orphaned_without_relink`.
- [ ] **Step 2: Run** focused pytest files; expected: fail on missing services.
- [ ] **Step 3: Implement** append-only approval/handoff events, separate pointer derivation, exact publish+manifest references, role checks and audited orphan state.
- [ ] **Step 4: Run tests** with authorization changes between request and worker execution; expected: revoked authorization blocks execution and immutable records remain intact.
- [ ] **Step 5: Commit** as `feat: add approvals and task handoff records`.

### Task 6: Dedicated Production Files page

**Files:**
- Create: `frontend/src/main.ts`
- Create: `frontend/src/App.vue`
- Create: `frontend/src/api/files.ts`
- Create: `frontend/src/components/UploadPanel.vue`
- Create: `frontend/src/components/PublishList.vue`
- Create: `frontend/src/components/HandoffPanel.vue`
- Create: `frontend/src/router.ts`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tests/files-page.test.ts`
- Modify: `plugin/manifest.toml`

**Interfaces:**
- `FilesApi.listProduction(productionId: UUID, episodeId?: UUID): Promise<FilesOverview>`; `FilesApi.createUpload(...)`, `FilesApi.resumeUpload(...)`, `FilesApi.createPublish(...)`, `FilesApi.respondHandoff(...)` use the authenticated client from Task 1.
- `App.vue` receives Production context from the official route but treats it only as a display/filter hint; server responses decide visibility.

- [ ] **Step 1: Write failing component tests** `test_files_page_is_production_scoped`, `test_invalid_context_shows_authorized_error`, `test_upload_progress_resumes_from_server_ranges`, `test_publish_list_separates_latest_published_and_approved`, `test_handoff_buttons_follow_authorized_state`, and `test_disabled_feature_shows_read_only_message`.
- [ ] **Step 2: Run** `npm --prefix frontend test -- --run`; expected: fail before page exists.
- [ ] **Step 3: Implement** dedicated standalone Vue page, static hash routes, `base: './'`, upload resume/status, publish pointers, approval evidence display and Accepted/Needs Fix controls. Do not add code to Kitsu task drawer/review pages.
- [ ] **Step 4: Run** component tests, lint and `npm --prefix frontend run build`; expected: pass with locked dependencies.
- [ ] **Step 5: Commit** as `feat: add dedicated Production Files page`.

### Task 7: Install/upgrade rehearsal, backup/restore and security acceptance

**Files:**
- Create: `ops/disable-files.md`
- Create: `ops/upgrade-rollback-runbook.md`
- Create: `ops/backup-manifest.schema.json`
- Create: `tests/ops/test_plugin_lifecycle_rehearsal.py`
- Create: `tests/ops/test_backup_restore.py`
- Create: `tests/security/test_secrets_and_public_sharing.py`
- Create: `tests/ops/test_mixed_worker_versions.py`

**Interfaces:**
- `create_backup_manifest(host_lock: HostLock, database_snapshot_id: str, plugin_package_sha256: str, staging_snapshot_id: str, artifact_inventory: Sequence[ArtifactEvidence]) -> BackupManifest` contains references/hashes only, no secrets.
- `BackupAdapter.copy_verified(source: BinaryIO, destination_key: str, size: int, sha256: str) -> BackupObject` writes bytes to the configured separate encrypted target and verifies read-back size/hash; the manifest alone is never treated as a backup.
- `validate_restore(manifest: BackupManifest, worker_build: WorkerBuild, host_lock: HostLock) -> RestoreValidation` fails before worker resume on any mismatch.

- [ ] **Step 1: Write failing lifecycle/recovery tests** `test_failed_install_restores_full_snapshot_without_uninstall`, `test_uninstall_can_drop_tables_and_delete_files_in_disposable_fixture`, `test_populated_upgrade_and_restore_preserve_plugin_and_host_rows`, `test_backup_copies_and_reads_back_staged_and_published_bytes`, `test_worker_paused_until_restore_reconciliation_passes`, `test_artifact_hash_mismatch_blocks_publish`, `test_mixed_schema_epoch_worker_refuses_claims`, `test_secrets_never_enter_logs_or_manifests`, and `test_no_public_permission_operations_exist`.
- [ ] **Step 2: Run** focused ops/security tests on a disposable exact-tag host and isolated Postgres; expected: lifecycle hazard is reproduced and restore contract initially fails.
- [ ] **Step 3: Implement** feature gate/drain behavior, backup manifest/coverage checks, full snapshot restore order, read-only external reconciliation and tested failure handling. No real Google account is used; provider behavior is simulated.
- [ ] **Step 4: Run** lifecycle, migration, security and mixed-worker suites; expected: successful isolated rehearsal with workers remaining stopped until verified.
- [ ] **Step 5: Commit** as `test: prove upgrade rollback and restore boundaries`.

### Task 8: Final local validation and implementation handoff

**Files:**
- Modify: `docs/superpowers/specs/2026-10-11-vfx-kitsu-files-plugin-design.md`
- Modify: `docs/superpowers/plans/2026-10-11-vfx-kitsu-files-plugin.md`
- Modify: `docs/superpowers/plans/2026-10-11-vfx-kitsu-files-traceability.md`

**Interfaces:**
- Produces `docs/validation/isolated-host-matrix.md` with exact tags/SHAs, migration outcomes, test commands/results, and explicit unverified Google/live-runtime gates.

- [ ] **Step 1: Run the targeted backend, migration and frontend suites** in the isolated environment; capture the exact host lock and results.
- [ ] **Step 2: Review security and compatibility matrix** against the spec and each traceability row; any gap blocks readiness.
- [ ] **Step 3: Run** `git diff --check` and `git status --short --branch`; expected: no formatting errors or unexpected files.
- [ ] **Step 4: Commit** as `docs: record isolated validation and implementation handoff`.

## Acceptance gates

Implementation is not production-ready until every gate in the design's “Local validation and release gates” section is evidenced. In particular, exact Kitsu/Zou compatibility, Google folder pre-generated ID behavior, Google My Drive/Shared Drive capability behavior, backup of artifact bytes, and any production deployment configuration remain separate verification gates. No plan task authorizes deployment, live migration, real Drive writes, merge, or release.

## Functional acceptance matrix

Use the existing `pytest` + pinned disposable Zou/PostgreSQL host for API, authorization, persistence, migration and worker integration. Reuse Vitest for component/API-client behavior; add only the smallest browser runner needed for isolated browser workflows. Every end-to-end row must seed synthetic Kitsu/Zou records, sign in with a synthetic identity, drive the Production Files page in a fresh test browser context, exercise the real mounted plugin API and database, run the durable worker against FakeStorage, then assert persisted state and recovery behavior. Service mocks or a successful build can support a row but cannot pass it.

| Workflow | Browser/UI action | Host API and permission proof | DB / worker / storage proof | Recovery and persisted result | Evidence status |
| --- | --- | --- | --- | --- | --- |
| Upload, resume and source transfer | Select a Task and file in the installed Files page; refresh after worker completion | Live browser uses Zou cookie auth and CSRF; pinned-host route tests deny missing CSRF, cross-origin requests and unauthorized Task scope | Real Zou/PostgreSQL upload, chunks, durable job and file metadata; separate worker process transfers via disk-backed FakeStorage | Actual worker crash after provider write is recovered by a second fenced worker without a duplicate provider object; browser refresh reads completed state. Browser interruption during upload is only fixture-tested | **PASS** installed-page UI → API → PostgreSQL → worker → FakeStorage → refresh; **PASS** worker crash/recovery and ambiguous provider response integration; **NOT TESTED** real browser interruption/resume and native Google transfer |
| Shared Working revisions | Artist A uploads w001; Artist B downloads it, uploads w002, refreshes, then retrieves both | Two distinct Task-authorized artists use cookie-authenticated plugin API and same-origin CSRF; authorization revocation, concurrent HTTP requests and stale-base conflicts are checked | Real plugin API/PostgreSQL allocate w001 then w002; separate worker processes transfer to persistent FakeStorage, verify provider IDs/size/hash/manifest, advance Latest Working; multi-file snapshots publish only after every file verifies | Installed-page Playwright verifies the two identities, byte-exact downloads and Latest Working after refresh; multi-file worker test kills the process after provider acceptance and recovers the same reserved object | **PASS** installed-page browser → API → PostgreSQL → worker → FakeStorage → refresh/download; **PASS** concurrent/stale-base, duplicate retry, ambiguous response, revocation and interrupted multi-file recovery; **NOT TESTED** live Google transfer and native WorkingFile reconciliation for Working revisions |
| Shared Drive template and folder provisioning | Select Production, import root URL, discover/select tree, map roles and activate; request Shot/Task folders | Production-manager authorization, root capability, cross-root denial, configuration version checks and Kitsu Task membership are validated | PostgreSQL template/config, idempotent job, durable reservations and UUID-keyed bindings; separate worker executes against persistent FakeStorage without copying files | Installed-page Playwright configures and activates the nested template, queues Task provisioning, runs the separate worker, refreshes and verifies persisted valid Drive links | **PASS** installed-page UI → real API → PostgreSQL → worker → FakeStorage → refreshed bindings; **PASS** nested, existing-folder, ambiguous-create, isolation and authorization integration tests; **NOT TESTED** Google API behavior |
| Publish, revision and QC | Select a completed Working revision and Publish from the installed Files page | Output Family configuration requires Production-manager authorization and a ready root-scoped Publish binding; Task publish authorization and idempotency are checked | Real plugin API/PostgreSQL reserves version and durable job; worker verifies source, copies to FakeStorage, records immutable manifest/QC, creates native Zou WorkingFile/OutputFile through established adapters, and advances Latest Published | Installed-page test verifies exact artifact, manifest, QC, native revision, Latest Published and refreshed state. Pinned worker test crashes after provider acceptance, expires the lease and retries against the same object ID | **PASS** installed-page UI → API → PostgreSQL → worker → FakeStorage → native Zou revision → manifest/QC → refresh; **PASS** post-transfer crash recovery; **NOT TESTED** live Google transfer and native-create lost-response injection |
| Approval and dependency pin | Supervisor approves the selected exact completed Publish; pinning remains a separate service workflow | Installed-page E2E denies Artist A and approves from a distinct synthetic Supervisor; approval API binds the exact manifest | Append-only approval advances Latest Approved independently from Latest Published | Browser reload verifies both pointers; PostgreSQL/service tests cover immutable dependency references and approval events | **PASS** installed-page exact-Publish approval and Latest Approved; **PASS** service-level pin tests; **NOT TESTED** browser/API dependency-pin workflow |
| Handoff | Sender creates Handoff from completed Publish; recipient accepts or requests fixes | Real API verifies sender Task permission and recipient assignment | PostgreSQL persists Handoff and append-only event without changing Kitsu Task or artifact | Installed-page test creates and accepts a Handoff across identities, refreshes and verifies persisted state; Needs Fix feedback remains fixture-tested | **PASS** installed-page API → PostgreSQL create/accept and refresh; **PASS** fixture Needs Fix validation; **NOT TESTED** Needs Fix on the installed page against the host |
| Publish crash recovery and replay | Installed Publish action shows progress and persisted result after refresh | Worker claim generation rejects stale completion; the OutputFile one-shot permit is persisted before Zou create | Real workers are killed after FakeStorage persists the artifact and after Zou creates an OutputFile but before plugin reconciliation; retry reuses the artifact and fences native creation into `native_create_ambiguous` without a duplicate | Pinned integration verifies immutable artifact identity, exact native OutputFile count, idle replay, and no Latest Published regression when an older Publish completes late | **PASS** artifact crash/retry, native-create uncertain outcome, fencing, replay and monotone pointer; manual retry still requires audited authoritative absence proof |
| vxf_0006 migration downgrade and restore | Not a browser action; operator procedure is documented separately | Test uses a fresh scratch database, never the pinned host or Production database | Representative Working File and Publish rows survive a pre-downgrade database snapshot; actual vxf_0006 downgrade/re-upgrade was executed on the scratch database | Downgrade removes both added values; re-upgrade does not recover them; restoring the snapshot does | **PASS** destructive data loss and snapshot restore verified; never use vxf_0006 downgrade as a production rollback |
| Failed jobs and resumable operations | Observe queued/completed states in installed Files page | Worker permission checks and API cookie/CSRF/Origin/Task authorization are exercised against pinned Zou | Actual worker process claims and persists transfer; PostgreSQL lease generation fences stale completion | Process exits after provider write, lease expiry is simulated, second process reconciles the reserved ID and completes exactly one object; provider ambiguous-response injection is covered | **PASS** actual upload worker transfer, crash/recovery and ambiguous response with FakeStorage; **NOT TESTED** live revoked-permission race, PostgreSQL outage recovery, and Google API resumable transfer |
| Full backup and restore, including worker hold | Separate disposable host; restore while API and Worker are stopped; open Files page only after state verification | Restored synthetic actor can read its Production; unauthorized Task artist approval attempt returns 403 | Full PostgreSQL dump, plugin code, non-secret settings, host lock, staging, FakeStorage metadata and all object bytes are restored; Working, Publish/QC, Approval, Handoff, native OutputFile, jobs and pointers are compared with source evidence | Pre-marker Worker process refuses before claim; installed page refreshes `w002`, downloads w001/w002 with SHA-256, and reads Publish/Approved pointers; only after validation a manifest-matched one-shot Worker returns idle | **PASS** earlier pinned-host snapshot/restore and readback; **PASS** this turn's separate patched-candidate restore, exact source/restored state report match, all 19 FakeStorage objects and Working/Publish artifacts verified, browser readback/denial passed, Worker held before marker and idle after; **NOT TESTED** encrypted offsite target, live Google reconciliation, or Production recovery |
| Google Drive adapter contract: My Drive and Shared Drive | Not a browser action; synthetic transport exercises provider requests | My Drive owner mismatch and root/write/list/download permission revocation fail closed | Contract tests check owner/root scope, Shared Drive flags, reserved-ID folder creation, upload payload, byte-exact download and SHA-256 readback; lost upload response retry uses the same reserved ID and reconciles verified bytes | 403s are permission failures; uncertain transport outcomes remain ambiguous; no real Drive write is made | **PASS** injected-transport contract suite; **BLOCKED** live My Drive / Shared Drive acceptance pending an authorized isolated root and credentials |
| Security and OSS readiness | Static policy/code review; no Production UI/browser state | Host identity remains the sole authority; same-origin CSRF, role and scope checks are retained | Drive root write capability and provider permission failures are enforced before write; Git history is scanned; npm audit, Python host dependency audit, Bandit, Vitest, build and CycloneDX SBOM workflows are configured | Worker fencing, exact artifact checkpoints, restore hold, and non-destructive Drive contracts remain separate gates | **PASS** Gitleaks history scan (21 commits, no findings), Bandit 1.8.6 (`plugin/`, zero findings), npm audit (0 advisories), 12 Vitest tests, frontend build, and prior 84-component SBOM generation; **PASS** candidate pip-audit (no known vulnerabilities); **FAIL** official locked-host Python audit (one upstream High plus Moderate/Low advisories in cryptography/pyOpenSSL); **NOT TESTED / BLOCKED** GitHub Actions (runs had no steps/logs), external penetration test, and public release provenance |
| Patched local Kitsu/Zou dependency candidate | Exact pinned Kitsu 1.0.70 / Zou 1.0.94 application commits and immutable base image; separate locally built image with packaging-only `fido2` / `pysaml2` pin adjustments | No Production or live credentials; synthetic users and FakeStorage only | `pip check` clean; `pip-audit==2.10.1` reports no known vulnerabilities; full host pytest suite passes | Installed-page browser workflow and second-container Backup/Restore/readback pass; Worker refuses before verification and is idle after the manifest marker | **PASS** candidate dependency compatibility, 100 pytest tests, complete installed-page Working → Publish/QC/native OutputFile → Approval → Handoff flow, separate-candidate restore and Worker hold; **BLOCKED** official host support/lock adoption pending upstream review and an officially released reproducible image |
| Current OSS regression slice | Installed browser, API/provider contract, separate Worker, restore guard and PostgreSQL state comparison | Patched candidate uses the actual installed Files page and synthetic cookie-authenticated users | `tests` executed against candidate Zou/PostgreSQL; Playwright runs against installed plugin routes; `npm test`, build and audit run locally | Candidate source/restored reports compare exactly; historical Working downloads and exact Publish state survive restore | **PASS** 100/100 candidate pytest, full installed-page flow (1 Playwright test), restored-page verification (1 Playwright test), 12/12 Vitest, frontend build and `npm audit --audit-level=moderate` (0); the previous 92-test result remains historical, not this turn's count |
| OSS license and private vulnerability channel | Repository metadata check | GitHub repo is private; Security policy setting is currently disabled | No license file exists although plugin manifest declares MIT; frontend SBOM includes standard permissive licenses and optional MPL-2.0 `lightningcss` packages; no user or contributor rights grant was inferred | `SECURITY.md` tells reporters not to file public issues and requires verified private reporting before public visibility | **BLOCKED** public OSS release pending rights-holder/copyright confirmation, an explicit license file, third-party notice review, and GitHub private vulnerability reporting setup |
| SBOM and release artifact provenance | CI generates CycloneDX SBOMs for npm and the pinned host Python inventory | No deployment/release workflow is authorized or present in this change | Local frontend SBOM has 84 components; pinned-host CycloneDX rendering was verified on a single package; full CI generation and artifact retention are not run yet | No signed or published release artifacts exist to verify | **PASS** local SBOM generation samples; **NOT TESTED** GitHub SBOM artifact upload, release signing, provenance/attestation, and artifact readback |
| Production host compatibility | No Production connection or mutation | Compare only when sanitized read-only Production evidence is supplied | Pinned Kitsu 1.0.70 / Zou 1.0.94 acceptance does not establish the Production version, proxy, schema, worker or auth configuration | Require sanitized Kitsu/Zou version/build/commit/image digest, plugin/migration state, PostgreSQL version/schema, proxy/upload limits, auth/cookie/origin behavior, worker/staging configuration and synthetic permission fixtures | **BLOCKED** no authorized read-only Production inventory is available; no Production connection was attempted |

Status vocabulary: **PASS** means the stated evidence passed at its named layer; **FAIL** means a test ran and demonstrated incorrect behavior; **BLOCKED** means a required integration boundary is not implemented or an external prerequisite is unavailable; **NOT TESTED** means no evidence was gathered for that specific behavior. A PASS at one layer never upgrades an end-to-end row whose other layers are blocked or untested.

Acceptance is row-specific: record browser action, HTTP outcome, synthetic actor/role, relevant table state, worker lease generation, adapter call ledger, recovery action and final persisted state. Keep local workstation templates and shared Drive templates separate. Keep Google credentials optional and do not claim Google API execution from FakeStorage; validate My Drive and Shared Drive behavior separately before deployment. Working File acceptance requires a real isolated two-artist browser/API/PostgreSQL/worker/FakeStorage flow; fixture-only UI tests cannot pass it.
