# Draft: copy-free Google Drive link share event contract 1.1

**Status:** Proposal only; KitsuSync review returned `CHANGES_REQUIRED` for the earlier draft. Not implemented, not accepted, and not enabled.
**Compatibility target:** KitsuSync v1 signed event intake; the candidate adds one strict event family and does not alter `file_delivery.*` 1.0.
**Scope:** Contract artifacts only. No Plugin/KitsuSync product code, Google OAuth, Drive access, or Discord access is included.

## 1. Decision summary

- Keep the existing `POST /api/integrations/vfx-files/v1/events` receiver, HMAC signing/framing, Inbox, Plugin outbox, and Production activation-epoch fence.
- Candidate event `contract_version` is the string `1.1`; the batch contains only 1.1 events. A 1.0 Package Delivery batch remains byte/schema compatible and cannot be mixed with 1.1.
- `file_share.created` is a separate strict schema at [file_share.created.schema.json](schemas/file_share.created.schema.json). It is not a `file_delivery.*` transition and never causes file copy/move or Drive ACL mutation.
- Emit exactly one event per receiving Task. One UI share operation can share one original object with multiple Tasks: events reuse `share.id` and Drive object ID but each has a distinct `share_target_id`, `event_id`, recipient IDs, and per-target idempotency key.
- Kitsu IDs, not labels, identify Production, entity, Tasks, Task Types, Department, and actor. The Plugin must resolve and authorize each object against current Zou state before persisting the share or queuing a notification.
- A notification event may be queued only after the Production administrator has approved disclosure of Drive links and display metadata to the configured recipient Task Type route. That approval does not grant Drive access. If recipient Drive access is not independently verified, the payload says so and the receiving UI/Discord message must not imply otherwise.

## 2. Schema and bounds

The schema is intentionally strict (`additionalProperties: false` at every object). It caps each event's strings, numeric ranges, arrays, and the batch at 100 events. It carries a compact inventory summary, never a file list. Unknown or future fields require a reviewed schema revision.

### Identity

- `event_id`: unique immutable event UUID for a recipient-target event. Reused on every retry.
- `share.id`: common UUID for one user-requested share operation, including its selected recipient Tasks.
- `share.share_target_id`: stable UUID for one recipient Task within that operation. Unique per recipient and stable through retries.
- `share.revision`: `1` means the initial immutable share record. Contract 1.1 emits only `file_share.created` at revision 1. Metadata refreshes do not increment it. Changing the object or recipient requires a new share operation; a future update/revoke event needs a separately reviewed schema.
- `idempotency_key`: stable per target and request. It identifies the original create semantics; it is not recalculated from mutable metadata.

The Plugin MUST reject reuse of a target idempotency key for a different Production, source, object, or recipient. A replay of the same request returns the existing share target and emits no second event.

### Source / Recipient

`source.entity_type` is `shot` or `asset`; IDs are stable Zou UUIDs. Display names are optional snapshots and never authorization inputs. `source.task_id` and `source.task_type_id` identify the exact sending Task. `recipient.task_id` and `recipient.task_type_id` identify the exact receiving Task. A verified Department UUID/name may be included as a snapshot. Names are bounded and optional.

Before creating any share target, the Plugin MUST resolve source and recipient against the current Production and verify both belong to the same `production_id`; verify that the actor is authenticated, currently authorized to share from the source Task, and allowed to address that recipient Task/Department; and apply the Production's routing/disclosure policy. The receiver MUST validate the signed Production and configured Task-Type route. Display names must never be used to infer IDs or membership.

### Metadata and inventory semantics

Each has a `status` independently chosen from `complete`, `partial`, `unavailable` and its own `observed_at` timestamp.

- `complete`: the requested, bounded scope was fully enumerated/read. `file_count` is an exact count only for that scope.
- `partial`: a provider/page/time/permission limit stopped the scan. `file_count` MUST be null; `observed_file_count_lower_bound` records only the number actually observed. A lower bound is not an estimated total.
- `unavailable`: the provider could not produce a usable observation. Both count fields are null; unknown is never encoded as zero.

Metadata fields (`name`, `mime_type`, `size_bytes`, `modified_at`, `sha256`) may be null when the provider did not supply that field. `complete` means the metadata request completed for the bounded set of fields, not that every optional provider field exists or that content is immutable.

