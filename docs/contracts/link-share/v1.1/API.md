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

No URL, provider token, ACL instruction, filename-based identity, destination Drive ID, or copy/move operation is accepted from the browser. The Files Plugin resolves the object ID through its own configured Drive connection/root, proves object type and root ancestry, reads bounded metadata, and verifies the source Task and each recipient Task against current Zou Production relationships and actor authorization. The Files Plugin is the sole Production Drive configuration authority. KitsuSync alone owns current route disclosure consent and checks it at Inbox intake and actual Discord dispatch; a producer claim is never consent evidence. If any requested target is invalid, the operation's atomicity policy must be explicit; recommendation: validate all targets before persisting any target, then create the operation/targets transactionally. Provider verification failure creates no link notification.

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
      "notification_status": "blocked_receiver_version"
    },
    {
      "share_target_id": "536272e4-2bd2-416b-b1e3-7ff47e51c982",
      "recipient_task_id": "775f3501-dc26-435d-a1d2-1d9f8e37b0aa",
      "status": "created",
      "notification_status": "blocked_receiver_version"
    }
  ]
}
```

`notification_status` uses the contract state vocabulary: `blocked_receiver_version`, `blocked_disclosure`, `pending`, `inbox_accepted`, `quarantined`, or `delivered`. The examples use `blocked_receiver_version` because the current KitsuSync PR #239/#240 heads support only `1.0` and do not accept `file_share.created`. This state means the share record exists but no notification is sent because an operator has not configured and verified the exact receiver contract pin. Sending is default-off and fails closed when the pin is absent, not `1.1`, or does not match the verified deployed receiver. If KitsuSync route disclosure consent is absent or revoked, use `blocked_disclosure`. `inbox_accepted` means durable receiver Inbox intake only; it does not mean Discord delivery. Use `delivered` only when a durable receiver journal result confirms delivery. If provider access/root verification fails, reject the request (403/409/422 according to the established Plugin API error contract) and persist a sanitized audit finding where appropriate. Replaying the same Idempotency-Key with the same canonical request returns the same IDs and no duplicate event; a different request under that key returns 409.

## Receiver version pin (operator configuration; proposal only)

Do not add or call an unauthenticated capabilities API. Before enabling the sender, an operator verifies the deployed KitsuSync revision against the reviewed 1.1 event schema/OpenAPI, configures the exact HTTPS receiver origin, and pins contract version `1.1` in the sender's existing deployment configuration. The HTTPS origin is verified by normal certificate and hostname validation; the contract pin is verified from the reviewed and deployed source/release records through existing deployment controls. The pin is not supplied by the Plugin event and is not inferred from an HTTP status or response body. If origin identity, deployed revision, or pin cannot be verified, the sender remains disabled. Dynamic negotiation is a separate future decision.

## KitsuSync event intake (existing route, candidate body)

The endpoint path and HMAC framing remain the existing v1 ones. Exact required headers are `X-VFX-Key-Id`, `X-VFX-Timestamp`, `X-VFX-Nonce`, `X-VFX-Content-SHA256`, `X-VFX-Signature`, and `Idempotency-Key`; the last is 16–128 characters. The current KitsuSync 1.0 contract requires root `contract_version` plus `events`. The draft 1.1 batch likewise requires root `contract_version: "1.1"` plus `events`, and every event must be `1.1`. KitsuSync PR #239 contains Draft 1.1 receiver source, but the receiver is not deployed or operator-pinned. The sender must remain disabled until the exact receiver is verified. For one share operation, the producer MUST include every recipient-target event in one signed request so the receiver can validate cross-target invariants.

```http
POST /api/integrations/vfx-files/v1/events HTTP/1.1
Content-Type: application/json
Idempotency-Key: vfxshare-batch-sha256-<stable-digest>
X-VFX-Key-Id: <configured producer key id>
X-VFX-Timestamp: <RFC 3339 timestamp>
X-VFX-Nonce: <fresh 16–128 character nonce>
X-VFX-Content-SHA256: <lowercase SHA-256 of exact raw request body>
X-VFX-Signature: sha256=<lowercase HMAC-SHA256 hex>
```

```json
{
  "contract_version": "1.1",
  "events": [
    { "contract_version": "1.1", "event_type": "file_share.created", "event_id": "...", "...": "see strict event schema" },
    { "contract_version": "1.1", "event_type": "file_share.created", "event_id": "...", "...": "second recipient target" }
  ]
}
```

This abbreviated HTTP body is illustrative only; use the full [positive fixture](fixtures/positive/two-recipient-share.json) as the body example. For a multi-recipient operation, the batch MUST contain every target from that operation. The signature MUST cover exact raw body bytes using `timestamp + LF + nonce + LF + lowercase body SHA-256 + LF + Idempotency-Key`. A retry keeps the complete event set, exact body bytes, event IDs, event payloads, and the 16–128 character Idempotency-Key stable while rotating nonce, timestamp, and signature under replay protection. KitsuSync deduplicates each event by its stable ID and payload; a repeated HTTP batch returns durable per-event duplicate outcomes.

## KitsuSync 202 response and completion

```http
HTTP/1.1 202 Accepted
Content-Type: application/json
```

```json
{
  "contract_version": "1.0",
  "results": [
    {"event_id":"22b904d7-3526-485b-8780-f1a6554518a9","outcome":"accepted","code":null},
    {"event_id":"f85e1407-bc24-4d91-aaff-085fa35d3257","outcome":"duplicate","code":null}
  ]
}
```

This is the unchanged KitsuSync 1.0 OpenAPI 202 response shape. A 1.0 request retains it unchanged. It does not show that the current 1.0 receiver accepts a 1.1 request; any future 1.1 receiver must agree its response version explicitly. Durable Inbox acceptance/deduplication is not webhook delivery. Do not mark `delivered` from HTTP 202.
