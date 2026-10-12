# VFX Files for Kitsu

VFX Files is a Kitsu/Zou plugin with a separate worker for Production-scoped folder provisioning, immutable Working revisions, Publish artifacts, QC, approvals, and handoffs. Kitsu/Zou remains the authority for identity and permissions; plugin metadata and durable jobs live in PostgreSQL, and external file operations go through a storage adapter.

## Status and compatibility

This repository and Draft PR are under development. It is not approved for Production or Staging use. The only exercised host is the disposable Kitsu `1.0.70` / Zou `1.0.94` image in [`environment/host-lock.yaml`](environment/host-lock.yaml). This is a test baseline, not a compatibility guarantee for an uninspected studio host.

The locked host currently fails its Python dependency security gate. A separately patched local candidate passed the recorded compatibility tests, but it is not an official Kitsu/Zou image or a supported deployment target. Live Google Drive My Drive and Shared Drive behavior and Production compatibility have not been verified. See the [security review](docs/security/oss-security-review-2026-10-12.md) and [acceptance matrix](docs/superpowers/plans/2026-10-11-vfx-kitsu-files-traceability.md).

The plugin feature gate defaults off. Do not enable it until an administrator has reviewed the configured storage root, credentials, worker configuration, and host authorization behavior.

## Development

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before changing code. Tests that prove host integration must run against the pinned disposable Kitsu/Zou environment with synthetic users and FakeStorage. Fixture-only browser tests are not installed-host integration proof.

Frontend checks run from `frontend/`:

```powershell
npm ci
npm test
npm run build
npm audit
```

The repository records focused backend and installed-page commands and their current evidence in the acceptance matrix. Do not connect local tests to Production, Staging, real production media, or a real Google Drive account.

## Security, licensing, and distribution

Report security issues through the process in [`SECURITY.md`](SECURITY.md), not a public issue or pull request. GitHub private vulnerability reporting is not enabled yet and must be verified before this repository becomes public.

There is no root `LICENSE` file. The plugin manifest currently says `MIT`, but rights-holder and copyright ownership have not been verified; that metadata is not sufficient permission to redistribute this repository. Do not treat this project as licensed for public reuse until the owner resolves that gate. Third-party notices, a release SBOM, artifact signing/provenance, and release readback are also incomplete.
