# VFX Files for Kitsu

VFX Files is a Kitsu/Zou plugin with a separate worker for Production-scoped folder provisioning, immutable Working revisions, Publish artifacts, QC, approvals, and handoffs. Kitsu/Zou remains the authority for identity and permissions; plugin metadata and durable jobs live in PostgreSQL, and external file operations go through a storage adapter.

## Status and compatibility

This repository and Draft PR are under development. It is not approved for Production or Staging use. The compatibility baseline is the disposable Kitsu `1.0.70` / Zou `1.0.94` image in [`environment/host-lock.yaml`](environment/host-lock.yaml); a local patched image built from that exact baseline has also passed isolated tests. Neither image establishes compatibility for an uninspected studio host.

The official locked host currently fails its Python dependency security gate. The separately patched local candidate (`sha256:22ea86def3bf277e00a14efa3ce7330d541750081304cf78a254953e3ddc31ec`) passed a full audit of 145 installed Python distributions, 114 host pytest tests, installed-page Working/Publish/Approval/Handoff E2E, and a separate-container backup/restore rehearsal. It remains unofficial and does not clear the locked-host security gate. Live Google Drive My Drive and Shared Drive behavior and Production compatibility have not been verified. See the [security review](docs/security/oss-security-review-2026-10-12.md), [candidate build record](docs/security/patched-host-candidate-2026-10-12.md), and [acceptance matrix](docs/superpowers/plans/2026-10-11-vfx-kitsu-files-traceability.md).

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

There is no root `LICENSE` file. The plugin manifest currently says `MIT`, but rights-holder and copyright ownership have not been verified; that metadata is not sufficient permission to redistribute this repository. Do not treat this project as licensed for public reuse until the owner resolves that gate. Offline SBOM drafts, a third-party notice draft, and a local plugin archive readback are recorded under [`docs/oss`](docs/oss/DISTRIBUTION-READBACK-2026-10-12.md). They are not final release notices, signed provenance, or authorization to publish.
