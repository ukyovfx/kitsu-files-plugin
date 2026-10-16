# Legacy KitsuSync Drive configuration import

The Files Plugin is the intended sole per-Production authority for Drive
connection, verified root, templates, storage configuration, and activation.
Existing `KitsuSyncDesiredConfiguration` records remain legacy source data; they
must not be auto-promoted or deleted.

`plugin.integrations.legacy_import.preview_legacy_import` is a read-only
preflight. It compares the stored Production, connection, root, execution
template, and encrypted credential state. It does not decrypt credentials,
call Google Drive, modify configuration, enqueue work, or enable activation.
Even a `ready_for_explicit_review` result is only evidence for the next
administrator-confirmed step. It reports that root identity matches stored
records; it does not claim fresh Google verification.

A future import action must run under the current Kitsu Production-admin
authorization and CSRF protections, lock the Production configuration, compare
the reviewed source revision/epoch again, preserve all existing roots,
credentials, folder bindings, jobs, and history, and atomically fence the old
activation epoch. It must leave the imported configuration inactive and
require fresh Plugin activation. Any changed source revision, revoked or
unavailable credential, connection/root/template mismatch, or uncertain
in-flight work must abort without mutation. No Drive file move/copy, ACL edit,
or history deletion is permitted.

Standalone root selection remains constrained to roots the server-side
`drive.file` grant can already verify (including Plugin-created roots) until a
web-compatible Picker authorization path and My Drive identity binding are
reviewed. The desktop/mobile `trigger_onepick` guidance is not treated as web
Picker compatibility.