`frame_summary` is optional/null and has its own status. It includes only bounded aggregates (range, missing/duplicate counts and at most 20 missing-frame examples). No directory listing or per-file manifest goes into the event. For partial inventory, an exact missing/duplicate count is null unless the range and all relevant names were fully observed. A summary range is descriptive and does not assert an image sequence if the detector cannot establish one.

### Drive object, URL and access

The Plugin accepts an opaque Drive object ID from its own authorized UI/API, never an arbitrary URL. The StorageAdapter must fetch that exact object through the configured connection and prove its ancestry remains inside the currently verified Production root. It also checks object type and current read capability. On timeout, permission error, shortcut/external-parent ambiguity, root change, or incomplete ancestry proof, it fails closed and emits no link-share notification.

The Plugin alone constructs `url` after object verification. Contract 1.1 allows only these canonical forms, with exact ID equality to `object.object_id`, no userinfo, query, fragment, redirector, or extra path:

- file: `https://drive.google.com/file/d/{object_id}/view`
- folder: `https://drive.google.com/drive/folders/{object_id}`

The schema regex checks host/path shape; the receiver MUST additionally parse the URL and compare the embedded ID to `object_id`, reject URL credentials/query/fragment and verify HTTPS host exactly. Never include OAuth access/refresh tokens, authorization codes, signed/internal download URLs, or unnecessary provider parameters.

`metadata.observed_at` is the metadata observation time. `access.access_verified_at` is a separate provider permission check. `verified_service_only` means only that the Plugin's configured service identity can reach the object; it does **not** imply recipient access. `verified_recipient` may be used only when an authoritative Drive check was performed as the actual recipient principal. A Kitsu user ID or Department membership alone is not a Google identity. If recipient access cannot be verified, set `unknown` (or `verified_service_only`) and explain in notification/UI that Drive access is subject to the recipient's existing Google permissions. Never create/change ACLs or public links.

### Discord disclosure

A Production administrator must explicitly approve disclosure of the Drive link and the bounded object metadata to the configured recipient Task Type's Discord route. Persist that approval (who/when/which route/policy version) before enqueue; `route_disclosure` carries its stable approval ID, approver/timestamp, `route_id`, and `policy_version` for audit. A route change or policy-version change invalidates the prior consent until explicitly re-approved. Without current approval, persist the in-app share if policy allows, but keep notification state blocked; do not send the link.

The configured Discord channel audience and Google Drive ACL are separate. Channel members may see a filename and URL but may not have Drive access; conversely Drive access does not imply channel membership. The message should state that access depends on Google Drive permissions and should not claim that all Discord readers can open the link. No ACL widening is performed by either system.

## 3. Batch and HTTP examples

The candidate batch schema at [event-batch.schema.json](schemas/event-batch.schema.json) constrains the existing `events` array to 1–100 1.1 events. It intentionally does not invent new authentication or routing fields. The positive fixture contains two recipient events for one share operation; `share.id` and object ID match while target/event/recipient IDs differ. Every event in a batch must have the same `contract_version`, Production, and activation epoch. A 1.0 event and a 1.1 event MUST NOT share a batch. Preserve any additional required envelope fields already defined by the KitsuSync v1 request schema exactly; KitsuSync must confirm whether the deployed draft envelope has fields beyond `events` before adopting this schema.

Illustrative request (header names/framing reuse existing contract; exact Idempotency-Key syntax must remain compatible with the KitsuSync validator):

```http
POST /api/integrations/vfx-files/v1/events HTTP/1.1
Content-Type: application/json
Idempotency-Key: vfx-share-batch-<stable-sha256-of-sorted-event-ids>
<the exact existing v1 timestamp, nonce, key-id and signature headers>
```

The signature framing remains the canonical existing one: timestamp + LF + nonce + LF + lowercase SHA-256 of the exact raw body + LF + Idempotency-Key. Retry uses the same exact body bytes, event IDs and Idempotency-Key; it uses a fresh nonce/timestamp/signature as required by the existing replay protection.

Request body is the full batch in `fixtures/positive/two-recipient-share.json`. The example is synthetic and includes no credentials. Do not send the sample data to any live service.

Illustrative accepted response:

```json
{
  "contract_version": "1.1",
  "results": [
    {"event_id":"22b904d7-3526-485b-8780-f1a6554518a9","status":"accepted"},
    {"event_id":"f85e1407-bc24-4d91-aaff-085fa35d3257","status":"accepted"}
  ]
}
```

