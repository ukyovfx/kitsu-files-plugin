"""Durable idempotent storage boundary for published KitsuSync v1 events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from .v1 import canonical_json, sha256_hex, validate_event_batch


class EventIdentityConflict(ValueError):
    """A previously used contract event ID now has different content."""


class EventRepository(Protocol):
    def get(self, event_id: str) -> Any | None: ...

    def insert(self, *, production_id: UUID, event_id: str, event_type: str, payload: dict, payload_sha256: str) -> Any: ...


@dataclass(frozen=True)
class AppendResult:
    event_id: str
    outcome: str
    record: Any


def append_event_batch(repository: EventRepository, batch: dict) -> list[AppendResult]:
    """Validate then persist the complete batch in the caller's transaction.

    Replaying an identical event returns its original record. Reusing an event
    identity for different canonical bytes is a hard conflict, never an update.
    """
    events = validate_event_batch(batch)
    staged = []
    for event in events:
        body = canonical_json(event)
        staged.append((event, sha256_hex(body)))
    results = []
    for event, digest in staged:
        existing = repository.get(event["event_id"])
        if existing is not None:
            if existing.payload_sha256 != digest or existing.production_id != UUID(event["production_id"]):
                raise EventIdentityConflict("Event identity already exists with different canonical content")
            results.append(AppendResult(event["event_id"], "duplicate", existing))
            continue
        record = repository.insert(
            production_id=UUID(event["production_id"]),
            event_id=event["event_id"],
            event_type=event["event_type"],
            payload=event,
            payload_sha256=digest,
        )
        results.append(AppendResult(event["event_id"], "accepted", record))
    return results


class SqlAlchemyEventRepository:
    """PostgreSQL adapter; appends participate in the caller's DB transaction."""

    def __init__(self, session):
        self.session = session
        from ..models import KitsuSyncEventOutbox

        self.model = KitsuSyncEventOutbox

    def get(self, event_id: str):
        from sqlalchemy import select

        return self.session.scalar(
            select(self.model).where(self.model.event_id == event_id).with_for_update()
        )

    def insert(self, *, production_id, event_id, event_type, payload, payload_sha256):
        from sqlalchemy.dialects.postgresql import insert
        from sqlalchemy import select
        from uuid import uuid4

        statement = (
            insert(self.model)
            .values(
                id=uuid4(),
                production_id=production_id,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                payload_sha256=payload_sha256,
                state="queued",
                attempts=0,
                lease_generation=0,
            )
            .on_conflict_do_nothing(index_elements=[self.model.event_id])
            .returning(self.model.id)
        )
        inserted_id = self.session.scalar(statement)
        if inserted_id is not None:
            self.session.flush()
            return self.session.get(self.model, inserted_id)
        existing = self.get(event_id)
        if existing is None or existing.payload_sha256 != payload_sha256 or existing.production_id != production_id:
            raise EventIdentityConflict("Event identity raced with different canonical content")
        return existing
