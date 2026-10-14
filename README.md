# VFX Files for Kitsu

VFX Files is an in-development Kitsu/Zou plugin and separate worker for Production-scoped file workflows. It adds managed folders, immutable Working revisions, Publish and QC records, Output Packages, approvals, and handoffs while keeping Kitsu/Zou as the identity and permission authority.

## Project status

This repository is under development. The product changes are still in Draft pull requests and are not part of `main`. No stable release is available, and the project is not approved for Production or Staging use. The only exercised host baseline is the disposable Kitsu 1.0.70 / Zou 1.0.94 environment recorded in [`environment/host-lock.yaml`](environment/host-lock.yaml); this does not establish compatibility with a studio host. The locked host's dependency security gate remains open, and live Google Drive behavior has not been verified. See [`docs/oss/PUBLICATION_AUDIT.md`](docs/oss/PUBLICATION_AUDIT.md) for publication blockers and evidence boundaries.

The feature gate defaults off. Do not enable it until the storage root, credentials, authorization behavior, host compatibility, backup plan, and separate worker have passed an approved deployment review.

## Architecture

The plugin API resolves access through the host's authenticated Kitsu/Zou context and stores plugin metadata and durable jobs in PostgreSQL. A separate worker claims fenced jobs and performs provider operations through a storage adapter. See the [runtime architecture](docs/architecture/vfx-files-runtime.md).

## Development

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before changing code. Use synthetic identities and FakeStorage for tests. Host integration evidence must name the exact source SHA and pinned disposable host; fixture-only browser checks are not installed-host proof.

Frontend checks run from `frontend/`:

```powershell
npm ci
npm test
npm run build
npm audit
```

Do not connect local tests to Production, Staging, production media, or a real Google Drive account.

## Security and licensing

Report vulnerabilities only through a verified private maintainer channel. GitHub private vulnerability reporting must be enabled and verified before public visibility; see [`SECURITY.md`](SECURITY.md). Do not place vulnerability details in public issues or pull requests.

There is no root `LICENSE` file. Although the plugin manifest contains an `MIT` label, rights-holder and copyright ownership have not been confirmed. That label does not grant permission to redistribute this repository. The third-party inventory in [`docs/oss/THIRD_PARTY_NOTICES.draft.md`](docs/oss/THIRD_PARTY_NOTICES.draft.md) is preliminary and does not resolve project licensing. Do not treat the project as licensed for public reuse until the rights holder resolves the gate.
