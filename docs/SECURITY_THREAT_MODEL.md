# VFX Files security threat model

**Status:** implementation review for the isolated plugin at Kitsu 1.0.70 / Zou 1.0.94. This documents controls observed in the repository and tests; it is not a Production security certification.

## Scope and trust boundaries

The plugin runs inside Zou and uses the host's authenticated identity and project relationships. It adds plugin-owned PostgreSQL records, a separate worker, local staging, and a StorageAdapter. The Google Drive adapter is contract-tested with an injected transport; no live Drive credentials or data are in scope. KitsuSync can supply a candidate Production storage URL, but it is not an authentication or authorization authority.

Trust boundaries:

1. Browser → same-origin Kitsu/Zou session and plugin API.
2. Plugin API → authoritative Zou user, Production, Task, membership, and assignment lookups.
3. API/database → durable job queue → separate worker process.
4. Worker → plugin-owned staging and configured storage root.
5. Plugin → native Zou WorkingFile/OutputFile API, where external calls can succeed before local reconciliation.
6. Backup operator → restored database, settings, staging, artifact data, and worker re-enable decision.

## Actors and protected assets

| Actor | Intended capability | Protected assets |
|---|---|---|
| Unauthenticated or cross-origin requester | None | Session tokens, CSRF proof, API data |
| Production artist | Read and upload only within assigned Production/Task; publish and handoff where role policy allows | Other Productions, unpublished files, immutable revisions |
| Supervisor | Review and approve an exact completed Publish; self-approval is denied | Approval history, QC evidence, latest pointers |
| Production manager/admin | Configure storage/template and provision folders in authorized Productions | Root boundary, credentials, folder bindings, configuration |
| Worker process | Execute durable, fenced jobs after authorization recheck | Staging bytes, provider objects, native Zou records, job state |
| Host/DB/storage administrator | Operational access outside the plugin's trust boundary | All plugin and host data; must be separately governed |

Protected data includes credentials, session and CSRF tokens, Kitsu UUID relationships, private media, checksums/manifests, immutable revision history, approvals, handoffs, and latest pointers.

## Threats and observed controls

| Threat | Controls observed | Residual risk / required gate |
|---|---|---|
| IDOR or cross-Production/Task access | UUIDs are resolved through host models; task-to-production relationship and project/Task permission are checked; unknown roles fail closed; worker rechecks authorization before external I/O. | Production's actual role configuration is unknown until a sanitized read-only inventory is reviewed. |
| CSRF or forged iframe context | Iframe query values do not establish identity; mutations require same-origin Origin and actor-bound, time-limited CSRF token; cookie-only and cross-origin mutation tests exist. | Host reverse-proxy origin/cookie configuration must match the tested same-origin deployment. |
| Unauthorized approval or self-approval | Supervisor capability, exact Publish/manifest binding, QC prerequisite, and publisher/reviewer separation are enforced and tested. | Installed plugin must remain under the host's authenticated API surface; no standalone trust in client role fields. |
| Path traversal or staging substitution | Server-generated staging keys are constrained to exact UUID/index syntax; resolved paths must remain beneath staging root; writes are hashed, fsynced, and create-only. | Filesystem ownership, permissions, backups, and host-level malware scanning are deployment responsibilities. |
| Oversized or inconsistent upload | Reservation enforces file/quota bounds; part route checks Content-Range, expected total, declared Content-Length and per-chunk limit before reading the body; finalization verifies the complete SHA-256. | Reverse proxy request-rate and connection limits should be configured to protect the host from request floods. |
| Cross-root Drive access or accidental public sharing | Adapter requires configured-root validation, ancestry checks, Shared Drive identity, My Drive owner identity, write capability, and denies permission failures; code does not create public ACLs; SHA-256 requires byte readback. | Contract tests do not establish live Google API behavior, OAuth scope quality, delegated permission changes, or provider semantics. Live My Drive and Shared Drive tests remain blocked. |
| Duplicate jobs, stale workers, or uncertain external result | Durable idempotency keys, PostgreSQL leases/generations, conditional completion, reserved provider IDs, and checkpoint/reconciliation exist. Native OutputFile creation uses a one-shot permit and uncertain outcomes block automatic replay. | Manual native-create reconciliation requires audited proof of worker termination and authoritative absence. |
| Permission revoked while a job is queued | Worker performs a fresh host authorization check before executing the job; failures are fail-closed. | Test this on the exact Production host version before deployment. |
| Tampered or incomplete backup/restore | Snapshot includes database and file state; manifest hashes are compared; Worker is held until restore reconciliation; destructive `vxf_0006` downgrade is prohibited as a recovery method. | The local restore marker is a trusted operator assertion, not a cryptographic signature or defense against a privileged host administrator. Backups need independent access control and encryption. |
| Secret leakage or dependency compromise | `.gitignore` excludes local secrets/data; npm lockfile and host image/source refs are pinned; Git history secret scan was clean at the audited checkpoint. | No project license has been added because rights ownership has not been verified. Production Python dependency audit and live security CI remain to be exercised after workflow setup. |

## Security review snapshot

- **Critical:** none confirmed in plugin-owned code. The inherited pinned-host runtime has unresolved high-severity dependency findings listed in `docs/security/pinned-host-dependency-audit-2026-10-12.md`; this remains a release blocker.
- **High:** the Drive adapter previously did not enforce the configured root's `canAddChildren` capability and classified provider 401/403 responses as uncertain outcomes. Fixed in `plugin/storage/google_drive.py`; regression coverage is in `tests/storage/test_google_drive_adapter.py`.
- **High (inherited runtime):** `cryptography==43.0.3` and `pyOpenSSL==24.2.1` in the pinned Kitsu/Zou image match upstream high advisories. The precise inventory and advisory references are in `docs/security/pinned-host-dependency-audit-2026-10-12.md`. This plugin repository cannot safely update host-core dependencies; keep the baseline blocked until a compatible patched host image is approved and retested.
- **Medium:** live Drive permission and object semantics, Production host compatibility, backup custody/encryption, and deployment proxy limits remain unverified gates.
- **Low / OSS readiness:** repository has no license file and GitHub currently reports no repository license; a public OSS license requires confirmation of rights holder and copyright notice. The Security policy must be enabled and verified before public visibility.

## Release conditions

Keep live Google Drive, Production compatibility, full backup/restore, security CI, and deployment as distinct gates. Never infer live-provider or Production PASS from FakeStorage or the pinned disposable host. See `docs/environment/backup-restore-acceptance-2026-10-12.md` and the implementation plan's functional acceptance matrix for current evidence.
