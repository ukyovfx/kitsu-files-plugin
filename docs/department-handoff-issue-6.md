# Department Handoff — Issue #6

This scoped change extends the existing Handoff service and API. Person-to-person Handoffs keep their existing request and response routes.

## Department flow

1. `GET /plugins/vfx_files/tasks/{task_id}/publish-options` returns only same-Production, same-entity destination Tasks whose Task Type is linked to the Production. Each Department destination includes its UUID/name and current eligible assignees.
2. `POST /plugins/vfx_files/publishes/{publish_id}/handoffs` accepts `recipient_department_id`, `recipient_task_id`, optional `recipient_person_id`, and optional `note`. It requires the existing Kitsu cookie, same-origin CSRF proof, `Idempotency-Key`, current source Task authorization, exact Department/Task/Production relationship, and at least one current eligible recipient.
3. `GET /plugins/vfx_files/overview?production_id={id}&task_id={recipient_task_id}` exposes the Handoff only to the sender with source Task access or a currently eligible recipient. The response includes the exact Publish UUID/version/checksum, sender note, current response/download eligibility, response ownership rule, and chronological event records.
4. `GET /plugins/vfx_files/handoffs/{handoff_id}/download` returns the exact verified Publish bytes to the current sender or authorized recipient. It rechecks the Kitsu identity and Task/Department relationship, the currently selected KitsuSync storage connection, the immutable artifact identity, provider-root containment, and SHA-256/byte count. It sets `Cache-Control: no-store` and never creates a Drive ACL.
5. `POST /plugins/vfx_files/handoffs/{handoff_id}/respond` accepts `accepted` or `needs_fix`; Needs Fix requires a reason. Current Production, Department, Task assignment, designated-person and role checks run again on every response. The Handoff row lock serializes competing responses; the first committed authorized response owns the decision and its event records the actor.

Department routing passes an immutable reference to the selected completed Publish. It does not copy files, change Google Drive ACLs, overwrite artifacts, or update Kitsu Task status. An optional accountable Person restricts response to that current authorized member. Otherwise any currently eligible, assigned Department Task member may respond; competing decisions serialize on the Handoff row.

`vxf_0017` adds nullable `sender_note` without rewriting old Handoffs. Downgrade refuses if notes exist; use a consistent snapshot restore rather than dropping recorded notes.

## Verification evidence

| Gate | Result in this change |
|---|---|
| Department/Task/Production and role predicates | PASS — targeted pytest |
| Inbox visibility predicate | PASS — targeted pytest |
| Pinned Kitsu 1.0.70 / Zou 1.0.94 / PostgreSQL API and concurrency suite | PASS — 34 tests: Department HTTP options/create/replay/inbox/download, current membership and Task revocation, Needs Fix history, unauthorized Department, cross-Production denial, no-recipient denial, Lighting → Compositing → Editorial, person-Handoff regression, concurrent creates/responses and migration/compatibility checks |
| `vxf_0017` install and schema epoch | PASS — fresh disposable PostgreSQL install advanced from `vxf_0016` to `vxf_0017`; `plugin_vfx_files_state.schema_epoch=16` |
| Installed Kitsu Department Handoff E2E | PASS — exact API/Worker source `244bf809bd4c9a60acb80dffb214fa2c1ae0abcd`, image ID/manifest `sha256:8851f47f5ed1d64edd3221e69575d2d93cae2580cdd0810ba1719677328a556f`; headless Playwright used normal Kitsu login and Production → Assets → VFX Files, then Working w001/w002, Publish, separate Supervisor approval, Department handoff, recipient download, Needs Fix history, refresh, and concurrent responses (`201` and `409`) on the installed Kitsu/Zou page |
| Runtime image / host identity | PASS — Kitsu 1.0.70 / Zou 1.0.94 candidate, Zou migration `b5d2e8f3a1c7`, plugin `vxf_0017`, `schema_epoch=16`; API, PostgreSQL, Redis and egress guard healthy in project `vfx-files-uat-issue6dept-ui2`; Worker invoked as a separate one-shot Compose process |
| Fixture-browser acceptance | PARTIAL — fixture run 4 passed; its 4 opt-in installed-host cases were skipped. The installed-host E2E above is a separate run and supplies the missing Department scenario evidence. |
| Frontend Vitest / build / npm audit | PASS — 21 tests; Vite build succeeded; 0 audit findings |
| GitHub Actions on PR #15 | BLOCKED — all three checks failed before any job steps (steps=[]); no code check executed in those jobs |
| Live Production, Staging, Google Drive or Discord | NOT TESTED — out of scope |

The API tests use synthetic identities, Departments, Tasks and Publish records against the pinned PostgreSQL host through Zou's installed plugin package. The full browser run used synthetic Manager, Artist A/B/C and Supervisor identities, plus synthetic same-Production source/recipient Tasks and FakeStorage. It exercised the Department controls from the installed plugin page: destination Department and Task selection, optional accountable Artist, sender note, exact Publish version, recipient Inbox, verified download, Needs Fix feedback and persisted history after refresh. A second handoff omitted the accountable person; two concurrent authorized recipients received statuses `[201, 409]`, leaving one persisted decision. No external Drive/Discord was used.

The KitsuSync desired-configuration mirror in this standalone UAT is an inactive synthetic fixture used only to resolve the FakeStorage root. No KitsuSync server was included and the fixture does not activate synchronization. The UAT harness correction also makes upload readiness configurable independently of whether a Worker service is continuously running.

### Current installed-host identity

- Worktree branch: `codex/department-handoff-issue6`
- Product source used to build API/Worker: `244bf809bd4c9a60acb80dffb214fa2c1ae0abcd`
- API/Worker image ID and manifest digest: `sha256:8851f47f5ed1d64edd3221e69575d2d93cae2580cdd0810ba1719677328a556f`
- Candidate host image: `sha256:d6081829bb56be7cd4523e2fcebdf753a969da90d334b41d858f0eaf436328bc`
- Compose project: `vfx-files-uat-issue6dept-ui2`; exposed Kitsu endpoint is loopback-only at `127.0.0.1:55461`.
- Database schema epoch: `16`; plugin migration `vxf_0017` present.

The latest static/browser script checks passed: Vitest 21/21, frontend production build, Playwright test syntax, UAT Compose contract 24/24, Compose config validation, and seeder Python compilation. An additional API/authz rerun in this continuation completed 8 Handoff transition and 9 authorization tests, but the three Department HTTP test setups errored while acquiring the CSRF fixture; the test fixture teardown then hit a foreign-key error because setup aborted before yielding. A standalone read-only reproduction of the Kitsu CSRF route returned HTTP 200. This harness rerun is therefore recorded as PARTIAL, not a new API PASS or a product defect; the prior pinned-host Department HTTP/PostgreSQL run of 34 tests remains the recorded backend acceptance evidence. The UAT DB is synthetic and was not cleaned around its append-only evidence.

PR #14's `Department Handoff adapter contract` section is incomplete: it omits the optional `note`, replay/status semantics, response ownership, Inbox/history shape, response endpoint and download endpoint. The actual API supports these behaviors; no server-side incompatibility was found. The exact response shapes and constraints were sent to the Task UI implementation thread for adapter alignment. This PR does not edit PR #14 or the combined Task-detail worktree.

`READY_FOR_COMBINED_UI_INTEGRATION`: YES for API adapter integration and this installed-host synthetic Department flow. Human visual review remains separate. Official host dependency-vulnerability, real Google Drive/Discord, Production/Staging, and release gates remain outside this acceptance and are not passed here.
