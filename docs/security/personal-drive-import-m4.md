# Personal Google Drive → Output version (M4) — design as built

Depends on PR #22 (personal Google link, `vxf_0022`, Drive links). This branch adds `vxf_0023` (schema epoch 22).

## Flow
1. **Select (browser).** Task → Send → "Google Driveから選択". The server returns *public* Picker parameters only
   (`GET …/personal-google/picker-config`: OAuth client ID, Cloud project number, browser API key, scope,
   `sha256(linked Google subject)`); it requires an active Production member, policy `allowed` and an active personal link.
   The browser gets a short-lived Google access token with Google Identity Services (`openid drive.file`), keeps it in
   memory only, checks that the signed-in Google account is the linked one (compares `sha256(sub)`), opens the Picker
   (files and folder hints, multi-select) and sends **only file IDs, optional root folder IDs and frame metadata** to the Plugin. The token is never sent to the Plugin,
   stored, or revoked (revoking would remove the server-held grant too). No Production credential or refresh token ever
   reaches the browser.
2. **Register (server).** `POST /tasks/<id>/personal-drive/output-packages`: CSRF/Origin, Task `publish` permission,
   `open_personal_source` (membership + policy + active grant, re-checked), then **every ID is re-read with the user's own
   server-side grant** (`files.get`): must exist, not trashed, not a folder / Google-native document, have a byte size, be
   within the per-file and Production quota limits, have a safe unique name (no path separators, case-insensitive
   duplicates rejected). Forged or invisible IDs → 404 `drive_object_unavailable`. Frame numbers/range go through the existing
   manifest validation (duplicates and out-of-range rejected; gaps are recorded as `missing_frames` and QC fails).
   An `OutputPackage(source_kind='personal')` plus the existing `verify_output_package` job is created, idempotent by key.
3. **Copy (worker).** The existing package worker, now with a per-run read-only reader for the registrant's own grant:
   original read via GET only (size + Drive MD5 checked), bytes SHA-256'd, written by the **Production** credential into
   the version folder (`output-package-<id>`), read back and verified, package → `completed`. Reserved target IDs make
   restarts/duplicates idempotent. Grant revoked / policy blocked / member left → `blocked_authorization`
   (`personal_source_*`), recoverable; source changed/vanished → needs attention; transient → retry.
4. **Recover.** The version row shows copy status and error code; the registrant or an admin can **Retry copy**
   (`POST /output-packages/<id>/retry`) which re-queues a stuck/failed job (nothing sweeps exhausted jobs otherwise).

The personal Drive original is never modified, moved or deleted: the personal reader has no write/delete/share method and
issues only GET (asserted in tests).

## Limits / config
`VFX_FILES_GOOGLE_PICKER_API_KEY` (browser key: restrict by HTTP referrer and to the Picker API), `VFX_FILES_GOOGLE_CLOUD_PROJECT_NUMBER`,
existing `VFX_FILES_GOOGLE_CLIENT_ID`. Files per Output: `VFX_FILES_PERSONAL_IMPORT_MAX_FILES` (default 2000, max 10000);
per-file size and total quota come from the Production storage connection. Kitsu must allow the two Google scripts
(`accounts.google.com/gsi/client`, `apis.google.com/js/api.js`) and the Picker frame in its CSP.

## Fixes found on the way (apply to every Output import)
* Checksums computed by the worker could not be stored: file rows are immutable once the package is `completed` and the
  UPDATE ran after the state change in the same flush. They are now flushed first.
* A retry after a target ID had been reserved compared the (still empty) manifest value with the reserved ID and demanded
  manual reconcile. An empty manifest value is no longer a mismatch.
* Policy decision vs. grant saving is serialized by a transaction advisory lock (also when no settings row exists yet).

## Folders
Folder hints are expanded only through the linked server-side personal grant. The preview requires
`capabilities.canListChildren`, paginates bounded GET listings, re-reads every child, and derives nested relative paths
from verified provider ancestry. Selecting a folder does **not** establish recursive access or complete visibility under
`drive.file`. The UI requires explicit acknowledgment that exactly the reviewed readable files will be imported.
Inaccessible, incomplete-search, unsafe, duplicate, cyclic, shortcut and Google-native objects fail closed. Default
selection cap: 2000 files; maximum path depth: 32. EXR frame metadata and missing-frame QC remain unchanged.

Registration rechecks ancestry rather than trusting browser paths. The Worker persists reserved directory and file IDs
before provider writes and verifies the selected Production connection and exact parent/name/type. Relative paths remain
in the manifest and file rows. Deletion verifies persisted nested parent IDs before proving absence; directories are
never recursively deleted. Streamed media reads enforce the expected size before writing excess bytes and close responses.

## Continuation corrections
- Task-context generation fencing discards late Picker/config/preview/register/retry results after Task changes.
- Ordinary refreshes during personal actions update history without invalidating their cleanup; direct Refresh is disabled.
- JA/EN M4 selection, frame review, progress, copy guidance and retry text follow the Task locale. Older M1–M3 panel text
  outside the M4 flow retains its existing localization; this is not whole-product localization acceptance.
- Synthetic tests cover Task switch, Refresh, concurrent deletion completion, source integrity, byte bounds, pagination,
  nested path preservation, hidden nested targets, idempotent targets, authorization and the OAuth advisory-lock race.

