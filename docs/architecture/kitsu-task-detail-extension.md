# Kitsu Task Detail Files Extension Feasibility

Status: local Kitsu hook and plugin-side Task context are implemented and passed authenticated browser UAT on a pinned disposable host. The Kitsu source branch remains unpublished and is not upstream accepted. Human visual UAT is still required.

## Pinned source examined

The source lock records Kitsu `v1.0.70` and Zou `v1.0.94`. The Kitsu field in `environment/host-lock.yaml` is an annotated tag object; its dereferenced `v1.0.70` source commit is `2f76bb0b53edd60e43afe634c49ba664ba5c38ca`. The pinned Zou source commit is `f5fc34c367a39d58d7741ab2790a5f349c312175`.

At that exact Kitsu source:

- [`TaskInfo.vue`](https://github.com/cgwire/kitsu/blob/2f76bb0b53edd60e43afe634c49ba664ba5c38ca/src/components/sides/TaskInfo.vue) owns the active Shot/Asset Task detail panel and receives the selected Task. It has no extension slot for the active Task view.
- [`Plugin.vue`](https://github.com/cgwire/kitsu/blob/2f76bb0b53edd60e43afe634c49ba664ba5c38ca/src/components/pages/Plugin.vue) already provides a same-origin iframe for project plugin frontends.
- [`user.js`](https://github.com/cgwire/kitsu/blob/2f76bb0b53edd60e43afe634c49ba664ba5c38ca/src/store/modules/user.js) exposes project-enabled plugin metadata (`frontend_project_enabled`).

The production-scoped Files page is therefore not proof of Task-detail integration. Injecting buttons into rendered DOM is not a supported extension point and is rejected.

## Local upstream-compatible hook proposal

The isolated Kitsu source branch `codex/task-detail-files-extension` contains commit `c2a3b99576a23bdfaaf6bebe0e99f80b43ba17c7` against the dereferenced Kitsu `v1.0.70` tag. It adds a generic Project Extensions tab to the existing Task detail pane. The tab enumerates only Kitsu's project-enabled frontend plugins, reuses the existing same-origin `/api/plugins/<plugin_id>/frontend/` route, and mounts the chosen plugin in an iframe. It passes `production_id`, `task_id`, `entity_id`, `entity_type`, `task_type_id`, theme and locale as context hints. The iframe key includes plugin ID, Production ID and Task ID; changing Task resets the panel and replaces its iframe. Kitsu core has no VFX Files-specific condition or Zou database field change.

These query IDs are hints, never authorization. The plugin resolves the Task and owning Production from Zou on every request, verifies the requested Production/Task scope, and rechecks current user access. A missing, stale, or contradictory scope fails closed. The Kitsu patch has not been published upstream. It is built into a local candidate image labeled `c2a3b99576a23bdfaaf6bebe0e99f80b43ba17c7` and exercised only by the `issue5-1016` synthetic UAT unit.

## Plugin-side behavior now supported

The Files frontend parses Task/Entity context and passes the Task ID to the existing Production overview API. The API already resolves Task ownership and authorizes the resulting Production/Task scope. Upload and Publish panels can run in fixed-Task mode without asking the artist to select a second Task. Task-bound asynchronous reads are fenced so a response from the previous Task cannot overwrite the newly selected Task view. Folder bindings and valid provider links are shown in Task context.

The plugin's existing Working upload/history/download, Publish/QC/Approval, authorized folder browser and Handoff panels are mounted through the Task-scoped iframe context. The Task context removes manual Task reselection. The user-visible `Open in Google Drive` links use only provider IDs returned by the authorized, root-checked Task listing; a link does not grant Drive access or change sharing. Authenticated Playwright on the pinned disposable host verified the actual Task drawer, Task and Production switching, Artist A upload, Artist A and Artist B byte-verified downloads, Publish/QC completion, Supervisor exact Publish approval, and Artist A denial on a different Production. The browser preview is `frontend/test-results/issue5-task-detail-files.png`; screenshots and this synthetic browser run are not human visual acceptance.

## Option A UX alignment (current draft)

The embedded Task mode is the artist workflow. When a Kitsu Task ID is present, the Plugin shows the selected entity and Task Type context, omits the Production dashboard and overview cards, and supplies the selected Task to Working, Output Package delivery, Publish and Handoff workflows. The source Task is not reselected in the embedded panel. The Production-level Plugin frontend now acts as an administration gateway and does not fetch or display artist file operations without Task context; its settings link depends on the native Drive settings route from PR #11.

The normal Kitsu Project-plugin page and the Task extension currently share Kitsu's `frontend_project_enabled` discovery flag. The generic Task-detail tab therefore still depends on that host metadata and does not itself hide the project-page navigation item from artists. Upstream Kitsu needs a generic Task-extension discovery capability separate from the Project-page capability, or an equivalent role-aware host route, before the standalone Project page can be made admin-only without hiding the Task tab. The current local hook is unpublished. Do not claim the UX is available on stock Kitsu until that host change is reviewed, adopted, and visually accepted.

The Task view's Share / Received Files surface remains package-version based. It does not yet provide a general original Drive file-or-folder reference picker. Do not imply that the current package delivery flow covers every Share use case or that it changes Drive ACLs.

## Existing Task-scoped operation surface at plugin baseline `cc6fd4dc06293f48978e048208877bc900399af8`

- `GET /api/plugins/vfx_files/tasks/{task_id}/storage-objects?parent_id=<id>&page_token=<token>` authenticates the Kitsu actor, resolves the Task and Production, checks read permission, verifies the selected parent remains below the configured root, and returns the authorized page. The browser's Drive open links are derived from these returned IDs.
- Working snapshots have resumable upload and exact Working-file download routes. Output Packages can be registered from listed Task objects; verified package file downloads use the existing package download route. Publish, QC state, approval/revocation and exact artifact links remain in their existing API paths.
- The Task-scoped `GET .../shared-folders` endpoint reads configured Task folder bindings. `POST .../shared-folders` can queue a Task-folder job only for plugin-owned template configuration; it explicitly rejects when KitsuSync owns the Production storage configuration. The plugin frontend has no KitsuSync provisioning adapter, so the Task panel does not expose folder creation while KitsuSync is the owner.
- The current backend/frontend expose no Task-scoped rename, copy, move, delete, sharing or trash route/contract. Those controls stay unavailable. Drive access still depends on the authenticated user's Google permissions.

## Department Handoff adapter contract

The Task Files view reuses the existing Handoff API and supplies the Kitsu-selected source Task through the iframe context. Department targets are returned by `GET /api/plugins/vfx_files/tasks/{task_id}/publish-options` as `department_targets` entries containing `task_id`, `task_type_name`, and `department_id` (alongside eligible person recipients and Publish families). The create request is `POST /api/plugins/vfx_files/publishes/{publish_id}/handoffs` with `Idempotency-Key` and JSON `{ "recipient_department_id": "<department UUID>", "recipient_task_id": "<receiving Task UUID>", "recipient_person_id": "<optional Person UUID>" }`. The response contains `id`, `state`, `publish_id`, `recipient_department_id`, `recipient_task_id`, and `recipient_person_id`. The service resolves source/target Task, Production, Department membership and permissions server-side. This Task iframe adapter does not change the parallel Issue #6 service, migration or shared Handoff UI files.

## Current integration boundary (2026-10-13)

The KitsuSync v1 contract is now represented by Draft PR #237 and its coordinated service-bearer/route work by Draft PR #238 (HEAD `fd75ebdce80e108814c5e477400dc73537115a04`). These are open drafts, not accepted or deployed APIs. The management API base is `/api/plugins/vfx_files/integrations/v1`; the signed Plugin-to-KitsuSync event intake remains a separate route and trust direction. The agreed draft identity profile uses a short-lived RS256 service JWT verified through KitsuSync JWKS, exact issuer/audience and Production/operation scopes. The Plugin has protocol validation and durable schema foundations, but does not yet expose that authenticated management API, verify service JWTs, or transmit its durable outbox. No Discord delivery belongs in this Plugin.

Until the two draft PRs are accepted and the KitsuSync consumer is deployed, the contract is a coordinated implementation target only. Do not add a fallback unauthenticated alias or treat the in-memory fake provider/outbox as production integration.
