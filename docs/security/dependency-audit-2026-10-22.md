# Pinned-host Python dependency audit — 2026-10-22

Scope: the "Pinned host dependency and Python static checks" CI job audits `pip freeze` of the immutable host image
`cgwire/cgwire:1.0.70-1.0.94-01` (Zou 1.0.94). It reports **39 advisories in 4 packages**. This is a property of the
pinned host, not of the Output deletion change: no dependency, lock file or image reference changes in this branch,
and the 2026-10-12 review already recorded the locked host as FAIL for the same reason. `pip-audit==2.10.1` was re-run locally against the exported freeze on 2026-10-22.

| Package (host pin) | Advisories | Fixed in | Pinned by | Reachable from plugin code? |
|---|---|---|---|---|
| `pillow==12.3.0` | 24 | 13.0.0 | `zou` pins `pillow==12.3.0` exactly (thumbnail/preview processing in Zou) | No. The plugin never imports `PIL`. Exposure is Zou's own image handling of uploaded previews. |
| `cryptography==43.0.3` | 10 | 46.0.6 – 49.0.0 | `fido2` / transitive pins in Zou | Only `AESGCM` (credential vault) and RSA hashing/padding (service auth) are used. The advisories concern SECT curves, X.509 verification/name constraints and bundled OpenSSL; none of those APIs are called by the plugin. |
| `pyopenssl==24.2.1` | 4 | 26.0.0 | Zou/Flask stack | No. DTLS cookie and SNI callback advisories; the plugin does not import `OpenSSL`. |
| `certifi==2026.7.22` | 1 | 2026.10.19 | `requests` (`certifi>=2023.5.7`) | Indirectly: CA bundle used by the plugin's HTTPS clients. Low impact; a bundle refresh. |

Severity detail for `cryptography` / `pyOpenSSL` and a rehearsed candidate (`cryptography==50.0.2`, `pyOpenSSL==26.4.0`,
`fido2==2.2.1`, `pysaml2==7.5.5`) are recorded in `pinned-host-dependency-audit-2026-10-12.md` and
`patched-host-candidate-2026-10-12.md`. Pillow and certifi are new since that review (the advisory database moved).

## Decision

- Do **not** bump packages in this PR. Pillow 13 is a major upgrade under an exact Zou pin and needs its own host
  compatibility run; the crypto pair needs the Zou metadata change already analysed on 2026-10-12.
- The CI job should be treated as a known-red gate for the pinned host until the host image or Zou pins change. A blanket
  `--ignore-vuln` would hide new advisories, so none was added.
- Next evidence needed: rerun the 2026-10-12 candidate recipe with Pillow/certifi fixed versions and the full host suite.

## Secret scan

The Secret scan job failed before scanning: the workflow invoked `github.com/gitleaks/gitleaks/v8@v8.25.1`, but that
release declares `github.com/zricethezav/gitleaks/v8`. The workflow now uses the declared path. The restored scan found
five `generic-api-key` hits in older commits (test fixtures naming an `Idempotency-Key` header or the
`revoke_drive_credential` job type); they were reviewed as false positives and are suppressed by exact
`commit:file:rule:line` fingerprints in `.gitleaksignore`. Any new finding still fails the scan.
