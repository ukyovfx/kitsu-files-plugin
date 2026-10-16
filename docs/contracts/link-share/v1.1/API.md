# Draft API examples — file share contract 1.1

These examples are proposal-only and are not implemented. They preserve the distinction between creating a local share record, sending a signed KitsuSync event, and downstream Discord delivery.

## Plugin share request (proposed)

```http
POST /api/plugins/vfx_files/tasks/b953915f-aede-4833-8842-6200248b9497/link-shares HTTP/1.1
Authorization: Bearer <current Kitsu session JWT>
Content-Type: application/json
Idempotency-Key: artist-request-4fbece8e-6a63-49b7-95d1-dc8e8d887831
X-CSRF-Token: <existing mutation CSRF token>
Origin: https://kitsu.example.invalid
```

```json
{
  "object_id": "1FzExampleDriveFolder_opaqueID",
  "recipient_task_ids": [
    "f23f6273-4a56-4db8-9bdd-c3837fcbe3e4",
    "775f3501-dc26-435d-a1d2-1d9f8e37b0aa"
  ]
}
```

No URL, provider token, ACL instruction, filename-based identity, destination Drive ID, or copy/move operation is accepted from the browser. The server resolves the object ID through the configured KitsuSync-owned connection/root, proves object type and root ancestry, reads bounded metadata, verifies the source Task and each recipient Task against current Zou Production relationships, checks actor authorization, and checks current route disclosure consent. If any requested target is invalid, the operation's atomicity policy must be explicit; recommendation: validate all targets before persisting any target, then create the operation/targets transactionally. Provider verification failure creates no link notification.

## Plugin response (proposed)

```http
HTTP/1.1 202 Accepted
Content-Type: application/json
```

```json
{
  "share_id": "0c0726a5-f758-409d-8bc0-bfd9f46fb8d9",
  "targets": [
    {
      "share_target_id": "b5c6c31d-f5f0-4ad7-8894-6d4c925cc832",
      "recipient_task_id": "f23f6273-4a56-4db8-9bdd-c3837fcbe3e4",
      "status": "created",
      "notification_status": "pending_capability"
    },
    {
      "share_target_id": "536272e4-2bd2-416b-b1e3-7ff47e51c982",
      "recipient_task_id": "775f3501-dc26-435d-a1d2-1d9f8e37b0aa",
      "status": "created",
      "notification_status": "pending_capability"
    }
  ]
}
```

`pending_capability` means the share record exists but no notification is sent until the receiver has explicitly advertised support for both event version 1.1 and `file_share.created`. If disclosure consent is absent/revoked, use `blocked_disclosure`. If provider access/root verification fails, reject the request (403/409/422 according to the established Plugin API error contract) and persist a sanitized audit finding where appropriate. Replaying the same Idempotency-Key with the same canonical request returns the same IDs and no duplicate event; a different request under that key returns 409.

## Capability check (proposed; requires KitsuSync agreement)

The capability direction is Plugin → KitsuSync. The existing RS256 management bearer scheme described for KitsuSync → Plugin MUST NOT be assumed to authorize the reverse direction. Existing event HMAC is for Plugin → KitsuSync body intake and does not authenticate a capability response. Agree a response-authentication method and production binding before implementation. There is no unauthenticated fallback.

```http
GET /api/integrations/vfx-files/v1/capabilities HTTP/1.1
```

```json
{
  "contract_versions": ["1.0", "1.1"],
  "event_types": ["file_delivery.created", "file_delivery.ready", "file_delivery.accepted", "file_delivery.needs_fix", "file_delivery.revised", "file_share.created"],
  "max_batch_events": 100,
  "production_id": "9cb8f70d-0b63-40b7-a73d-3237e6f50d3d",
  "expires_at": "2026-10-16T04:20:00Z"
}
```

Until response authentication, Production binding and freshness rules are agreed, the capability state is `unknown`; producer outbox delivery of 1.1 remains held. A successful HTTP status alone is not capability proof.

## KitsuSync event intake (existing route, candidate body)

The endpoint path and HMAC framing remain the existing v1 ones. Keep the canonical v1 signature header names and required request envelope fields unchanged; the example below shows the candidate event-batch payload shape. KitsuSync must verify whether the existing body includes required top-level fields beyond `events` before adopting the batch schema.

```http
POST /api/integrations/vfx-files/v1/events HTTP/1.1
Content-Type: application/json
Idempotency-Key: vfxshare-batch-sha256-<64-lowercase-hex>
<the exact existing v1 timestamp, nonce, key-id and signature headers>
```

```json
{
  "events": [
    { "contract_version": "1.1", "event_type": "file_share.created", "event_id": "...", "...": "see strict event schema" },
    { "contract_version": "1.1", "event_type": "file_share.created", "event_id": "...", "...": "second recipient target" }
  ]
}
```

This abbreviated HTTP body is illustrative only; use the full [positive fixture](fixtures/positive/two-recipient-share.json) as the body example. The signature MUST cover exact raw body bytes according to the existing contract. `Idempotency-Key` is stable across retries, while the nonce, timestamp and signature are refreshed under the existing replay policy.

## KitsuSync 202 response and completion

```http
HTTP/1.1 202 Accepted
Content-Type: application/json
```

```json
{
  "contract_version": "1.1",
  "results": [
    {"event_id":"22b904d7-3526-485b-8780-f1a6554518a9","status":"accepted"},
    {"event_id":"f85e1407-bc24-4d91-aaff-085fa35d3257","status":"duplicate"}
  ]
}
```

Durable Inbox acceptance/deduplication is not webhook delivery. The plugin should query the separately agreed journal/status API or expose `inbox_accepted` until a receiver confirmation is available. Do not mark `delivered` from HTTP 202.