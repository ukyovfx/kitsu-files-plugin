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
| Output Package manifest validation | PASS | Local pure tests cover stable object IDs, SHA/size metadata, explicit frame ranges, duplicate frames and gap reporting. |
| Output Package Task API and Files UI | NOT TESTED | Task-scoped reservation/list/options/page/download routes and an immutable package panel are implemented, but no installed pinned-host browser run has exercised them. |
| Output Package worker copy and provider readback | NOT TESTED | A fenced `verify_output_package` handler copies to reserved immutable IDs, reads back SHA-256 and records QC; the only real Zou/PostgreSQL integration test is skipped locally because pinned `zou` is unavailable. |
| Output Package migration vxf_0008 | NOT TESTED | Static migration checks pass; PostgreSQL upgrade/re-upgrade and populated-data rehearsal have not run. Downgrade refuses when package rows exist; supported recovery is snapshot restore. |
| Output Package QC and latest pointer | PARTIAL | QC evidence and a separate Latest Working pointer are persisted by the package worker. Native Zou OutputFile revision creation, package Publish/Approval and Latest Published/Approved/Final Delivery advancement are not implemented in this slice. |
| Package multi-Task delivery / handoff | NOT TESTED | Existing Working Publish handoffs remain separate; an Output Package cannot yet be delivered to multiple recipient Tasks. |
| KitsuSync event delivery contract | BLOCKED | No versioned public API contract is available. A Protocol plus in-memory FakeConnectionProvider/FakeEventOutbox is contract-only and is not durable or connected to package delivery. No KitsuSync files changed. |
| KitsuSync or live Google Drive / Production | NOT TESTED | Prohibited for this task; no external connection attempted. |
| Frontend Vitest | PASS | Current branch: 18 tests across 3 files. |
| Python focused tests | PASS / SKIP | Current focused run: 38 passed, 1 skipped. The skip is the host-only package API/worker integration test because local Python lacks the pinned Zou runtime. |
| Full local pytest collection | BLOCKED | `pytest -q` cannot collect three existing SQLAlchemy-dependent suites because the local Python environment lacks SQLAlchemy; 12 other host-only modules skip. Run them inside the pinned disposable host. |
| Python syntax compilation | PASS | Current branch: `.venv\\Scripts\\python.exe -m compileall -q plugin tests`. |
| Frontend production build | PASS | Current branch Vite production build; generated packaged frontend assets are included. |
| Frontend production dependency audit | PASS | `npm audit --omit=dev`: zero reported vulnerabilities. This does not audit Python host dependencies. |
| Full pinned-host API/PostgreSQL/Worker/installed-page E2E | NOT TESTED | Existing PR #4/#8 evidence predates this package code and cannot prove this integrated slice. |

## Current feature boundary

This stacked branch adds Task-scoped Output Package registration, bounded file listing/download routes, immutable provider-copy/readback worker logic, sequence QC evidence, separate package pointer state, and a Task-context frontend panel. It remains an incomplete feature slice: no native Zou OutputFile reconciliation, Publish/Approval linkage, package recipient handoffs, full Kitsu folder reconciliation, official Kitsu TaskInfo hook, pinned-host E2E, PostgreSQL migration rehearsal, real Google Drive verification, or live KitsuSync delivery is proven here.
