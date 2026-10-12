# Offline distribution readback — 2026-10-12

This is a local, unpublished packaging rehearsal for the current plugin directory. It does not authorize redistribution or clear the OSS release gate. No `LICENSE` file was added and the manifest's MIT label is not treated as proof of ownership.

## Distribution boundary

The package candidate is the tracked `plugin/` tree, including the built `plugin/frontend/dist/` page. Repository-level `worker/` changes and the untracked root overlay are outside this artifact. The plugin consumes Kitsu/Zou-provided Python dependencies; those host packages are not copied into the plugin archive. This rehearsal does not create a host image or a release artifact.

## Readback

The package was assembled from tracked `plugin/` files only, reopened from the archive, and checked file-by-file against the source SHA-256 values. Final readback **PASS**: 51 files; archive SHA-256 `c0719d11abad8d03318b1e24c66365f5eab81fbe6d4266bf18d906eb5b318556`. The temporary archive was removed after verification and was not published. The archive metadata is generated locally; this hash identifies this exact readback artifact, not a signed release artifact.

## SBOM and notices

`distribution-sbom.cdx.json` describes the package candidate and the Vue/Noble runtime code bundled into its frontend. `frontend-build-sbom.cdx.json` is the lockfile-wide CycloneDX inventory used for the frontend build and includes development/test tooling; it is a broader build-environment inventory, not a claim that every listed package is shipped.

`THIRD_PARTY_NOTICES.draft.md` is an inventory draft for legal review. It is not a final notice or license determination. Confirm the exact transitive bundle contents, notices required by each license, plugin source ownership, and rights-holder before any public distribution.

## Remaining gates

- Candidate Kitsu/Zou dependency overrides remain **FAIL** for official adoption. The repeatable local image build and SHA-256-locked wheels passed `pip check`; a full `pip-audit==2.10.1` scanned all 145 installed candidate distributions with zero findings. Against the exact candidate image, 114 host pytest tests, installed-page E2E, backup/restore and restored-page Playwright passed. No official/supportable host release or build provenance is established.
- The official locked host dependency audit remains **FAIL** as recorded in `docs/security/pinned-host-dependency-audit-2026-10-12.md`.
- Installed-host execution of the guarded seeder and browser flow is **PASS** on a separate disposable candidate host. A runtime defect in Zou's default token helper was corrected so new test tokens carry an explicit 15-minute expiry. A separate older local seed still contains four unresolved JWT-shaped values; this rehearsal neither read those values nor used, revoked, or deleted them.
- GitHub Actions remains **NOT TESTED**; the last recorded run failed before any steps and exposed no job logs. This gate was not rerun.
- License ownership, private vulnerability reporting configuration, signing/provenance, public history privacy review, real Drive, and Production compatibility remain unresolved or untested.
