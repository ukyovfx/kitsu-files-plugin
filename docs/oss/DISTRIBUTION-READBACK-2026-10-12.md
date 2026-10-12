# Offline distribution readback — 2026-10-12

This is a local, unpublished packaging rehearsal for the current plugin directory. It does not authorize redistribution or clear the OSS release gate. No `LICENSE` file was added and the manifest's MIT label is not treated as proof of ownership.

## Distribution boundary

The plugin package candidate is the tracked `plugin/` tree, including the built `plugin/frontend/dist/` page. It contains both the Zou plugin, `worker_runtime.py`, worker handlers, and the Google Drive adapter. There is no separate Worker source artifact: install the same versioned package into the Zou plugin path for the API and expose that same path to a separate Zou Python process for Worker execution. The entrypoint retains `--once` and now supports supervised `--poll`; this change adds the process mode but does not claim a Production service configuration.

The repository-level `worker/` changes and the untracked root overlay are outside the package and must not be used as distribution inputs. The plugin consumes Kitsu/Zou-provided Python dependencies; those host packages are not copied into the plugin archive. The host lock describes the disposable verification environment, not an installable host image or production compatibility promise.

## Readback

The package was assembled from tracked `plugin/` files only, reopened from the archive, and checked file-by-file against the source SHA-256 values. Final readback **PASS**: 51 files; archive SHA-256 `c0719d11abad8d03318b1e24c66365f5eab81fbe6d4266bf18d906eb5b318556`. The temporary archive was removed after verification and was not published. The archive metadata is generated locally; this hash identifies this exact readback artifact, not a signed release artifact.

Final source-alignment check on branch `codex/vfx-files-plugin-implementation`, HEAD `f4a4abef35be32494208a058f7bd898f063161e5`: the 51 Git-tracked `plugin/` files had manifest SHA-256 `4b7771e261cc6d674e4e15f82df1572867a23e3a3e645990b9b6465f2439c8d4`. A file-by-file comparison with `/opt/zou/plugins/vfx_files` copied read-only from the stopped disposable browser-acceptance host matched all 51 files (0 missing, 0 differing, 0 extra). Rebuilding the frontend with the locked npm toolchain succeeded and regenerated the same tracked bundle filenames; no tracked plugin/frontend source or build output changed. This confirms that the installed source used by the recorded installed-page E2E matches the current PR HEAD.

At this checkpoint, all 45 untracked root overlay files are byte-identical copies of corresponding tracked `plugin/` files. The three modified root-level `worker/` files are also outside `plugin/`. The acceptance harness imported the installed package from `/opt/zou/plugins` (and the pinned pytest harness copied tracked `plugin/` to its isolated plugin folder); it did not import either root overlay. These pre-existing files were preserved and excluded from the artifact and verification inputs.

For an isolated installation rehearsal, the complete code boundary is one `plugin/` tree shared by the API and separate Worker process. The existing installed-page flow uses `python -m vfx_files.worker_runtime --once`; a supervised installation may select the new `--poll` mode. The default deployment mode remains disabled. Google Drive wiring exists, but it has only Fake HTTP contract coverage and has not been exercised against an actual Drive root.

The complete offline handoff bundle contains the 51-file tracked plugin tree, the pinned verification host lock, and `docs/oss/WORKER-DISTRIBUTION.md`. A deterministic archive was reopened and each of its 53 entries was checked against its source SHA-256: **PASS**. Bundle SHA-256 `19a4d700cc8b3cb3ef388149eb501944fdfe9f16519ad8b03ef6a57f17e62f3b`; content-manifest SHA-256 `d0202daf23f696def981dcec70caa19888edc84eae18e0cad44012152951894b`. The local temporary archive was not published.

## Worker-readiness delta readback

After adding the supported Google Drive Worker adapter factory, credential-reference handling, retry mapping, lease renewal, and `--poll`, the tracked `plugin/` tree was packaged again from Git-tracked files only. The reopened archive matched all 51 source files by SHA-256: **PASS**. Archive SHA-256 `d4ea7dfdcfbc5a69422fcc21849fe46e20f6c7646f7374a53402afa5101fdfef`. The archive existed only in a temporary local file and was removed after readback. This proves package byte integrity for this worktree snapshot; it is not a signed artifact or an installed-host/Google Drive acceptance result.

## SBOM and notices

`distribution-sbom.cdx.json` describes the package candidate and the Vue/Noble runtime code bundled into its frontend. `frontend-build-sbom.cdx.json` is the lockfile-wide CycloneDX inventory used for the frontend build and includes development/test tooling; it is a broader build-environment inventory, not a claim that every listed package is shipped.

`THIRD_PARTY_NOTICES.draft.md` is an inventory draft for legal review. It is not a final notice or license determination. Confirm the exact transitive bundle contents, notices required by each license, plugin source ownership, and rights-holder before any public distribution.

## Remaining gates

- Candidate Kitsu/Zou dependency overrides remain **FAIL** for official adoption. The repeatable local image build and SHA-256-locked wheels passed `pip check`; a full `pip-audit==2.10.1` scanned all 145 installed candidate distributions with zero findings. Against the exact candidate image, 114 host pytest tests, installed-page E2E, backup/restore and restored-page Playwright passed. No official/supportable host release or build provenance is established.
- The official locked host dependency audit remains **FAIL** as recorded in `docs/security/pinned-host-dependency-audit-2026-10-12.md`.
- Installed-host execution of the guarded seeder and browser flow is **PASS** on a separate disposable candidate host. A runtime defect in Zou's default token helper was corrected so new test tokens carry an explicit 15-minute expiry. A separate older local seed still contains four unresolved JWT-shaped values; this rehearsal neither read those values nor used, revoked, or deleted them.
- GitHub Actions remains **NOT TESTED**; the last recorded run failed before any steps and exposed no job logs. This gate was not rerun.
- License ownership, private vulnerability reporting configuration, signing/provenance, public history privacy review, real Drive, and Production compatibility remain unresolved or untested.
