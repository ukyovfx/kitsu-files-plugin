# Kitsu Files Pipeline Acceptance — PR #12

Provider: Files Plugin PR #12, source base `707abf640ff3434e97f42d10c15ea6270df6ef7f` (current working tree contains uncommitted changes; they are not part of that SHA yet).

Consumer contract: KitsuSync PR #239 at `c63de68159a37a7d61da1c31f1991c0aa8df0990`.

| Gate | Status | Evidence / limitation |
|---|---|---|
| KitsuSync template selection → deterministic Plugin materialization | PARTIAL | Existing Plugin code resolves the published catalog selection, validates scope/overrides, binds the verified root as the Production root, persists the derived execution snapshot with the desired config revision, and fences Preview/Apply/Worker. Current-state source inspection only in this pass; Docker-backed regression could not run. `future_only` and `adopt_existing` remain fail-closed. |
| Snapshot → Preview → Apply → separate Worker → FakeStorage | REUSED PASS / NOT RE-RUN | Existing PR #11 evidence is retained; it was not redesigned or repeated in this pass. No new integrated run was possible because the Docker Desktop Linux Engine became unavailable. |
| Output Package HTTP 400 / valid Output Type | BLOCKED | The Package request requires a real Kitsu Output Type. The revised host integration test now obtains an ID from `output-package-options`, checks an invalid ID returns the explicit 400, and submits a valid Working-backed request. The test was not run on this working-tree delta because the pinned host/runner was unavailable. |
| Working revision → Output Package provenance | BLOCKED | The API now verifies every selected source object belongs to the selected completed Working revision. A regression case rejects an unrelated but valid FakeStorage object. The PostgreSQL test was not executed on this delta. |
| Output Package worker copy, manifest, QC, readback, reload and download | BLOCKED | A pinned-host test composes Working upload, separate Worker processes, package registration, verification/QC, provider copy, persisted metadata and verified download. It requires the disposable Zou/PostgreSQL test runner; not run on this delta. |
| Upload provider filename preservation | PARTIAL | The upload worker now passes the reserved `file_name` to the StorageAdapter; the earlier pinned-host regression run showed the copied object as `unnamed` before that fix and passed after it. That prior result predates the current source-validation addition and is not a fresh run for this working tree. |
| Production-wide Output Package Task chooser and Working source selection | PASS (fixture browser only) | Playwright drives the UI, selects a Task and prior Working revision, registers one image-sequence package and asserts the complete request. The fixture does not prove live API/PostgreSQL/Worker behavior. |
| Working upload retry and Handoff response UI | PASS (fixture browser only) | Two Playwright tests cover deterministic upload-part retry and Needs Fix feedback / Accepted refresh persistence. |
| Production overview task metadata and Task authorization | NOT TESTED | Overview now provides entity, Task Type and Task display labels, and filters Task choices through current Task read authorization. A new pinned-host regression covers assigned versus unassigned Tasks and the task-scoped 403; it could not run because Docker was unavailable. |
| Output Package `file_delivery.*` producer/API/UI | NOT IMPLEMENTED | KitsuSync v1 accepts these events, but the current Plugin does not have a wired authenticated Output Package delivery API or canonical producer. The untracked service prototype is not integrated or included in the packaged runtime. No synthetic receiver result is claimed. |
| Existing Working / Publish / QC / Approval / Handoff regressions | NOT RE-RUN | Previous accepted evidence remains unchanged; this pass did not complete a fresh installed-page run. |
| Native Kitsu Task detail extension | BLOCKED | Kitsu 1.0.70 has no TaskInfo extension slot. The Production Files page remains the current UI; no core patch is claimed installed. |
| Frontend Vitest | PASS | `npm test`: 20 passed across 3 files. |
| Frontend browser fixtures | PASS | `npx playwright test browser-tests/files-workflows.pw.js --workers=1`: 3 passed. These are fixture-backed UI tests, not integrated E2E. |
| Frontend production build | PASS | `npm run build` completed; packaged `plugin/frontend/dist` output was regenerated. |
| Installed-page Kitsu login → API → PostgreSQL → Worker E2E | BLOCKED / NOT TESTED | Docker reported that the Docker Desktop Linux Engine was unavailable. No current-source image was built, no seeder was run, and no installed-page workflow is claimed. |
| GitHub Actions | BLOCKED (runner startup) | PR #12 check runs fail within 2–6 seconds and every job reports `steps: []`; no code step executed. This is runner/infrastructure failure evidence, not a code result. |
| Python syntax compilation | PASS | `python -m compileall -q plugin tests/packages/test_output_package_host_integration.py` completed successfully. |
| Diff whitespace check | PASS | `git diff --check` passed after normalizing the generated HTML line endings. |
| Real Google Drive, Production, Discord | NOT TESTED | No real Drive/Production access or Discord delivery was attempted. |

## Current evidence boundary

The current changes include the pinned host regression test and API/Worker fixes, but the production package workflow has not been rerun after those changes. Fixture-browser success, syntax compilation, and a frontend build do not substitute for the required PostgreSQL → API → separate Worker → FakeStorage proof. The Docker runtime must be restored before that gate can be closed.
