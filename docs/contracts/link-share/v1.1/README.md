# Draft: copy-free Google Drive link share event contract 1.1

**Status:** Proposal only; KitsuSync review returned `CHANGES_REQUIRED` for the earlier draft. Not implemented, not accepted, and not enabled.
**Compatibility target:** A future KitsuSync receiver explicitly pinned to contract `1.1`. The current KitsuSync PR #239/#240 heads accept only contract `1.0`; this proposal is incompatible with them and sending stays disabled.
**Scope:** Contract artifacts only. No Plugin/KitsuSync product code, Google OAuth, Drive access, or Discord access is included.

## 1. Decision summary

- Keep the existing `POST /api/integrations/vfx-files/v1/events` receiver, exact HMAC signing/framing, Inbox, Plugin outbox, and Production activation-epoch fence.
- The draft batch requires root `contract_version: "1.1"`; every event also requires `contract_version: "1.1"`. The separate KitsuSync 1.0 batch/event contract and `file_delivery.*` semantics remain unchanged. Batches are homogeneous; the 1.1 schema rejects 1.0 events.
- `file_share.created` is a separate strict schema at [file_share.created.schema.json](schemas/file_share.created.schema.json). It is not a `file_delivery.*` transition and never causes file copy/move or Drive ACL mutation.
- Emit exactly one event per receiving Task. One UI share operation can share one original object with multiple Tasks: events reuse `share.id` and Drive object ID but each has a distinct `share_target_id`, `event_id`, recipient IDs, and per-target idempotency key.
- Kitsu IDs, not labels, identify Production, entity, Tasks, Task Types, Department, and actor. The Plugin must resolve and authorize each object against current Zou state before persisting the share or queuing a notification.
- KitsuSync owns the Production + recipient Task Type Discord route and its explicit Drive-link disclosure opt-in, audit record, and revocation. It checks current consent at inbox intake and again immediately before Discord dispatch. Producer fields or UI state never prove KitsuSync consent. The sender remains default-off until the exact receiver contract version is pinned and verified.

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

Metadata fields (`name`, `mime_type`, `size_bytes`, `modified_at`, `sha256`) may be null when the provider did not supply that field. `complete` means the metadata request completed for the bounded set of fields, not that every optional provider field exists or that content is immutable. `unavailable` requires all five measured-value fields to be null.

`frame_summary` is optional/null and has its own status. It includes only bounded aggregates (range, missing/duplicate counts and at most 20 missing-frame examples). `complete` requires frame start/end and exact missing/duplicate counts; `partial` requires missing/duplicate counts to be null; `unavailable` requires all measurements null and no samples. No directory listing or per-file manifest goes into the event. A summary range is descriptive and does not assert an image sequence if the detector cannot establish one.

### Drive object, URL and access

The Plugin accepts an opaque Drive object ID from its own authorized UI/API, never an arbitrary URL. The StorageAdapter must fetch that exact object through the configured connection and prove its ancestry remains inside the currently verified Production root. It also checks object type and current read capability. On timeout, permission error, shortcut/external-parent ambiguity, root change, or incomplete ancestry proof, it fails closed and emits no link-share notification.

The Plugin alone constructs `url` after object verification. Contract 1.1 allows only these canonical forms, with exact ID equality to `object.object_id`, no userinfo, query, fragment, redirector, or extra path:

- file: `https://drive.google.com/file/d/{object_id}/view`
- folder: `https://drive.google.com/drive/folders/{object_id}`

The schema regex checks host/path shape; the receiver MUST additionally parse the URL and compare the embedded ID to `object_id`, reject URL credentials/query/fragment and verify HTTPS host exactly. Never include OAuth access/refresh tokens, authorization codes, signed/internal download URLs, or unnecessary provider parameters.

This 1.1 URL shape does not carry Google's `resourceKey` query parameter. A resource-key-protected link may require that key for recipients who do not already have access. Do not strip the key and claim the link is usable, or append it outside this contract. Until a separately reviewed contract extension defines the field, disclosure, storage, and recipient behavior, such items are unsupported for 1.1 sharing and must remain blocked from send.

`metadata.observed_at` is the metadata observation time. `access.access_verified_at` is a separate provider permission check. `verified_service_only` means only that the Plugin's configured service identity can reach the object; it does **not** imply recipient access. This contract has no recipient-principal verification status. Use `unknown` when recipient Google access is unknown, and `denied` only when the relevant check actually established denial; do not represent service access as recipient access. Explain that Drive access depends on the recipient's existing Google permissions. Never create/change ACLs or public links.

