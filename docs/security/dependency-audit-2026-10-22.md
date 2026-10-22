# Pinned-host Python dependency audit — refreshed 2026-10-23

Scope: the "Pinned host dependency and Python static checks" CI job audits `pip freeze` of the immutable host image
`cgwire/cgwire:1.0.70-1.0.94-01@sha256:e6d9021241b12f88616762b4c7600a5e84d646119cba4a153ce7908fbd4f300a`
(Zou 1.0.94 / Kitsu 1.0.70). Exact PR-head run `39828641151` at `52b8e558bc0fb5508fa74a6a6f710c0ca3ce01f2`
reports **50 vulnerability records in 4 immutable-image packages**. The runner's `pip-audit==2.10.1` advisory data
contains duplicate records for some identifiers; the identifiers below are de-duplicated. This is an image-level gate:
M4 changes no Python dependency, host lock, or host image reference. The separate local synthetic-runner audit counted
52 records in 5 packages because its environment also contains `pytest`; those two pytest records are test-only and
are not part of the pinned-image CI result.

| Package (host pin) | Records / advisories | Audit fix level | Dependency origin and exposure |
|---|---|---|---|---|
| `certifi==2026.7.22` | 1 / `GHSA-6q48-6c7w-9gw2` | `2026.10.19` | Transitive through `requests`; the Plugin uses Requests HTTPS clients and the vulnerable CA bundle is runtime-reachable. The advisory concerns compromised emSign intermediates trusted through the bundled root, so an on-path attacker with a compromised key could intercept affected TLS connections. |
| `cryptography==43.0.3` | 10 records / `CVE-2024-12797`, `CVE-2026-26007`, `CVE-2026-34073`, `CVE-2026-69248`, `CVE-2026-69249`, `GHSA-537c-gmf6-5ccf` | `49.0.0` covers all listed records | Present in the Zou image through its FIDO2/SAML dependency constraints and imported by the Plugin for AES-GCM credential encryption and RSA service authentication. Several advisories concern SECT curves or X.509 validation paths not called by those Plugin code paths; bundled OpenSSL findings and host-side callers remain in scope. Treat as reachable at package level pending supported host-level triage. |
| `pillow==12.3.0` | 35 / `GHSA-22hg-r758-rrw9`, `GHSA-24rc-8hj2-v98c`, `GHSA-254f-3936-288h`, `GHSA-27h8-gf7j-v5hc`, `GHSA-332p-7hrc-92wf`, `GHSA-3j72-p6qg-6xp7`, `GHSA-462x-pgg6-55fp`, `GHSA-4rm8-3hmm-39fg`, `GHSA-4rr5-89j8-mwwm`, `GHSA-58r4-6rv3-gfh8`, `GHSA-5xm2-6676-qcp9`, `GHSA-65qm-jvmh-6pqg`, `GHSA-74r2-7ff9-mjj2`, `GHSA-78g4-xqwv-q3g8`, `GHSA-7mwm-ghpf-9vrm`, `GHSA-9v9v-9qvf-h2hj`, `GHSA-c6hw-5grf-ff96`, `GHSA-cp9v-662q-vvx9`, `GHSA-f2g6-542v-9j58`, `GHSA-fc9g-5qg7-mf45`, `GHSA-g4qv-2jxx-769h`, `GHSA-g7cr-3p24-7wf7`, `GHSA-g842-9rc4-69pw`, `GHSA-gg2r-wvfm-q9rx`, `GHSA-j4m9-6w36-pw47`, `GHSA-jg86-v3jv-hrpm`, `GHSA-mrfp-p49p-3hf2`, `GHSA-p4wg-p73j-c528`, `GHSA-p8hx-v2p6-rp84`, `GHSA-pchr-8j38-3x4p`, `GHSA-q45j-58j2-45rg`, `GHSA-v9pv-5gc8-w2m6`, `GHSA-vx6w-vv9f-6gvg`, `GHSA-w889-685w-c8fh`, `GHSA-xgh8-jp6q-p5x8` | `13.0.0` | Zou pins Pillow exactly for its image thumbnail/preview handling. The Plugin does not import Pillow, but uploaded image/preview processing is a host runtime path. |
| `pyopenssl==24.2.1` | 4 records / `CVE-2026-27448`, `CVE-2026-27459` | `26.0.0` | Zou/Flask stack dependency. The Plugin does not import `OpenSSL`; the affected SNI callback and DTLS-cookie callback paths belong to callers in the host stack and were not demonstrated reachable here. |

The pinned image SBOM is artifact `pinned-host-sbom` from run `39828641151`; the local full runner audit is not a
substitute for this four-package host result. Package origins and existing supported-host work are documented in
`pinned-host-dependency-audit-2026-10-12.md`, `patched-host-candidate-2026-10-12.md`,
`environment/host-lock.yaml`, and the Zou/FIDO2/SAML pins. The previously rehearsed candidate used
`cryptography==50.0.2`, `pyOpenSSL==26.4.0`, `fido2==2.2.1`, and `pysaml2==7.5.5`; that unofficial candidate predates
these Pillow and certifi findings and is not remediation approval.

## Decision

- Do **not** bump packages in this PR. Pillow 13 is a major upgrade under an exact Zou pin; the crypto pair and
  pyOpenSSL require the supported Zou/host metadata change and full host compatibility/security run. Certifi also
  requires a rebuilt supported image so the vulnerable CA bundle is no longer present.
- The CI job should be treated as a known-red gate for the pinned host until the host image or Zou pins change. A blanket
  `--ignore-vuln` would hide new advisories, so none was added.
- CI run `39828641151` also skipped Python `compileall`/Bandit after the blocking audit failed. The workflow now runs
  those checks in an independent job; the dependency audit remains a required failing gate.
- Host remediation is blocked on an upstream-supported Zou/image update (or separately approved and fully validated host
  candidate) that resolves all four package findings. Do not accept M4 while this exact host lock remains red.

## Secret scan

The Secret scan job failed before scanning: the workflow invoked `github.com/gitleaks/gitleaks/v8@v8.25.1`, but that
release declares `github.com/zricethezav/gitleaks/v8`. The workflow now uses the declared path. The restored scan found
five `generic-api-key` hits in older commits (test fixtures naming an `Idempotency-Key` header or the
`revoke_drive_credential` job type); they were reviewed as false positives and are suppressed by exact
`commit:file:rule:line` fingerprints in `.gitleaksignore`. Any new finding still fails the scan.
