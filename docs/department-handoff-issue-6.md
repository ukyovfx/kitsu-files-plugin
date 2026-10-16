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
| Migration additive/data-preserving source contract | PASS — targeted pytest |
| HTTP/PostgreSQL create, replay, response, revocation, verified download, cross-Production and no-recipient regressions | NOT TESTED — test module skips unless the pinned Zou plugin host is installed |
| Frontend Vitest | PASS — see PR check evidence |
| Frontend production build | PASS — see PR check evidence |
| Live Production, Staging, Google Drive or Discord | NOT TESTED — out of scope |

The API integration tests use synthetic people, Departments, Tasks and Publish records. They require `VFX_FILES_TEST_PLUGIN_FOLDER` and the repository's pinned Kitsu/Zou PostgreSQL test host; they are not fixture-browser or live-server proof.
