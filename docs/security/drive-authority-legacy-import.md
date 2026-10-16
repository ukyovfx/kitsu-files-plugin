# Legacy KitsuSync Drive configuration import

The Files Plugin is the only new Drive configuration authority per Production. Existing `KitsuSyncDesiredConfiguration` records remain the active legacy source until a manager explicitly imports them. Migration never silently changes ownership.

## Preview

`plugin.integrations.legacy_import.preview_legacy_import` is a read-only preflight. It compares the legacy Production, connection, verified-root identity, execution template, activation epoch, and encrypted credential state. It does not decrypt credentials, call Google Drive, modify configuration, enqueue work, or enable activation. A `ready_for_explicit_review` result means only that a manager may review a separately confirmed import; it does not prove fresh provider access.

## Explicit import

The native Kitsu Production import endpoint requires the current Kitsu manager/admin authorization, same-origin CSRF protections, a revision-matched confirmation, and an idempotency key. The Plugin locks the Production, reruns the preview, checks the source revision, active encrypted credential and existing template, and performs a fresh server-side My Drive root verification. Unsupported or stale state aborts without ownership transfer.

The transaction transfers only the durable authority/configuration record, advances the configuration revision and activation epoch, sets activation disabled, and disables/revision-fences the existing execution template. It preserves the legacy configuration row, OAuth credential, root, folder bindings, jobs, and history. No file is copied or moved and no ACL is changed. An immutable `ProductionDriveAuthorityEvent` is inserted in the same transaction. It records only the actor, operation type, source revision, new configuration revision, new activation epoch, idempotency key, and timestamp; it contains no credentials or configuration payload. The transaction fails if the audit row cannot be written.

## Rollback plan

Before import, the operator must have a consistent database and storage snapshot and review outstanding/in-flight sync work. Keep the worker stopped during rollback. Do not downgrade the Plugin schema or manually switch the authority owner. If rollback is required, restore the pre-import consistent snapshot as a unit and verify the authority row, legacy settings, credential state, template version, bindings, jobs, history, and activation epoch before reopening the worker. If the original snapshot is unavailable or the post-import state is uncertain, keep activation disabled and stop for review.

## Standalone root limit

Native root selection is limited to roots the server-side `drive.file` grant can already verify, including Plugin-created roots. Existing roots that are not app-visible remain blocked until a web-compatible Picker authorization path and My Drive identity binding are reviewed. The desktop/mobile `trigger_onepick` guidance is not treated as web Picker compatibility.
