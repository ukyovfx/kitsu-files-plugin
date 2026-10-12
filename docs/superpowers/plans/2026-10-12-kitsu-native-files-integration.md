# Kitsu Native Files and Delivery Integration Plan

> **For agentic workers:** Use inline execution and keep every implementation increment independently testable. User explicitly authorized autonomous safe implementation.

**Goal:** Deliver Task-context Files, ID-bound folder reconciliation, immutable output packages, and Department/Task-type distribution without replacing Kitsu's native review model.

**Architecture:** Extend the current plugin frontend so it can run in a Task-scoped context passed by Kitsu. The pinned Kitsu Web source has no task-detail plugin slot; document a small upstream-compatible TaskInfo extension host that embeds existing project plugin frontends in a same-origin iframe, without DOM injection or Zou changes. Keep plugin-owned durable records and adapters in this repository, with KitsuSync and Google Drive connectors behind contracts and Fake implementations until their external contracts/credentials are approved.

**Tech Stack:** Vue 3/Vitest/Playwright, Flask/Zou plugins, SQLAlchemy/Alembic/PostgreSQL, durable fenced Worker, FakeStorage.

**Spec:** GitHub Issues #5 and #6; `docs/superpowers/specs/2026-10-11-vfx-kitsu-files-plugin-design.md`.

## Global Constraints

- Base this feature branch on Draft PR #4's exact `16ef67d4b23028e1c3b4c6bf52a39f2238d3e81e`; do not include Draft PR #7 direct-install changes.
- Preserve existing Working, Publish, QC, Approval, native OutputFile and Handoff contracts unless a new plugin-owned migration and regression tests extend them compatibly.
- Resolve all Production, Entity, Task, Task Type, Department and Drive identity from stable IDs and server-side Zou records; fail closed on cross-scope IDs.
- Keep local synthetic/FakeStorage evidence distinct from KitsuSync, real Drive and live-host integration.
- Do not change Kitsu/Zou source in this repository, connect to Production/Drive/SSH, install, migrate, merge or release.

## Review Focus

- Task or Production changes while requests are in flight must discard stale response state and clear previous artifact links.
- Folder rename/move/delete/ownership changes must quarantine and require review rather than rebind by name or auto-repair.
- Multi-file sequence metadata must reject invalid ranges, duplicates, missing frames and digest mismatches without publishing partial packages.
- Department recipient eligibility must be re-evaluated on every read/transition against current Production membership, Department membership and destination Task Type.
- KitsuSync events must be outboxed idempotently; no direct Discord or undocumented KitsuSync call is allowed.

## Implementation Tasks

### Task 1: Task-detail extension boundary

**Files:** `docs/architecture/kitsu-task-detail-extension.md`, `frontend/src/kitsuClient.js`, `frontend/src/App.vue`, focused frontend tests.

- [x] Record exact pinned-source findings and the generic Kitsu TaskInfo iframe-hook proposal; verify the proposal does not require DOM selectors, plugin-specific Kitsu changes, or Zou core changes.
- [x] Add Task/entity context parsing and Task-scoped overview calls; suppress manual Task reselection when host context is present.
- [x] Add response-generation cancellation and clear old Task state when the context changes.
- [x] Verify parsing and request generation with Vitest; real patched Kitsu UI stays NOT TESTED because no disposable Kitsu source build was changed.

### Task 2: Department and receiving-Task Handoffs

**Files:** plugin Handoff model/migration/service/resources, frontend delivery UI, synthetic API/integration tests.

- [x] Add an additive schema for optional destination Department, destination Task and accountable Person while preserving existing person-targeted rows.
- [x] Resolve the target Department through its Task Type and verify same Production/entity; check current Production, Department and Task assignment before response, then recheck membership inside the locked transition transaction.
- [x] Add pending-recipient uniqueness guards, idempotent response transitions and preserve existing immutable HandoffEvent audit history.
- [ ] Test the full PostgreSQL/Zou revocation, empty Department, cross-Production API and concurrent response scenarios against the disposable pinned host.

### Task 3: Output Packages and frame manifests

**Files:** plugin-owned package/file models and migration, manifest/QC services, StorageAdapter pagination/resume seams, focused tests.

- [x] Add Output Package/version/file models separately from native OutputFile revisions and independent latest pointer columns.
- [x] Add manifest validation for stable provider object IDs, checksums, explicit frame ranges, missing frames and optional format/color metadata.
- [ ] Add package reservation/API, resumable transfer worker, provider readback, QC/approval integration and pointer advancement. This is not implemented in this increment.
- [ ] Test low-res and final EXR sequence fixtures, gaps, v001/v002 allocation, partial transfer restart and immutable downloads end-to-end.

### Task 4: ID-bound folder sync and KitsuSync adapter contract

**Files:** plugin folder mapping/reconciliation models and migrations, dry-run API/service, adapter protocol/Fake, tests and documentation.

- [ ] Represent Production/Episode/Sequence/Shot/Asset/Task/Task Type hierarchy and template version by stable Kitsu UUID plus authorized provider IDs. This increment adds Asset role planning but not the full hierarchy model.
- [ ] Separate initial dry-run plan from continuous reconciliation. Reuse stable bindings; quarantine unknown provider changes; never infer identity from names or delete/move unmanaged objects.
- [ ] Validate root ancestry and ownership before each mutation. Fence concurrent jobs and recover ambiguous provider replies by reserved ID/readback.
- [ ] Define a versioned idempotent KitsuSync event interface with Fake implementation; keep the live client blocked until a published KitsuSync API contract is verified.

### Task 5: Integrated acceptance and review

- [ ] Run focused tests for each increment, then relevant pinned-host/PostgreSQL suites, frontend build and browser tests.
- [ ] Mark live Kitsu TaskInfo, KitsuSync API, Google Drive and server Human UAT as NOT TESTED/BLOCKED where no isolated authorized target exists.
- [ ] Commit reviewable increments and open Draft PR(s) stacked on PR #4; never modify PR #7 or treat either as merged.

## Decision Ledger

- Ruling: Use a TaskInfo iframe extension host over existing project plugin frontends — the pinned Kitsu source already uses same-origin iframes for project plugins, while TaskInfo has no plugin slot; this avoids brittle DOM injection and minimizes Zou changes. Cost if wrong: Kitsu maintainers may reject the hook, so the implementation remains pending upstream adoption.
- Ruling: Keep KitsuSync integration at a local contract/Fake boundary — no published API contract was found in the supplied/current KitsuSync checkout, which also has unrelated dirty work; this avoids inventing cross-repository behavior. Cost if wrong: notifications stay queued until the owners publish a compatible contract.
