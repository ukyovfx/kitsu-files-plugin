# Google OAuth credential operations

This procedure configures the Plugin as the confidential Google OAuth client. It does not authorize access to any Drive account by itself. Real OAuth use requires an owner-approved isolated Drive root and a separately reviewed deployment.

## Required configuration

Provide the Plugin API and Worker with the same Google OAuth client ID, client secret, exact callback URI, fixed KitsuSync return URI, credential key directory and active key ID. The callback must be registered at:

`https://<plugin-host>/api/plugins/vfx_files/oauth/google/callback`

`VFX_FILES_CREDENTIAL_KEY_DIR` must be an absolute, owner-only directory on a POSIX host. Store one 32-byte key per `<key-id>.key` file, owned by the service account and mode `0600`; the directory must be mode `0700`. Keep this directory outside PostgreSQL backups, container images and the plugin package. The API and Worker service accounts must be able to read it; browser processes and KitsuSync must not.

The OAuth callback uses a short-lived state bound to a KitsuSync actor and Production, PKCE S256, a fixed redirect URI and a single-use database record. OAuth codes and callback query strings must be excluded from reverse-proxy, APM, tracing and access logs. Configure the proxy to redact the callback query; do not log full callback request URLs. Callback responses are `no-store` and `no-referrer`.

## Key rotation

1. Create a new random 32-byte key file with owner-only permissions. Keep the current key file in place.
2. Set `VFX_FILES_CREDENTIAL_ACTIVE_KEY_ID` to the new key ID and `VFX_FILES_CREDENTIAL_PREVIOUS_KEY_ID` to the old key ID for a maintenance process running inside the Zou application context. Keep API and Worker stopped during this single-process key rotation.
3. Call `plugin.integrations.oauth.rotate_configured_encrypted_records(session_factory, app.config, app.extensions)` once. It re-encrypts credentials and pending OAuth verifier/response records in one database transaction. It returns the number of encrypted rows changed and never prints key material.
4. Confirm the command completed, then query only non-secret metadata (`key_id`, row count, state) to confirm all rows use the new key ID. Do not select ciphertext or decrypted fields into logs.
5. Remove the previous-key setting and old key file only after the metadata check and a verified encrypted backup of both the database and key directory. Restart API and Worker and confirm health and credential status.

If rotation fails or the metadata check is incomplete, restore the matching database and key-directory snapshot together. Never remove a key that may still decrypt a live row.

## Reauthorization and revocation

Routine page visits and API operations reuse the encrypted access/refresh grant. The Plugin requests offline access without forcing the Google consent screen. A genuinely invalid or revoked refresh grant disables the connection and requires an explicit new authorization intent. Revocation first disables local use and queues provider revocation; the credential is removed from the database only after a confirmed provider response. Uncertain provider outcomes remain pending for reconciliation.

My Drive ownership is the OIDC `sub` fetched server-to-server for the same grant. Shared Drive identity is the provider-verified Drive ID. Neither value is accepted from a browser field. A successful OAuth callback alone does not enable storage: an authorized root verification is required first.

Never put access tokens, refresh tokens, authorization codes, client secrets, OAuth URLs containing state, callback URLs with query strings, or decrypted credential payloads in logs, tickets, diagnostics or API responses.
