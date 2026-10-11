# Pinned host dependency audit — 2026-10-12

## Scope

The baseline inventory came from `/opt/zou/env/bin/python -m pip freeze` in a disposable instance of the exact image locked by `environment/host-lock.yaml`: Kitsu 1.0.70 / Zou 1.0.94, Python 3.10.12, image digest `sha256:e6d9021241b12f88616762b4c7600a5e84d646119cba4a153ce7908fbd4f300a`. `pip-audit` 2.10.1 reported 14 advisory records across `cryptography==43.0.3` and `pyOpenSSL==24.2.1`; aliases reduce these to the eight unique advisories below. Scanner records establish affected installed versions, not that every vulnerable API is reachable in Zou.

Severity below follows the upstream pyca GitHub-reviewed advisory. Some mirrors assign a different CVSS severity: for example, GitHub Advisory Database's NVD mirror scores CVE-2026-27459 High (CVSS 4.0 7.2), while pyca/pyopenssl classifies it Moderate. The High classification is retained in the risk note rather than silently discarded.

| Package in locked image | Advisory | Upstream severity | Affected behavior and applicability condition | First upstream fix |
|---|---|---:|---|---:|
| `cryptography==43.0.3` | [CVE-2026-26007](https://github.com/pyca/cryptography/security/advisories/GHSA-r6ph-v2qm-q3c2) | High | Invalid small-subgroup points on binary SECT curves; relevant when attacker-controlled keys on those uncommon curves reach affected public-key operations. | `46.0.5` |
| `cryptography==43.0.3` | [CVE-2026-69249](https://github.com/pyca/cryptography/security/advisories/GHSA-jwv3-5hgf-82ww) | Low | Duplicate self-signed intermediates can cause exponential work while rejecting attacker-controlled certificate chains. | `49.0.0` |
| `cryptography==43.0.3` | [CVE-2026-34073](https://github.com/pyca/cryptography/security/advisories/GHSA-m959-cc7f-wv43) | Low | Name constraints can miss the peer name for unusual wildcard-SAN certificate topologies. | `46.0.6` |
| `cryptography==43.0.3` | [CVE-2026-69248](https://github.com/pyca/cryptography/security/advisories/GHSA-m2h6-j472-rp4c) | Low | A wildcard SAN can escape a constrained DNS subtree during X.509 verification. | `49.0.0` |
| `cryptography==43.0.3` | [CVE-2024-12797](https://github.com/pyca/cryptography/security/advisories/GHSA-79v4-65xg-pq4g) | Low | Vulnerable statically linked OpenSSL in the upstream PyPI wheels for cryptography 42.0.0–44.0.0. | `44.0.1` |
| `cryptography==43.0.3` | [CVE-2026-34180](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf) | Moderate | Vulnerable OpenSSL bundled into cryptography wheels before the fixed wheel build. | `48.0.1` |
| `pyOpenSSL==24.2.1` | [CVE-2026-27459](https://github.com/pyca/pyopenssl/security/advisories/GHSA-5pwr-322w-8jr4) | Moderate (NVD mirror: High) | A caller-supplied DTLS cookie callback returning more than 256 bytes can overflow an OpenSSL buffer. | `26.0.0` |
| `pyOpenSSL==24.2.1` | [CVE-2026-27448](https://github.com/pyca/pyopenssl/security/advisories/GHSA-vp96-hxj8-p424) | Low | A caller-supplied SNI callback exception can be treated as success instead of rejecting the connection. | `26.0.0` |

The previous table overstated several severities. In particular, CVE-2026-69249 is Low, CVE-2026-34073 is Low, CVE-2026-34180 is Moderate, and CVE-2026-27459 is Moderate per the library maintainer. Only CVE-2026-26007 is High in the upstream maintainer severity set; the NVD-mirror High score for CVE-2026-27459 is separately retained above. A dependency audit remains a release gate even where an application call path has not been demonstrated.

## Compatibility candidate

No newer official Kitsu or Zou tag was available in the source check: Kitsu `v1.0.70` remains the latest release, and Zou `v1.0.94` remains the latest tagged baseline. Zou `v1.0.94` metadata pins `fido2==2.2.0` and `pysaml2==7.5.4`; the former constrains cryptography below 49, and the latter constrains pyOpenSSL below 24.3.0. Directly upgrading cryptography and pyOpenSSL in the original image therefore fails `pip check` and is not a safe patch.

A separate local candidate was built from the immutable official image digest above and the exact Zou `v1.0.94` source commit in `environment/host-lock.yaml`. It changes only the temporary Zou package metadata pins to `fido2==2.2.1` and `pysaml2==7.5.5`, then installs `cryptography==50.0.2` and `pyOpenSSL==26.4.0`. No Kitsu or Zou application logic was changed. This is a test candidate, not an official or supported image. The repository host lock was deliberately left unchanged.

| Candidate check | Result |
|---|---|
| Python 3.10.12 compatibility and installed versions | **PASS** — `zou==1.0.94`, `cryptography==50.0.2`, `pyOpenSSL==26.4.0`, `fido2==2.2.1`, `pysaml2==7.5.5` imported in the candidate. |
| `pip check` | **PASS** — no broken requirements. |
| `pip-audit==2.10.1` against candidate site-packages | **PASS** — no known vulnerabilities found. |
| Full pinned-host pytest suite | **PASS** — 100 passed; one upstream Flask-Caching deprecation warning. The first harness run had four setup-only failures because the copied test root omitted `environment/host-lock.yaml` and its expected crash-script path; after correcting the disposable container layout, all 100 passed. |
| Installed Files browser workflow | **PASS** — one real candidate-host Playwright test covered template activation/provisioning, separate worker execution, Working `w001`/`w002`, both downloads, Publish/QC/native Zou output, unauthorized approval denial, Supervisor approval, Handoff acceptance, and refreshed persisted UI state. |
| Candidate backup / restore | **PASS** — PostgreSQL, plugin package, host lock, staging, FakeStorage state and bytes were restored into a separate candidate container. Sanitized state reports matched exactly; all 19 storage objects and Working/Publish artifacts verified; the restored page passed its browser check. The Worker refused before the validation marker without changing job counts and returned idle after the validated marker. |

This candidate demonstrates a viable, testable dependency combination while preserving Kitsu 1.0.70 and Zou 1.0.94 application code. The remaining host action is a separately reviewed upstream Zou metadata change (or an officially supported later host image) and release of a reproducible patched image. Do not copy the temporary package changes into Production or update `host-lock.yaml` until that upstream and deployment path is approved and all release gates pass.

## Disposition

- **Locked official test image:** dependency security gate **FAIL**. It still contains the vulnerable package versions above, including one High upstream advisory and additional Moderate/Low advisories.
- **Local candidate:** dependency scan and isolated integration gates **PASS**, but the candidate is not an official host release and does not clear the locked-host release gate.
- No in-place pip upgrade, host-lock edit, Kitsu/Zou core change, Production/Staging connection, or Google Drive write was made.

## Frontend inventory

The locked frontend SBOM generated locally contains 84 components and reports Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, MIT, and MPL-2.0 identifiers. The MPL-2.0 entries are the optional `lightningcss` package family. The exact shipped bundle's third-party notice set has not been assembled or reviewed. The repository has no root `LICENSE` file even though the plugin manifest labels the plugin MIT; rights-holder and copyright confirmation remain required before adding a license.
