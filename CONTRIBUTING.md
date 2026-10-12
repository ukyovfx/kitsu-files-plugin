# Contributing

Thank you for helping improve VFX Files. Keep changes focused, preserve plugin-owned migrations and API compatibility, and include a regression test for each confirmed defect.

## Safe development

- Use the pinned disposable Kitsu/Zou host from `environment/host-lock.yaml` for host integration tests. Use synthetic identities and FakeStorage.
- Never connect development or CI to VFX Studio Production, Staging, real production media, or a real Google Drive account.
- Keep credentials in ignored local environment files. Never commit `.env`, tokens, cookies, databases, host downloads, media, staging data, or build output.
- Do not change host Kitsu/Zou core, perform a live migration, or make destructive Drive operations.
- Keep KitsuSync and this plugin in separate repositories and release lifecycles.

## Local checks

From the repository root, run the focused Python tests for changed modules and the applicable pinned-host integration suite. From `frontend/`, run:

```powershell
npm ci
npm test
npm run build
npm audit
```

Run the installed-page Playwright acceptance against the disposable pinned host for UI, API, PostgreSQL, Worker, and Storage changes. Fixture-only browser tests are not integration proof. Record each acceptance as PASS, FAIL, BLOCKED, or NOT TESTED, including the exact host lock and commit used.

The installed-page seeder is credential-producing test code. Before running it, set `VFX_FILES_DISPOSABLE_TEST_MODE=1`, provide `VFX_FILES_E2E_ROOT_URL` for the disposable host, and use `VFX_FILES_DISPOSABLE_HOST_ACK=DISPOSABLE_PINNED_KITSU_ZOU_ONLY` only when that host uses a non-reserved name. Its JSON output contains short-lived browser JWTs: capture it only through a protected test harness, never a terminal transcript, CI log, issue, or PR. Do not point it at Production, Staging, or a real Google Drive account.

## Pull requests

- Use a focused branch and draft pull request into `main`.
- Describe compatibility, migration, authorization, storage, recovery, and rollback effects.
- Include test commands and their results; do not carry forward old PASS results as current evidence.
- Never include secrets, internal server addresses, user information, or private production data in commits or pull request text.
- Do not merge, tag, release, or deploy without separate authorization.
