# VFX Files runtime architecture

This diagram describes the implementation checkpoint `bb732e3c3932f7488053e7e82ea5243d2358c121`. It is a source-level design map, not deployment approval or evidence of live Google Drive or Production behavior.

```mermaid
flowchart LR
  Browser[Browser in Kitsu shell] -->|Kitsu session and same-origin API| API[Zou plugin API]
  API -->|resolve user, Production, Task and capability| Zou[Kitsu / Zou authority]
  API -->|plugin records and durable jobs| PG[(PostgreSQL)]
  PG -->|leased, fenced jobs| Worker[Separate worker]
  Worker -->|recheck authorization and restore gate| Zou
  Worker -->|staged bytes and provider calls| Adapter[Storage adapter]
  Adapter --> Drive[Configured Google Drive root]
  API -->|WorkingFile / OutputFile records| Zou
  Worker -->|verified result and reconciliation| PG
  PG -->|outbox events| Events[Event delivery worker]
  Events -->|signed, idempotent HTTPS events| KS[KitsuSync integration]
```

## Ownership boundaries

- Kitsu/Zou owns identity, Production membership, Task assignments, and native WorkingFile/OutputFile records.
- PostgreSQL stores plugin state, job leases, manifests, workflow history, and event outbox records.
- The separate Worker performs external storage operations after authorization and restore checks. A database success state alone is not proof that provider bytes were transferred.
- The storage adapter is constrained to the configured Production root. KitsuSync receives signed integration events and can provide an authorized-root contract; it is not the identity authority.

For publication blockers and evidence boundaries, see [`docs/oss/PUBLICATION_AUDIT.md`](../oss/PUBLICATION_AUDIT.md).
