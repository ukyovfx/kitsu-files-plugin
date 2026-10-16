# Kitsu Task Detail Files Extension Feasibility

Status: Option A is the selected architecture. Plugin-side Task mode is combined with the Drive-authority branch below; the Kitsu hook and Zou metadata support remain unpublished dependencies.

## Option A implementation update (2026-10-17)

- The artist surface is the existing Shot/Asset Task drawer. Kitsu's local `TaskDetailExtensions` hook adds a Task-scoped plugin tab and embeds the Plugin frontend in a same-origin iframe. It passes Production, Task, entity, Task Type, locale, and theme context; these remain hints that the Plugin revalidates against Zou.
- In Task mode, the Plugin frontend removes the standalone Production dashboard, page header, duplicate Task selection, and app footer. The inner sections stay bound to the selected Task. Share and Received Files remain visibly gated until their authenticated reference-sharing contract and receiver support clear the existing security gate.
- The native Production Plugin page opens the Plugin-owned Drive settings panel directly. It is the only configuration surface and is restricted to authorized Production managers. The separate Files dashboard and its return navigation are removed from this candidate.
- The Kitsu hook is local and unpublished at `f22aca711077910388816a4f438a225a52d96240`. Pinned Zou `v1.0.94` currently does not preserve `frontend_task_enabled` or `frontend_project_admin_enabled`; an additive default-false migration and serialization/static-route change is required before installing the Plugin exposes both pages correctly.
- Native installed-host authenticated visual UAT has not been run on this combined candidate. The existing earlier UAT does not cover these exact Plugin, Kitsu, and Zou revisions.

## Pinned source examined

The source lock records Kitsu `v1.0.70` and Zou `v1.0.94`. The Kitsu field in `environment/host-lock.yaml` is an annotated tag object; its dereferenced `v1.0.70` source commit is `2f76bb0b53edd60e43afe634c49ba664ba5c38ca`. The pinned Zou source commit is `f5fc34c367a39d58d7741ab2790a5f349c312175`.

At that exact Kitsu source:

- [`TaskInfo.vue`](https://github.com/cgwire/kitsu/blob/2f76bb0b53edd60e43afe634c49ba664ba5c38ca/src/components/sides/TaskInfo.vue) owns the active Shot/Asset Task detail panel and receives the selected Task. It has no extension slot for the active Task view.
- [`Plugin.vue`](https://github.com/cgwire/kitsu/blob/2f76bb0b53edd60e43afe634c49ba664ba5c38ca/src/components/pages/Plugin.vue) already provides a same-origin iframe for project plugin frontends.
- [`user.js`](https://github.com/cgwire/kitsu/blob/2f76bb0b53edd60e43afe634c49ba664ba5c38ca/src/store/modules/user.js) exposes project-enabled plugin metadata (`frontend_project_enabled`).

The production-scoped Files page is therefore not proof of Task-detail integration. Injecting buttons into rendered DOM is not a supported extension point and is rejected.

## Small upstream-compatible hook proposal

Add a generic Project Extensions panel/tab to the Task detail component. It should enumerate only project-enabled frontend plugins, reuse Kitsu's existing same-origin plugin frontend route, and mount the selected plugin in a sandboxed iframe. Pass the selected Task's stable IDs as query context: `production_id`, `task_id`, `entity_id`, `entity_type`, and `task_type_id`, plus existing theme and locale settings. Key the iframe by plugin ID and Task ID so Task changes destroy old component state. Do not add VFX Files-specific conditions or Zou database fields to Kitsu core.

These query IDs are hints, never authorization. The plugin must resolve the Task and owning Production from Zou on every request, verify all IDs agree, and recheck current user access. A missing, stale, or contradictory context must fail closed. The proposed hook itself needs an upstream review and compatibility test against Kitsu; this repository does not claim that it is installed.

## Plugin-side behavior now supported

The Files frontend parses Task/Entity context and passes the Task ID to the existing Production overview API. The API already resolves Task ownership and authorizes the resulting Production/Task scope. Upload and Publish panels can run in fixed-Task mode without asking the artist to select a second Task. Task-bound asynchronous reads are fenced so a response from the previous Task cannot overwrite the newly selected Task view. Folder bindings and valid provider links are shown in Task context.

This is a plugin-side prerequisite, not native Kitsu UI acceptance. Until an upstream hook is available in the deployed Kitsu, the actual Task detail panel cannot display this view through an official extension point.

## Current integration boundary (2026-10-13)

The KitsuSync v1 contract is now represented by Draft PR #237 and its coordinated service-bearer/route work by Draft PR #238 (HEAD `fd75ebdce80e108814c5e477400dc73537115a04`). These are open drafts, not accepted or deployed APIs. The management API base is `/api/plugins/vfx_files/integrations/v1`; the signed Plugin-to-KitsuSync event intake remains a separate route and trust direction. The agreed draft identity profile uses a short-lived RS256 service JWT verified through KitsuSync JWKS, exact issuer/audience and Production/operation scopes. The Plugin has protocol validation and durable schema foundations, but does not yet expose that authenticated management API, verify service JWTs, or transmit its durable outbox. No Discord delivery belongs in this Plugin.

Until the two draft PRs are accepted and the KitsuSync consumer is deployed, the contract is a coordinated implementation target only. Do not add a fallback unauthenticated alias or treat the in-memory fake provider/outbox as production integration.
