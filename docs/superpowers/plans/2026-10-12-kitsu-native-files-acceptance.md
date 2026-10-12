# Kitsu Task Files Integration Acceptance — Current Branch

Base: PR #4 branch at `16ef67d4b23028e1c3b4c6bf52a39f2238d3e81e`.

| Gate | Status | Evidence / limitation |
|---|---|---|
| Task context parsing from official-plugin iframe query | PASS | Vitest verifies Production/Task/Entity/Task Type context strings; they remain untrusted hints. |
| Task-scoped overview API call | PASS | Vitest verifies `task_id` reaches the existing overview route. Server ownership authorization is existing code and requires pinned-host regression to re-prove. |
| Automatic fixed Task on Working/Publish panels | PASS | Vue implementation removes Task chooser in Task context and resets context-specific state. |
| Stale asynchronous response fencing | PASS | Monotonic request-generation tests plus panel local request fences. Actual dynamic Kitsu Task panel switch NOT TESTED. |
| Actual official Kitsu Task detail extension host | BLOCKED | Kitsu 1.0.70 `TaskInfo.vue` has no active Task extension slot. Proposal is documented; no Kitsu source change or installed patched Kitsu build is included. |
| Task folder binding display/create from Files context | NOT TESTED | Plugin uses existing Task shared-folder endpoints; Task-scoped API/worker integration not run against fresh host after changes. |
| Asset folder template role | PASS | Fake hierarchy and plan unit tests verify stable Asset UUID identity and nested Task folders. Zou/FakeStorage worker route integration NOT TESTED. |
| Nested Working/Publish/Output template descendants | PASS | Planning helper now preserves all descendants below configured Task role using template folder IDs. Provider side effects NOT TESTED. |
| Department/task recipient guard predicates | PASS | Unit tests cover project, entity, Task Type Department, current memberships and designated person constraints. |
| Department Handoff API/migration/concurrent response | NOT TESTED | Additive migration and API path exist; no PostgreSQL migration rehearsal or pinned Zou membership revocation/race test run in this branch. |
| Output Package / frame manifest metadata | PASS | Additive model/migration and pure manifest validation tests cover SHA, explicit frames, duplicate IDs/numbers and gaps. |
| Output Package API, worker transfer, QC, approval and pointers | NOT TESTED | Metadata foundation only; no creation API/worker pipeline or browser workflow in this increment. Existing Publish records are unchanged. |
| KitsuSync event delivery contract | BLOCKED | No versioned public API contract found in inspected local KitsuSync repo/docs; only internal/fake boundary may proceed. No KitsuSync files changed. |
| KitsuSync or live Google Drive / Production | NOT TESTED | Prohibited for this task; no external connection attempted. |
| Frontend Vitest | PASS | 17 tests on this branch. |
| Python focused tests | PASS / SKIPPED | 31 passed, 1 host-only handoff service module skipped because local Python 3.13 lacks Zou/SQLAlchemy. Covers authz helpers, template discovery/plan, frame manifest, recipient predicates and migration source checks; not the pinned Zou runtime. |
| Python syntax compilation | PASS | `python -m compileall -q plugin tests`. |
| Frontend production build | PASS | Vite build on this branch; generated packaged frontend assets included. |
| Full pinned-host API/PostgreSQL/Worker/installed-page E2E | NOT TESTED | Existing PR #4 evidence predates this new code; it is not carried forward as a current PASS. |

## Current feature boundary

The branch prepares the plugin frontend for a Task iframe context, adds Asset template path planning and Department-to-Task handoff support, and adds Output Package metadata/frame-manifest foundations. It does not ship an official Kitsu TaskInfo hook, a complete Package API/worker/review pipeline, Google Drive reconciliation, or live KitsuSync delivery. Those remain separate release gates.
