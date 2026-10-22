# Personal Google account link (M3) — design as built

Scope: link / check / unlink a Kitsu user's **own** Google account, and a per-Production allow/block switch.
Out of scope (M4+): Google Picker, importing files, any use of the grant to touch Drive content.

## Two separate OAuth identities
| | Production OAuth (unchanged) | Personal OAuth (new) |
|---|---|---|
| Owner | the Production (admin-authorized account) | one Kitsu user |
| Tables | `drive_credential`, `drive_authorization_intent` | `google_user_credential` (PK `actor_id`), `google_user_authorization_intent` |
| Used for | shared-drive storage writes/reads/deletes | (M4) reading files the user picks |
| Tokens | server only, encrypted | server only, encrypted (AES-GCM, AAD binds actor + Google subject) |
| To browser | never | never — only state (`active`, `reauth_required`, …) and a 4-character account hint |

They share only the OAuth client settings, `CredentialKeyring` (and its key rotation, now extended to personal rows), the
fixed callback URL (dispatch is by the single-use state) and the Google endpoints. A personal grant is never passed to
Production storage code: `PersonalDriveReader` is GET-only, is not a `StorageAdapter`, and `resolve_storage_adapter`
refuses it. `drive.file` can include write capability at the Google API level; the Plugin refuses to use it.

## Ownership vs permission
The grant belongs to the user (not to a Production). Each Production has `personal_google_policy` (`allowed` default /
`blocked`) in `production_output_setting`. `blocked` rejects starting a link, completing one that began earlier, and
`open_personal_source` (the M4 boundary, re-checked on every call: active member + policy allowed + active grant).
Blocking never deletes an existing grant; a user can always unlink (`POST /me/google-account/disconnect`).

## Flow and checks
start (`POST …/personal-google/authorizations`: Kitsu membership, CSRF/Origin, Idempotency-Key, policy) → Google
(state + PKCE S256, scope exactly `openid drive.file`, offline) → fixed callback → state consumed before provider I/O,
single use, 10-minute expiry → token exchange with `code_verifier` → scope set must equal exactly the requested scopes →
OIDC `sub` read server-to-server → one Google account per Kitsu user and one Kitsu user per Google account → encrypted
store. A different signed-in Kitsu user hitting the callback is refused before the state is consumed
(`account_mismatch`). Google-side revocation shows up on refresh/`check` as `reauth_required`. Unlink: disable locally,
revoke at Google, erase only after Google confirms (`revoke_pending` is retryable).

## Routes
`GET …/productions/<id>/personal-google` (own status + policy), `POST …/personal-google/authorizations`,
`POST …/personal-google/check`, `POST /me/google-account/disconnect`, `GET|POST …/personal-google-policy` (admin write).

## Database
Migration `vxf_0022` (schema epoch 21, additive): two tables + `personal_google_policy` column. It follows PR #19's
`vxf_0021`. The unmerged prototype's own `vxf_0021`/`vxf_0022` were **not** taken (different content, same IDs); renumber at
merge time if another migration lands first.

## Not verified (needs a dedicated Google test environment and explicit approval)
Real consent screen, `drive.file` behavior with Picker-selected files, the real token/refresh/revoke responses, whether
the same OAuth client ID is required for Picker and server access, external/personal accounts, the Kitsu UI inside the
real iframe (the Playwright MCP was not available in this run).
