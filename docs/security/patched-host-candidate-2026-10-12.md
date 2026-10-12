# Reproducible patched host candidate — 2026-10-12

This is a local compatibility candidate only. It does not change `host-lock.yaml`, official Kitsu/Zou images, Kitsu/Zou application code, Production, or Staging.

## Build inputs

- Base: exact official test image digest in `environment/host-lock.yaml`.
- Application source: Kitsu `v1.0.70` commit `d8f420069a6bf24d98c5557e418538eff6173cea`; Zou `v1.0.94` commit `f5fc34c367a39d58d7741ab2790a5f349c312175`.
- Python: 3.10.12 from the pinned image.
- Build recipe: `environment/patched-candidate.Dockerfile`.
- Hash-locked wheels: `environment/patched-candidate-requirements.txt` (Linux x86_64, CPython 3.10-compatible wheels).
- The Dockerfile asserts the two expected Zou metadata constraints before changing them, installs only four pinned wheels with `--no-deps --require-hashes`, then runs `pip check`.

Build locally with:

```powershell
docker build -f patched-candidate.Dockerfile -t vfx-files-host-candidate:2026-10-12 environment
```

The resulting image is a candidate, not an official or supported host. The metadata change is packaging-only and must receive upstream review before it can be adopted.

## Evidence

The exact committed Docker recipe built successfully as image ID `sha256:22ea86def3bf277e00a14efa3ce7330d541750081304cf78a254953e3ddc31ec`. Build-time `pip check` passed. A one-shot, network-isolated candidate container imported Zou 1.0.94 and all four patched versions (`cryptography==50.0.2`, `fido2==2.2.1`, `pyOpenSSL==26.4.0`, `pysaml2==7.5.5`).

Current offline acceptance against that exact candidate image:

- Full candidate Python environment audit: `pip-audit==2.10.1`, all 145 installed distributions pinned to their observed versions, `--no-deps --disable-pip`; **zero findings**. This scans the candidate environment as built; the official locked image remains separately **FAIL**.
- Candidate-host pytest: **114 passed**, one upstream Flask-Caching deprecation warning, zero failures/skips. The first harness attempt placed the crash worker helper at a different temporary path and produced two setup failures; rerunning from the expected `/tmp/vfx-kitsu-test-root` layout passed the entire suite.
- Installed Files page: **1 Playwright test passed** against real candidate-host API, PostgreSQL, installed package, separate one-shot Worker and persistent FakeStorage. It covered nested folder provisioning/reuse, two Artists, Working `w001`/`w002`, immutable downloads, Publish, QC, native Zou OutputFile, denied self-approval, Supervisor approval, Handoff acceptance and refreshed persisted state.
- Backup/restore: **PASS** into a second container of the same image. The PostgreSQL dump, installed plugin, host lock, non-secret settings, staging and FakeStorage state/bytes were hashed; source and restored state reports matched. Worker remained held before the manifest-matched marker, then returned idle. Restored-page browser readback verified both Working versions and the Published/Approved pointers.
- Frontend Vitest: **12 passed**; frontend production build passed; `npm audit --audit-level=moderate`: **zero vulnerabilities**.

The installed-page seeder was corrected after the disposable run revealed that Zou's `create_access_token_for_raw_person` disables the default expiry when a Person has no expiration date. The seeder now explicitly passes a 15-minute expiry and persists the resulting JTI. Its JWTs are generated only for synthetic test identities, kept inside the protected browser harness, and never printed.

This remains a local compatibility candidate, not an official or supported host release. The official locked-host dependency security gate remains **FAIL** until upstream or a supported host image resolves the vulnerable package constraints and passes its own full verification. No host-lock change is authorized by these test results.
