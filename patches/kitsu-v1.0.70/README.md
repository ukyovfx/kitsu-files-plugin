# Kitsu 1.0.70 Task Plugin Extension Hook

This patch is separate from the VFX Files Plugin package and does not modify the Kitsu repository. It adds a generic, opt-in Task detail section for Kitsu project-enabled plugins, embedding the plugin's existing same-origin frontend route and passing the selected Production, Entity, Task, and Task Type IDs as context hints.

Pinned source: `cgwire/kitsu` commit `d8f420069a6bf24d98c5557e418538eff6173cea` (`1.0.70`).

Apply only to that source revision:

```sh
git checkout d8f420069a6bf24d98c5557e418538eff6173cea
git apply --check /path/to/vfx-kitsu-files-plugin/patches/kitsu-v1.0.70/task-plugin-iframe.patch
git apply /path/to/vfx-kitsu-files-plugin/patches/kitsu-v1.0.70/task-plugin-iframe.patch
```

Validation performed for this patch:

- Exact pinned Kitsu source was checked out in a temporary detached clone.
- `git apply --check --reverse` succeeded against the modified tree.
- The patch was reversed, `git apply --check` succeeded against the clean pinned tree, and the patch reapplied successfully.
- Kitsu build/test validation is NOT TESTED: `npm ci` rejected the installed Node 24.14.1 because a locked dependency requires `^24.15.0`.
- No Kitsu PR, installation, deployment, or upstream write was performed. The upstream repository is not a writable worktree in this task.

The query parameters are untrusted routing hints only. Plugin APIs must continue to resolve the current authenticated user and authorize every Task and Production server-side.
