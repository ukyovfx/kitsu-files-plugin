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

The exact committed Docker recipe built successfully as image ID `sha256:22ea86def3bf277e00a14efa3ce7330d541750081304cf78a254953e3ddc31ec`. Build-time `pip check` passed. A one-shot, network-isolated candidate container imported Zou 1.0.94 and all four patched versions (`cryptography==50.0.2`, `fido2==2.2.1`, `pyOpenSSL==26.4.0`, `pysaml2==7.5.5`). `pip-audit==2.10.1` against those four exact candidates reported no known vulnerabilities. The first attempt to audit the full `pip freeze` from Windows failed because pip-audit re-resolved the official Zou package metadata and found its unpatched exact pins; that is a harness limitation, not a passing full-environment scan.

Previous isolated candidate evidence is recorded in `pinned-host-dependency-audit-2026-10-12.md`: 100 pinned-host tests, installed-page E2E, and separate-host restore passed on a prior candidate build. The full 100-test suite, installed-page E2E, restore, and complete candidate environment audit were **NOT TESTED** against the exact image ID above. The official locked host security gate remains **FAIL**.
