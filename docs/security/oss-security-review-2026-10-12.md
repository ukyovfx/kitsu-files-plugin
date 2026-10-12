# OSS security review and host candidate — 2026-10-12

## Scope and boundaries

- Repository: `ukyovfx/vfx-kitsu-files-plugin`; reviewed source checkpoint `3c66b3242301a36fbedbd2e6ff2a2e28e7868116`, branch `codex/vfx-files-plugin-implementation`.
- Review areas: host authentication and authorization, Production/Task/root isolation, uploads and downloads, immutable Working and Publish objects, Worker fencing/recovery, native Zou reconciliation, restore guard, CI and dependency supply chain, license/reporting readiness.
- No Production, Staging, KitsuSync database, real Drive account, or real production media was accessed in this pass. No merge, release, deployment, upstream PR, or host-lock edit was made.
- No applicable `AGENTS.md` exists in this repository. The target worktree already contained a local package overlay and three modified Worker files. They remain uncommitted and were excluded from the source fix commit; the overlay copy of `resources.py` was kept in sync with the committed Handoff check.

## External Skill review

The official [Ponytail repository](https://github.com/DietrichGebert/ponytail) was inspected at commit `9b58c1ffb790c075ca32e70a89cf1d80588f4abf` (version 5.1.0). The review covered its main Skill, Audit and Review Skills, `INSTALL.md`, hook manifests, and hook scripts.

- The Skill files are instruction/checklist content, not independent security scanner binaries. The Audit and Review checklists were applied manually to this repository.
- The Codex installation path asks the user to trust two lifecycle hooks. The inspected hooks run local Node code on session/subagent/prompt lifecycle events and read repository file lists; install/update actions obtain source through the plugin marketplace/GitHub/npm path. Installation also persists user-level configuration/statusline state. No runtime network request was found in the reviewed hook paths.
- Ponytail was not installed; no hook, installer, updater, or global setting was executed or changed. Instructions conflicting with this repository's safety boundaries were not adopted.
- No Ponytail CLI or separate Audit/Review executable was available. This review therefore does not claim an automated Ponytail run.

## Findings

### Critical

None confirmed in plugin-owned code in this review.

### High

- **Plugin-owned code:** the earlier Google Drive root capability and provider 401/403 classification defects remain fixed with focused regression coverage. The Handoff recipient mismatch described below is fixed and has a pinned-host regression test.
- **Locked host dependency:** `cryptography==43.0.3` is affected by upstream High [CVE-2026-26007](https://github.com/pyca/cryptography/security/advisories/GHSA-r6ph-v2qm-q3c2), fixed in 46.0.5. Exploitation requires use of affected binary SECT-curve public-key operations with attacker-controlled points; this is an uncommon curve family, but the package is in the installed host and the dependency gate must fail until the official baseline is patched or replaced.
- **Scoring difference:** `pyOpenSSL==24.2.1` is affected by [CVE-2026-27459](https://github.com/pyca/pyopenssl/security/advisories/GHSA-5pwr-322w-8jr4). pyca rates it Moderate and conditions it on an application-supplied DTLS cookie callback returning more than 256 bytes; the NVD-mirrored GitHub Advisory Database record scores it High (CVSS 4.0 7.2). The patched pyca release is 26.0.0. Keep both views visible. The broader host dependency audit remains a release failure regardless of whether Zou reaches the particular callback.
- **Potential credential exposure — unresolved:** Gitleaks found four JWT-formatted values in a retained local seed. Source tracing ties the seed to `tests/e2e/seed_installed_page_flow.py`, which called Zou's `create_access_token_for_raw_person`. Three identities were created as synthetic artists/supervisor; the manager was selected from existing host people by role/fallback, so that identity is not proven synthetic. All four tokens matched the seed's actor IDs and had no `exp` claim. The seed's only URL was a non-reserved HTTPS Google Drive folder link containing a synthetic folder ID; it is a link value, not evidence of a Drive API connection. No token was verified or used, and no DNS/HTTP/Drive request was made. The seed file is outside Git/PR content and has an owner-scoped ACL with no broad read ACE, but the token values cannot be proven synthetic-only. Treat them as potentially live until the responsible host owner identifies the signing/target host and revokes or reissues them if applicable. No token, ID, or URL value is recorded here.
- **Seeder guard remediation:** commit `3c66b3242301a36fbedbd2e6ff2a2e28e7868116` removes the URL literal, requires explicit disposable-test mode and a supplied root origin, requires an additional acknowledgement for non-reserved hostnames, creates all four identities synthetically, and configures/checks a 15-minute JWT expiry before output. Its JSON still contains credentials needed by the browser harness and must be handled as sensitive output. The isolated helper tests pass (13 tests); post-fix seeding against any host was deliberately **NOT TESTED**, so the host library's runtime expiry behavior is not claimed here. Bandit 1.9.4 found dynamic `exec` in the first helper-test implementation; the pure safety helpers were moved into an importable module and no dynamic execution remains. Two hard-coded temporary-directory findings in cleanup helpers were replaced with `tempfile.gettempdir()`. The prior URL remains in Git history; no history rewrite was attempted.
- Full package/advisory details and fixed versions are in `pinned-host-dependency-audit-2026-10-12.md`.

### Medium / Low

- Google Drive contract tests cannot verify live My Drive or Shared Drive OAuth scopes, provider ACL changes, external semantics, or real resumable transfer.
- Production compatibility, backup custody/encryption, public vulnerability reporting, license rights, exact shipped-bundle notices, history identity, CI execution, release signing/provenance, and artifact readback remain open gates.
- **Handoff recipient eligibility (fixed):** creation previously accepted a task assignee *or* any active Production member, while response authorization required task assignment. An assigned-task regression test reproduced the mismatch as an unresponsive `201 Created` Handoff. Creation now requires both active Production membership and Task assignment; the test verifies `403` and no Handoff row for a project-only member.
- The restore marker is a trusted operator assertion protected by the Worker guard; it is not a cryptographic signature or protection against a privileged host administrator.

## Patched dependency candidate

The locked image cannot accept only `cryptography` / `pyOpenSSL` upgrades: the Zou 1.0.94 packaging constraints pin FIDO2 and PySAML2 versions whose declared bounds conflict with the patched crypto packages. A separate local image was built from the exact locked official image digest and Zou 1.0.94 source commit. Its temporary Zou packaging metadata changes were limited to `fido2==2.2.1` and `pysaml2==7.5.5`; the installed versions were `cryptography==50.0.2` and `pyOpenSSL==26.4.0`. Kitsu/Zou application logic stayed at the locked commits.

Evidence from this local candidate:

- `pip check`: **PASS**, no broken requirements.
- `pip-audit==2.10.1`: **PASS**, no known vulnerabilities in the candidate environment.
- Pinned-host pytest suite: **PASS**, 100 tests.
- Real installed-page browser workflow: **PASS**, one test exercised template activation/provisioning, actual plugin API/PostgreSQL, separate Worker, FakeStorage, two artists' Working revisions and downloads, Publish/QC/native Zou OutputFile, Supervisor Approval, Handoff, and refreshed UI.
- Backup/restore into a second disposable candidate: **PASS**. PostgreSQL, package, host lock, staging, FakeStorage metadata and bytes were restored while the Worker was stopped. Source and restored sanitized state reports matched; 19 storage objects and all Working/Publish artifacts passed verification. A pre-marker Worker attempt failed before claim and left job counts unchanged; the restored installed-page browser test passed; after the manifest-matched validation marker, the one-shot Worker returned idle without changing job counts.
- The first pytest harness setup omitted the test root's host-lock file and expected crash-script path. Those two setup omissions caused four harness failures; after correcting only the disposable container layout, the full 100-test suite passed.
- Follow-up Handoff regression: **PASS** on the disposable Kitsu 1.0.70 / Zou 1.0.94 + PostgreSQL host using the real plugin route and separate worker; the assertion returned `403` with the Handoff count unchanged. Before the fix, the same case returned `201`; the full Publish/QC/native OutputFile/Approval/Handoff test then passed after the fix.

This local build is a compatibility candidate only. It does not replace the official host or clear the locked-host gate. The minimal upstream proposal is a separately reviewed Zou packaging-metadata update for the two conflicting pins, followed by an official reproducible image. No upstream PR was created because Kitsu/Zou core changes are outside this plugin PR's repository.

## Tools and local verification

| Check | Result |
|---|---|
| Gitleaks Git history | **PASS** — fresh `--all` history scan found no leak. |
| Gitleaks first-party source/docs | **PASS** — scoped source and documentation directories plus root project docs had zero findings. |
| Current PR body token-pattern check | **PASS** — no credential-shaped token patterns were found. |
| Gitleaks broad worktree scan | **REVIEW REQUIRED** — 204 generic-api-key pattern matches were confined to the ignored local Python license-index data (200) and retained upstream Zou workflow/test fixtures (4); these are unverified pattern matches, not declared clean. The first-party scoped scan remained at zero. |
| Retained local candidate seed | **REVIEW REQUIRED** — four JWT-formatted findings. Source traced them to the seeder: three identities were synthetic, while the manager was selected from the target host; tokens had no expiry. The only URL was a non-reserved HTTPS Google Drive folder link with a synthetic folder ID. No token or service was contacted. The owner-scoped local file remains pending host-owner identification and cleanup/revocation decision. |
| Seeder target/identity guards | **PASS — 13 focused unit tests** | Added reserved-host/HTTPS rules, explicit disposable mode, non-reserved host acknowledgement, URL validation, a 15-minute expiry setting/check, and synthetic-manager construction. No post-fix host seeding was run; runtime minting remains **NOT TESTED**. |
| Bandit over `plugin/` | **PASS** — current local environment reported zero findings. |
| Bandit over changed seeder/helper files | **PASS** — Bandit 1.9.4, with B101 excluded for normal pytest assertions, found no issues in the seeder, pure safety helper, and focused tests. The full `tests/e2e` scan with B101 excluded still reports one **LOW** B106 pattern in the legacy disposable upload seeder's explicitly synthetic fixed test key; no Medium/High issues remain in that scan. |
| Bandit on first run | **REVIEWED / RESOLVED** — Bandit 1.8.6 crashed under Python 3.14 on removed `ast.Num`; the ignored local tool was upgraded to 1.9.4. Its B102 finding in the new test loader and B108 temp-path findings were resolved as described above. |
| Frontend Vitest | **PASS** — 12 tests. |
| Frontend production build | **PASS**. |
| `npm audit --audit-level=moderate` | **PASS** — zero advisories. |
| Candidate host `pip-audit` | **PASS** — no known vulnerabilities. |
| Locked host Python dependency audit | **FAIL** — vulnerable packages remain in the official locked image. |
| GitHub Actions | **NOT TESTED / BLOCKED** — both runs for PR head `a754ba903d18c91871fe64cefdc670d4d8723dde` failed before any job steps ran; all jobs reported empty step lists and no logs. Local checks above are not represented as CI proof. |
| Semgrep, CodeQL CLI, Trivy, OSV-Scanner, external penetration test | **NOT TESTED** — not installed or run; no finding is inferred from their absence. |

Git history privacy is **REVIEW REQUIRED**: author metadata was inspected without reproducing values; 20 commits use a non-GitHub-noreply address and two distinct addresses appear in history. No history rewrite was attempted. The repository owner must decide whether to keep this metadata or plan an approved history rewrite before public distribution.

## OSS release gates

| Gate | Status | Remaining work |
|---|---|---|
| Plugin-owned security findings | **PASS** for the reviewed scope | Keep regression tests and review uncommitted overlay before it is included in a PR. |
| Local credential exposure | **REVIEW REQUIRED** | Four no-expiry JWTs remain in an owner-scoped local seed. Three actors were synthetic, but the manager was selected from existing host data; the signing/target host is not verified. The host owner must identify the target and revoke/reissue if the tokens may be valid. No token or service was used. The test seeder is guarded now, but post-fix host minting was not tested. |
| Official locked-host dependency audit | **FAIL** | Adopt an officially reviewed patched host image or supported later host, then rerun the full suite and update the lock only after all gates pass. |
| Local patched candidate | **PASS** as isolated evidence only | Obtain upstream metadata review and an official reproducible image. |
| Real Google My Drive / Shared Drive | **BLOCKED** | Requires separately authorized isolated roots and credentials. |
| Production Kitsu/Zou compatibility | **BLOCKED** | Requires a sanitized read-only version/build/image, database, proxy/auth, worker/staging and role inventory. No connection was attempted. |
| Backup/restore | **PASS** in disposable FakeStorage candidate | Encrypted offsite target, custody and Production recovery remain untested. Never use destructive `vxf_0006` downgrade for Production recovery. |
| License and third-party notices | **BLOCKED** | Confirm rights holder/copyright, add an authorized license, then assemble and review notices for the exact shipped bundle. |
| Private vulnerability reporting | **BLOCKED** | GitHub currently reports repository private and no enabled private reporting/security policy setting. Enable and verify before public visibility. |
| Git history privacy | **REVIEW REQUIRED** | Decide whether author identities and the prior hard-coded external Drive URL literal need an approved history rewrite before public distribution. This review did not rewrite history. |
| GitHub CI | **BLOCKED / NOT TESTED** | Restore usable Actions execution; recent checks had no steps or logs. |
| SBOM / signing / provenance / artifact readback | **NOT TESTED** | Verify CI artifact retention, release signing, provenance and readback before release. |
| Merge / release / deployment | **NOT AUTHORIZED** | No such operation was performed. |