HTTP 202 / `accepted` means durable KitsuSync Inbox intake only. It is not evidence that the Discord webhook succeeded. `duplicate` means the same event was already durably accepted and produces no second journal effect. `quarantined` is durable terminal intake with an operator-visible reason; it is not silently retried as a new event. Actual notification completion is reported by KitsuSync's existing journal/status surface.

## 4. Version and capability gate

The existing events endpoint has no safe way for an old receiver to advertise a new event family. Candidate minimum capability response (same integration trust boundary; proposal only):

```http
GET /api/integrations/vfx-files/v1/capabilities
Authorization: Bearer <existing service-auth credential>
```

```json
{
  "contract_versions": ["1.0", "1.1"],
  "event_types": ["file_delivery.created", "file_delivery.ready", "file_delivery.accepted", "file_delivery.needs_fix", "file_delivery.revised", "file_share.created"],
  "max_batch_events": 100
}
```

KitsuSync must explicitly approve the capability representation and authentication. The Plugin must require a fresh, authenticated, Production-scoped capability result that includes both `1.1` and `file_share.created` before it queues/sends a 1.1 notification. Unsupported, missing, stale, malformed, wrong-audience, or failed capability checks fail closed. Capability must be bound to the receiver/Production configuration, not inferred from HTTP 200 or from a prior unrelated Production.

Share persistence and notification delivery are distinct state machines. A valid local share can exist while notification is `blocked_capability`, `blocked_disclosure`, `pending`, `inbox_accepted`, `quarantined`, or `delivered` (only if the receiver exposes a durable journal result). No user UI may label 202 as Discord delivered. Do not fall back by translating `file_share.created` into `file_delivery.*`.

Activation epoch stays in every event. KitsuSync verifies it against the enabled Production mirror at intake. Old-epoch events remain quarantined and are not rewritten/re-signed into the new epoch. Re-activation requires a new authorized event decision under the current epoch; existing historical events remain immutable.

## 5. Idempotency, outbox, retries and ordering

- Persist one immutable share operation plus one immutable target row per recipient Task. Reserve the target idempotency key transactionally before provider reads or outbox insertion.
- Derive one stable `event_id` per target's `file_share.created`, and persist the exact canonical event bytes plus their SHA-256. The same target retry returns the same IDs, snapshot and payload. A conflicting payload under an existing key is a 409; no second target/event/notification is created.
- For a multi-recipient send, each target has independent status and retry. Each event references the same Drive object; no copy/move occurs.
- The HTTP batch Idempotency-Key is a stable deterministic digest of its sorted constituent event IDs (subject to KitsuSync confirming its accepted syntax). Do not use attempt number or transient nonce in the key. HTTP retry keeps body, event IDs and key identical.
- Use the existing Plugin durable outbox and fenced worker. Backoff is bounded; non-retryable 4xx/schema/capability errors are held/quarantined for operator visibility, while transient network/429/5xx errors follow the existing bounded retry policy. Do not make an unbounded poison-event loop.
- Preserve event order per `share_target_id`; only one `created` event exists in this 1.1 proposal. Future update/revoke events must define ordering before adoption.
- KitsuSync Inbox deduplicates by stable event identity/request idempotency and returns durable `accepted`/`duplicate`/`quarantined`; signed nonce replay controls remain separate from event idempotency.

## 6. Responsibility split

| Concern | Files Plugin (producer) | KitsuSync (receiver/router) |
|---|---|---|
| Source and target identity | Resolve stable Kitsu UUIDs; same-Production relationship; current actor and recipient authorization | Verify event Production and route configuration against authenticated Production mirror |
| Drive | Resolve object ID using its authorized adapter; root ancestry and object type; snapshot metadata/inventory; canonical URL | Strictly validate event schema, HTTPS host/path and URL/object ID equality; never exercise the URL as a fetch proxy |
| Sharing | Persist operation/target/history; never copy/move; never mutate ACL | Route notification only to configured recipient Task Type channel; no Drive ACL mutation |
| Disclosure | Require current Production admin route-disclosure approval before adding link to outbox | Ensure route is enabled/Production-scoped; journal route/result; do not reinterpret Discord membership as Drive access |
| Security | HMAC-sign exact body with producer key, key ID, activation epoch; retries keep event/body/idempotency stable | Authenticate HMAC, reject replay, enforce epoch and route, durable Inbox idempotency, quarantine with diagnostics |
| Notification status | Distinguish queued/intake/quarantined from delivered | Existing journal is authoritative for webhook delivery; HTTP 202 is not success of downstream send |

