# VFX Studio Kitsu Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the isolated VFX Files Zou plugin and fenced worker with durable file workflows, versioned publishes, explicit approvals, folder provisioning, and task handoff.

**Architecture:** Keep all product code in an official Zou plugin with plugin-owned PostgreSQL tables and Alembic revisions. A separate process in the pinned Zou environment claims durable jobs and performs external I/O through a StorageAdapter; native WorkingFile/OutputFile access stays behind one compatibility adapter. The frontend is a standalone Vue/Vite build mounted as the official Production plugin page.

**Tech Stack:** Python in the exact pinned Zou runtime; Flask plugin routes; SQLAlchemy/Alembic; PostgreSQL; pytest; Vue 3 + Vite; Node/npm lockfile; Google Drive API v3 adapter; fake storage adapter. Exact framework/package versions and immutable `kitsu-client-js` commit are locked in Task 1 after the compatibility spike.

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
- Produces `HostLock` fields `kitsu_tag`, `kitsu_commit`, `zou_tag`, `zou_commit`, `python_version`, `node_version`, `npm_version`, `vue_version`, `vite_version`, and `kitsu_client_ref`.
- Produces `createKitsuClient({ apiBase, getBearerToken }): KitsuApiClient` and a host compatibility test that confirms the plugin's JWT/API calls work on the exact isolated host pair.

- [ ] **Step 1: Write failing compatibility and client-auth tests** named `test_candidate_host_pair_loads_plugin_context`, `test_plugin_api_client_uses_bearer_token`, and `test_lock_contains_immutable_refs`; assert the Production context IDs are parsed as untrusted strings and the client never stores a token.
- [ ] **Step 2: Run the focused tests** with `pytest tests/compat -q`; expected: fail because the lock/client harness is absent.
- [ ] **Step 3: Create an isolated host environment** from exact upstream `v1.0.70` and `v1.0.94` refs, record immutable commit SHAs and runtime versions, and test Zou plugin registration/auth route locally. No Production/Staging credentials or browser profile.
- [ ] **Step 4: Pin frontend dependencies** in an npm lockfile and pin `kitsu-client-js` to an immutable commit only if its auth/API path passes; otherwise implement the minimal reviewed API wrapper in `kitsuClient.ts`. Never pin a branch name.
- [ ] **Step 5: Run focused tests** with `pytest tests/compat -q` and `npm --prefix frontend ci && npm --prefix frontend run build`; expected: all pass on the pinned local host.
- [ ] **Step 6: Commit** as `build: pin isolated Kitsu and Zou compatibility host`.

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
