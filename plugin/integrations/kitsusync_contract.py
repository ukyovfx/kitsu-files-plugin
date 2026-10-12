"""Synthetic boundary for the coordinated KitsuSync v1 contract.

The fake remains deliberately non-networked. Plugin-to-KitsuSync signed event
delivery is a separate trust direction from management service-bearer auth.
"""

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Protocol


@dataclass(frozen=True)
class AuthorizedProductionRoot:
    production_id: str
    provider: str
    root_id: str
    drive_id: str | None


@dataclass(frozen=True)
class IntegrationEvent:
    idempotency_key: str
    event_type: str
    payload: Mapping[str, object]
    payload_sha256: str


class ConnectionProvider(Protocol):
    def authorized_root(self, production_id: str) -> AuthorizedProductionRoot: ...


class EventOutbox(Protocol):
    def append(self, *, idempotency_key: str, event_type: str, payload: Mapping[str, object]) -> IntegrationEvent: ...


class ContractConflict(Exception):
    pass


class FakeConnectionProvider:
    def __init__(self, roots: Mapping[str, AuthorizedProductionRoot]):
        self._roots = dict(roots)

    def authorized_root(self, production_id: str) -> AuthorizedProductionRoot:
        root = self._roots.get(production_id)
        if root is None or root.production_id != production_id or not root.root_id:
            raise PermissionError("No synthetic authorized Production root is configured")
        return root


class FakeEventOutbox:
    """In-memory contract double; not a production durable outbox."""

    def __init__(self):
        self._events: dict[str, IntegrationEvent] = {}

    def append(self, *, idempotency_key: str, event_type: str, payload: Mapping[str, object]) -> IntegrationEvent:
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 255:
            raise ValueError("A bounded event idempotency key is required")
        if not isinstance(event_type, str) or not event_type or len(event_type) > 128:
            raise ValueError("A bounded event type is required")
        try:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("Event payload must be JSON serializable") from None
        digest = hashlib.sha256(encoded).hexdigest()
        event = IntegrationEvent(idempotency_key, event_type, json.loads(encoded), digest)
        previous = self._events.get(idempotency_key)
        if previous is not None:
            if previous != event:
                raise ContractConflict("Event idempotency key is already used for different content")
            return previous
        self._events[idempotency_key] = event
        return event

    @property
    def events(self) -> tuple[IntegrationEvent, ...]:
        return tuple(self._events.values())
