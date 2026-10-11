# Pinned host dependency audit — 2026-10-12

## Scope

Audited Python packages installed in the disposable official test image locked by `environment/host-lock.yaml` (`cgwire/cgwire:1.0.70-1.0.94-01` at its recorded SHA-256 digest). The environment was read only. No Docker image, Kitsu/Zou source, Production, or Staging configuration was changed.

The package inventory came from `/opt/zou/env/bin/python -m pip freeze` inside the disposable restored test host. `pip-audit` 2.10.1 evaluated 145 package records and reported 14 records across `cryptography==43.0.3` and `pyOpenSSL==24.2.1`; records include duplicate aliases. Unique advisory identifiers include:

| Package | Finding | Severity | Upstream fixed version |
|---|---|---|---|
| cryptography 43.0.3 | CVE-2026-26007 — small-subgroup validation gap on SECT curves | High | 46.0.5 |
| cryptography 43.0.3 | CVE-2026-69249 — exponential certificate path building with duplicate intermediates | High | 49.0.0 |
| cryptography 43.0.3 | CVE-2026-34073 — DNS name constraint validation gap | Medium | 46.0.6 |
| cryptography 43.0.3 | CVE-2026-69248 — wildcard DNS name constraint validation | Medium | 49.0.0 |
| cryptography 43.0.3 | CVE-2024-12797 — OpenSSL raw public key verification behavior | Medium | 44.0.1 |
| cryptography 43.0.3 | GHSA-537c-gmf6-5ccf — vulnerable OpenSSL bundled in PyPI wheels | High | 48.0.1 |
| pyOpenSSL 24.2.1 | CVE-2026-27459 — DTLS cookie callback buffer overflow | High | 26.0.0 |
| pyOpenSSL 24.2.1 | CVE-2026-27448 — SNI callback exception fails open | Medium | 26.0.0 |

Severity is taken from upstream GitHub-reviewed advisories where available. Vendor scoring can differ between CVSS versions; the CI gate fails on any finding and does not suppress the scoring differences.

## Disposition

These packages are inherited from the pinned Kitsu/Zou host image, outside this plugin repository's dependency ownership. Updating them in place could change Kitsu/Zou runtime behavior and would violate the host-core boundary. No compatibility workaround or exception was added. The pinned-host Python dependency audit therefore **FAILS** the OSS security gate until a separately reviewed host image keeps Kitsu 1.0.70 / Zou 1.0.94 compatible while carrying patched dependencies, or a later supported host baseline is selected and fully retested.

The security workflow intentionally runs the dependency audit without an ignore list so this gate remains visible. Required versions above are advisory remediation hints, not authorization to mutate the disposable or Production host.

## Frontend inventory

The locked frontend dependency SBOM generated locally contains 84 components and reports Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, MIT, and MPL-2.0 license identifiers. The MPL-2.0 entries are the optional `lightningcss` package family. The exact shipped bundle's third-party notice set has not been assembled or reviewed; do not treat the SBOM as a license grant or release notice. The repository has no root `LICENSE` file even though the plugin manifest labels the plugin MIT.

## References

- [GitHub advisory: cryptography subgroup validation](https://github.com/pyca/cryptography/security/advisories/GHSA-r6ph-v2qm-q3c2)
- [NVD: CVE-2026-26007](https://nvd.nist.gov/vuln/detail/CVE-2026-26007)
- [GitHub advisory: cryptography certificate path exhaustion](https://github.com/pyca/cryptography/security/advisories/GHSA-jwv3-5hgf-82ww)
- [NVD: CVE-2026-69249](https://nvd.nist.gov/vuln/detail/CVE-2026-69249)
- [GitHub advisory: pyOpenSSL cookie callback overflow](https://github.com/pyca/pyopenssl/security/advisories/GHSA-5pwr-322w-8jr4)
- [GitHub advisory: pyOpenSSL SNI callback fail-open](https://github.com/pyca/pyopenssl/security/advisories/GHSA-vp96-hxj8-p424)