## Verification checkpoint (2026-10-23)
Base: `135ea4cd572fd3d49730ecc5b9b86898556c8474`, PR base `codex/m1-m3-integration`.
Existing pinned synthetic host: Kitsu 1.0.70 / Zou 1.0.94, Python 3.10.12; host-lock digest remains unchanged.
No existing database was reset and no Staging/Production or real Google account was used.

| Gate | Result | Scope |
| --- | --- | --- |
| Focused backend/OAuth/migration/deletion | PASS: 79 tests | `pytest tests/packages/test_personal_drive_import.py tests/packages/test_personal_selection.py tests/packages/test_output_version_deletion.py tests/migrations tests/integrations/test_personal_google_oauth.py -q -p no:cacheprovider` in existing pinned runner |
| Migration rehearsal | PASS, included above | Temporary connection-local tables only: upgrade/default/check, personal-row downgrade refusal, safe downgrade/re-upgrade; installed tables untouched |
| Vitest | PASS: 48 tests / 9 files | `cd frontend; npm test` |
| Browser fixtures | PASS: 15 tests | `npm run test:browser -- --config playwright.local.config.js personal-drive-import.pw.js personal-google.pw.js --reporter=line`; installed Chrome, mocked Google/API; machine-specific config remains untracked |
| Build | PASS | `npm run build`; 36 modules; packaged JS/CSS referenced by index |
| Python syntax | PASS: 183 files | AST parse of plugin/worker/tests, without bytecode artifacts |
| Bandit 1.8.6 | PASS | `bandit --recursive plugin --severity-level medium --confidence-level medium`; no findings at configured threshold |
| npm audit | PASS | `npm audit --audit-level=high`: zero vulnerabilities |
| Synthetic test-runner pip-audit 2.10.1 | FAIL | 52 advisories: certifi 2026.7.22 (1), cryptography 43.0.3 (10), pillow 12.3.0 (35), pyopenssl 24.2.1 (4), pytest 8.4.2 (2). No ignore or dependency changes |
| Full backend diagnostic | FAIL | 479 passed / 19 failed / 2 skipped / 1 error before final focused fixes; not an acceptance PASS |
| Installed native Kitsu browser | BLOCKED | Existing test runner has no published browser port and its installed frontend HTTP route returns 404. No shared UAT stack changed; fixture tests do not substitute for this gate |
| Independent review | Completed, limited | Read-only source review found three defects, now resolved; reviewer did not execute tests or Google calls |

Full-suite diagnostics: 17 failure names match the historical M1–M3 report (17 failed / 407 passed), without claiming an
exact live-baseline reproduction. Two additional failures were upload ambiguous-outcome and completed-working-file
authorization revocation. The upload diagnostic explicitly claimed a pending `revoke_personal_google` job and returned
`invalid_upload_payload`: persistent synthetic queue contamination. Revocation also remains a full-suite uncertainty;
its isolated run progressed to a 404 rather than expected 403. The existing comparison runner is epoch 19, so its passing
two-test result is **not** valid M1–M3 baseline proof. New M4 fixture teardown completes only its own Task/revoke jobs.
The offline-bundle unit error in the copied runner was missing `.git`, not a product assertion.
The historical task-share browser fixture also fails looking for the superseded `Share` tab; checked-out baseline uses
Send/Receive. It was not rewritten to mask this stale fixture.

Exact-head hosted CI run `39828312670`: frontend dependency/tests/build and secret scan PASS; immutable-image dependency
 audit FAIL with 50 advisories in four runtime packages. Local runner inventory additionally includes pytest (two advisories),
 accounting for its 52/5 result. Hosted Python syntax/Bandit step was SKIPPED after the failed audit; local static results
 above are separate evidence. Security and installed-browser failures remain release gates. A Draft PR is reviewable work, not M4 acceptance.
Recovery archives and the earlier OAuth prototype remain untouched. Evidence and disposable scripts are retained by this
task under the KitsuSync `artifacts/m4-implementation-20261023` directory; they are not packaged or committed here.

## Not verified (needs a dedicated Google test environment and explicit approval)
Real GIS token + Picker in Kitsu's iframe (popup/cookie behavior, CSP, `setOrigin`); whether the Picker's selection is
readable by the server's refresh-token grant (same OAuth client ID / Cloud project assumed); real Drive responses
(`size`, `md5Checksum`, 404 vs 403, rate limits); Picker folder/children access; real large EXR transfers (synthetic traversal of 300 frames is covered); external/personal Gmail accounts; shared-drive write permission of the Production credential.


## Ordered remaining acceptance
1. Reconcile the pinned-host vulnerability gate through the existing security/host-lock process; no advisory suppression.
2. Provide an isolated installed Kitsu browser endpoint with ordinary synthetic form-login fixtures and this exact package.
   Re-run native Task Send/Receive, selection review, progress/retry, nested download/delete and Worker readback there.
3. Resolve full-suite queue isolation and reproduce any remaining failures against exact M1–M3 and M4 source/schema pairs.
4. With separate authorization, validate real GIS/Picker/account identity, server grant visibility, restricted-folder behavior,
   CSP/iframe/popup behavior, EXR transfer sizes, transient errors and Production-target credential permissions in a dedicated
   non-production Google test setup.
5. Review and accept M4 only after these gates pass. Do not begin M5 or deploy from this Draft PR.
