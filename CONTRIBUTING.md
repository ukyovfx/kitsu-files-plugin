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

## Pull requests

- Use a focused branch and draft pull request into `main`.
- Describe compatibility, migration, authorization, storage, recovery, and rollback effects.
- Include test commands and their results; do not carry forward old PASS results as current evidence.
- Never include secrets, internal server addresses, user information, or private production data in commits or pull request text.
- Do not merge, tag, release, or deploy without separate authorization.