### Discord disclosure

KitsuSync owns the Production + recipient Task Type Discord route and the explicit opt-in to disclose Drive links. KitsuSync persists the approval, audit history, and revocation state. It checks current consent when accepting the event into the Inbox and again immediately before dispatch; a route or policy change invalidates the approval until renewed. The event deliberately carries no `route_disclosure` claim: a Plugin-provided approval ID, route ID, timestamp, policy version, or consent assertion is not evidence of KitsuSync admin consent. The Plugin may preserve an in-app share while notification stays blocked, but it must not send or imply that consent exists.

The configured Discord channel audience and Google Drive ACL are separate. Channel members may see a filename and URL but may not have Drive access; conversely Drive access does not imply channel membership. The message should state that access depends on Google Drive permissions and should not claim that all Discord readers can open the link. No ACL widening is performed by either system.

## 3. Batch and HTTP examples

The batch schema requires `contract_version: "1.1"` and an `events` array of 1–100 strict 1.1 events. Each event version must equal the batch version; the 1.1 schema cannot admit mixed or 1.0 events. The positive fixture contains two recipient events for one share operation; `share.id` and object ID match while target/event/recipient IDs differ. The current KitsuSync PR #239/#240 receiver requires a root and event version of `1.0`, so it rejects this proposal. The 1.0 request and response contracts remain unchanged; do not infer 1.1 support from the shared route or HMAC format.

Illustrative request for a future verified 1.1 receiver. Use the exact existing header names and constraints: `X-VFX-Key-Id`, `X-VFX-Timestamp`, `X-VFX-Nonce`, `X-VFX-Content-SHA256`, `X-VFX-Signature`, and `Idempotency-Key` (16–128 characters). The sender must preserve the existing validator's accepted syntax.

```http
POST /api/integrations/vfx-files/v1/events HTTP/1.1
Content-Type: application/json
Idempotency-Key: vfx-share-batch-<stable-sha256-of-sorted-event-ids>
X-VFX-Key-Id: <configured producer key id>
X-VFX-Timestamp: <RFC 3339 timestamp>
X-VFX-Nonce: <fresh 16–128 character nonce>
X-VFX-Content-SHA256: <lowercase SHA-256 of exact raw request body>
X-VFX-Signature: sha256=<lowercase HMAC-SHA256 hex>
```

The signature framing remains the canonical existing one: timestamp + LF + nonce + LF + lowercase SHA-256 of the exact raw body + LF + Idempotency-Key. The Idempotency-Key is 16–128 characters. A retry keeps exact body bytes, event IDs, and Idempotency-Key stable while rotating nonce, timestamp, and signature under replay protection.

Request body is the full batch in `fixtures/positive/two-recipient-share.json`. The example is synthetic and includes no credentials. Do not send the sample data to any live service.

Illustrative accepted response:

```json
{
  "contract_version": "1.0",
  "results": [
    {"event_id":"22b904d7-3526-485b-8780-f1a6554518a9","outcome":"accepted","code":null},
    {"event_id":"f85e1407-bc24-4d91-aaff-085fa35d3257","outcome":"duplicate","code":null}
  ]
}
```