## 7. Change from previous proposal

| Previous proposal concern | Revision in this draft |
|---|---|
| Source lacked entity kind | Add required `source.entity_type`; stable IDs remain authoritative; labels become optional snapshots |
| One ID ambiguously represented a multi-recipient action | Separate operation `share.id`, target `share_target_id`, unique `event_id`, and initial `share.revision = 1` |
| Partial inventory could look like exact count | Independent status and observation timestamps; partial uses null exact count + explicit lower bound; unavailable uses nulls; no file list |
| Metadata and access timestamps were conflated | `metadata.observed_at`, `inventory.observed_at`, and `access.access_verified_at` are distinct; access subject is typed |
| URL rules did not prove object identity/root | Service-generated canonical Drive URL only, exact embedded-ID equality, provider API/root ancestry check, receiver reparses URL |
| Discord route could be mistaken for file authorization | Require audited Production admin disclosure approval; explicitly separate Discord audience from Google ACL; no ACL changes |
| Old receiver compatibility was implicit | Authenticated capability gate; absent/failed support check blocks notification; share record and notification status remain separate |
| Retry semantics were underspecified | Persist exact payload; stable per-target IDs and idempotency, same bytes/key on retry, fresh HMAC nonce; epoch changes do not rewrite history |
| Event family could be confused with Package Delivery | Strict separate `file_share.created`; never translate it into `file_delivery.*` |

## 8. KitsuSync review items and open decisions

1. **Envelope exactness (blocking):** confirm the exact current v1 request-body envelope, whether `contract_version` is at event level, and all mandatory fields outside `events`. Keep those fields unchanged; this package's batch schema is a candidate composition, not a claim that it matches an uninspected envelope extension.
2. **Capability endpoint (blocking):** approve endpoint path, authentication, Production binding, cache/expiry, response type, and whether an explicit configured allowlist is preferable. Until agreed, producer must not send 1.1.
3. **Producer identity:** confirm `producer_id` canonical value and `key_id` header naming; no new key trust protocol is proposed.
4. **Idempotency validation:** confirm max syntax/length and whether KitsuSync dedupes each event independently when the batch contains multiple recipient events.
5. **Quarantine/recovery:** confirm persisted operator diagnostics and manual reconciliation for unsupported schema/epoch/route, without automatic replay under a different epoch.
6. **ACL verification:** confirm whether the Plugin can ever check the recipient's actual Google principal without exposing tokens or widening OAuth scopes. Default remains `unknown` and no ACL mutation.
7. **Disclosure policy:** choose whether Production admin approval is one persistent route-level consent (with revocation) or per-share approval. Recommendation: route-level audited consent scoped to Production + Task Type route + disclosure fields + policy version, with explicit revocation; event carries consent ID/snapshot. A route change invalidates it.
8. **Supported source types:** this draft supports `shot` and `asset`; decide whether sequence/episode-level sharing is needed before adoption.
9. **Share lifecycle:** 1.1 covers creation only. Define update/revoke separately before adding events; changing metadata does not mutate the original event.
10. **Frame analysis:** agree filename parsing policy/output fields for frame sequences. Until then, all sequence metrics may be null/partial; they are not a gate to link-only share.
11. **Notification copy:** approve concise Discord wording that says recipients may need existing Google Drive permission and that no permission was added.

## 9. Files in this proposal

- `schemas/file_share.created.schema.json` — strict event payload
- `schemas/event-batch.schema.json` — candidate homogeneous 1.1 `events` array constraint
- `schemas/receiver-capabilities.schema.json` — candidate shape for the capability response; its authentication contract remains open
- `fixtures/positive/two-recipient-share.json` — one operation targeting two Tasks, same Drive object, separate target/event identity
- `fixtures/negative/untrusted-url.json` — arbitrary URL rejection
- `fixtures/negative/object-type-url-mismatch.json` — file/folder URL-form mismatch rejection
- `fixtures/negative/partial-inventory-claims-exact-count.json` — partial/exact-count contradiction rejection
- `fixtures/negative/mixed-version-batch.json` — 1.0/1.1 mixed batch rejection

JSON Schema validates shape and bounds only. Plugin-side Kitsu authorization, same-Production membership, provider ancestry, exact URL/object ID equality, capability status, route consent, and epoch enforcement remain semantic runtime checks and are not claimed as implemented by this proposal.
