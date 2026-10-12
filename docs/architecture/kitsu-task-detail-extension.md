# Kitsu Task Detail Files Extension Feasibility

Status: proposal and plugin-side context support; no Kitsu/Zou core source is modified here.

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

## Independent future UI

KitsuSync remains the owner of Drive connection, authorized roots, shared template configuration and notification routing. VFX Files owns immutable Working/Publish/Output Package metadata and Task handoff receipts. A KitsuSync integration API has not been published in the inspected local contract surface. The plugin must use an internal fake/outbox contract until both projects publish and agree a versioned, authenticated, idempotent contract; it must not call KitsuSync internals or send Discord messages directly.