This is the unchanged KitsuSync 1.0 OpenAPI response shape (`contract_version`, and each result's `event_id`, `outcome`, and `code`). It does not claim the current 1.0 receiver accepts a 1.1 request. A 1.0 request retains this 1.0 response unchanged. HTTP 202 / `accepted` means durable KitsuSync Inbox intake only, not Discord delivery. `duplicate` adds no second journal effect; `quarantined` is durable terminal intake with an operator-visible reason. Actual notification completion is reported by KitsuSync's journal/status surface.

## 4. Version pin and sender gate

Do not add a dynamic capabilities endpoint to this accepted contract. There is no authenticated, agreed capability exchange on the current receiver. Before rollout, an operator must verify the deployed KitsuSync source/release and its event schema/OpenAPI at the selected exact revision, then configure the sender's receiver contract version pin as `1.1` for that trusted receiver origin. The receiver origin is identified by the configured HTTPS authority with normal certificate/hostname validation; the version pin is an operator-verified deployment setting, not a producer claim or HTTP response. Record the reviewed source revision and deployed revision through existing deployment controls; if they cannot be verified as matching the reviewed 1.1 contract, do not enable sending. No new wire fields or unauthenticated discovery endpoint are implied.

The 1.1 sender is default-off. Missing pin, any pin other than `1.1`, a changed receiver origin, failed TLS identity validation, or a deployment/version mismatch keeps notification held and fails closed. The exact current PR #239/#240 receiver is pinned to `1.0`; it does not accept `file_share.created`, so 1.1 sending remains disabled against it. Dynamic capability negotiation and its authentication are a separate future decision.

Share persistence and notification delivery are distinct state machines. A valid local share can exist while notification is `blocked_receiver_version`, `blocked_disclosure`, `pending`, `inbox_accepted`, `quarantined`, or `delivered` (only if the receiver exposes a durable journal result). No user UI may label 202 as Discord delivered. Do not fall back by translating `file_share.created` into `file_delivery.*`.

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
| Disclosure | Do not assert receiver consent in the event; hold notification unless receiver version is pinned | Own Production + recipient Task Type route and explicit Drive-link opt-in; audit/revoke; check at Inbox intake and actual dispatch |
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
| Old receiver compatibility was implicit | Exact receiver deployment/version pin; sender default-off and fail-closed when absent or mismatched; share record and notification status remain separate |
| Retry semantics were underspecified | Persist exact payload; stable per-target IDs and idempotency, same bytes/key on retry, fresh HMAC nonce; epoch changes do not rewrite history |
| Event family could be confused with Package Delivery | Strict separate `file_share.created`; never translate it into `file_delivery.*` |

## 8. KitsuSync review items and open decisions

1. **Receiver implementation:** the current 1.0 receiver does not implement `file_share.created`. Keep sending disabled until KitsuSync publishes and deploys the exact reviewed 1.1 contract and handler.
2. **Receiver pin operations:** agree the existing configuration location and deployment evidence used to bind the configured HTTPS origin to the exact reviewed KitsuSync source/revision; do not add a capabilities endpoint by implication.
3. **Producer identity:** confirm `producer_id` canonical value; current KitsuSync uses `X-VFX-Key-Id` to select a producer-bound integration key.
4. **Idempotency validation:** current receiver accepts a 16–128 character `Idempotency-Key`; confirm syntax and whether event deduplication remains independent for multi-recipient batches in any future 1.1 handler.
5. **Quarantine/recovery:** confirm persisted operator diagnostics and manual reconciliation for unsupported schema/epoch/route, without automatic replay under a different epoch.
6. **Recipient Drive access:** no recipient-principal verification path is defined; retain service-only, unknown, and denial states without implying Google access or mutating ACLs.
7. **Disclosure policy:** KitsuSync owns audited, revocable consent scoped to Production + recipient Task Type route and Drive-link disclosure. The receiver checks it at intake and dispatch; event content cannot assert consent.
8. **Supported source types:** this draft supports `shot` and `asset`; decide whether sequence/episode-level sharing is needed before adoption.
9. **Share lifecycle:** 1.1 covers creation only. Define update/revoke separately before adding events; changing metadata does not mutate the original event.
10. **Frame analysis:** agree filename parsing policy/output fields for frame sequences. Until then, all sequence metrics may be null/partial; they are not a gate to link-only share.
11. **Notification copy:** approve concise Discord wording that says recipients may need existing Google Drive permission and that no permission was added.

## 9. Files in this proposal

- `schemas/file_share.created.schema.json` — strict event payload
- `schemas/event-batch.schema.json` — candidate homogeneous 1.1 `events` array constraint
- `fixtures/positive/two-recipient-share.json` — one operation targeting two Tasks, same Drive object, separate target/event identity
- `fixtures/negative/untrusted-url.json` — arbitrary URL rejection
- `fixtures/negative/object-type-url-mismatch.json` — file/folder URL-form mismatch rejection
- `fixtures/negative/partial-inventory-claims-exact-count.json` — partial/exact-count contradiction rejection
- `fixtures/negative/metadata-unavailable-claims-values.json` — unavailable metadata with values rejected
- `fixtures/negative/frame-summary-complete-omits-aggregate.json` — complete frame summary missing an aggregate rejected
- `fixtures/negative/frame-summary-partial-claims-exact-count.json` — partial frame summary with an exact count rejected
- `fixtures/negative/frame-summary-unavailable-claims-values.json` — unavailable frame summary with measured values rejected
- `fixtures/negative/mixed-version-batch.json` — 1.0/1.1 mixed batch rejection

JSON Schema validates shape and bounds only. Plugin-side Kitsu authorization, same-Production membership, provider ancestry, exact URL/object ID equality, receiver pin verification, KitsuSync route consent, and epoch enforcement remain semantic runtime checks and are not claimed as implemented by this proposal.
