"""Fenced delivery of the Plugin's durable, signed KitsuSync event outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
import json
import os
import re
import secrets
import ssl
import stat
from urllib.parse import urlsplit
from uuid import UUID

from .v1 import canonical_json, signed_event_headers, validate_event_batch
from .share_v1_1 import validate_file_share_batch

MAX_ATTEMPTS = 8
MAX_RESPONSE_BYTES = 16 * 1024
MAX_RETRY_SECONDS = 3600
_KEY_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


@dataclass(frozen=True)
class EventDeliveryConfig:
    endpoint: str
    key_id: str
    secret: bytes
    contract_version_pin: str = "1.0"

    def __post_init__(self):
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or parsed.path != "/api/integrations/vfx-files/v1/events"
            or not _KEY_ID.fullmatch(self.key_id)
            or not isinstance(self.secret, bytes) or len(self.secret) < 32
            or self.contract_version_pin not in {"1.0", "1.1"}
        ):
            raise ValueError("KitsuSync event delivery configuration is invalid")


def load_event_delivery_config(environ=None) -> EventDeliveryConfig | None:
    """Load worker-only event credentials from a protected file, never env value."""
    env = os.environ if environ is None else environ
    endpoint = env.get("VFX_FILES_KITSUSYNC_EVENT_ENDPOINT")
    key_id = env.get("VFX_FILES_KITSUSYNC_EVENT_KEY_ID")
    secret_path = env.get("VFX_FILES_KITSUSYNC_EVENT_HMAC_KEY_FILE")
    contract_version_pin = env.get("VFX_FILES_KITSUSYNC_EVENT_CONTRACT_VERSION", "1.0")
    if not endpoint and not key_id and not secret_path:
        return None
    if not endpoint or not key_id or not secret_path:
        raise RuntimeError("KitsuSync event delivery configuration is incomplete")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(secret_path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
            or (os.name == "posix" and metadata.st_uid != os.geteuid())
        ):
            os.close(descriptor)
            raise ValueError
        with os.fdopen(descriptor, "rb") as key_file:
            secret = key_file.read(4097)
        if len(secret) > 4096 or len(secret) < 32:
            raise ValueError
        return EventDeliveryConfig(endpoint, key_id, secret, contract_version_pin)
    except (OSError, ValueError):
        raise RuntimeError("KitsuSync event delivery key file is unavailable or insufficiently protected") from None


class HttpsEventReceiver:
    """Small HTTPS client with redirects disabled and a bounded response body."""

    def __init__(self, *, timeout_seconds=10):
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("Event receiver timeout is outside the allowed range")
        self.timeout_seconds = timeout_seconds

    def post(self, endpoint, body, headers):
        parsed = urlsplit(endpoint)
        connection = http.client.HTTPSConnection(
            parsed.hostname, parsed.port or 443, timeout=self.timeout_seconds,
            context=ssl.create_default_context(),
        )
        try:
            connection.request(
                "POST", parsed.path, body=body,
                headers={**headers, "Content-Type": "application/json", "Accept": "application/json"},
            )
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                return response.status, None
            try:
                payload = json.loads(data.decode("utf-8")) if data else None
            except (UnicodeDecodeError, ValueError):
                payload = None
            return response.status, payload
        finally:
            connection.close()


def _retry_delay(attempts: int) -> int:
    return min(MAX_RETRY_SECONDS, 5 * (2 ** min(max(attempts - 1, 0), 10)))


def _valid_acceptance(status, response, event_id, event_type=None):
    if status != 202 or not isinstance(response, dict) or set(response) != {"contract_version", "results"}:
        return None
    results = response.get("results")
    if response.get("contract_version") != "1.0" or not isinstance(results, list) or len(results) != 1:
        return None
    result = results[0]
    if not isinstance(result, dict) or set(result) != {"event_id", "outcome", "code"}:
        return None
    if result.get("event_id") != event_id or result.get("outcome") not in ("accepted", "duplicate", "quarantined"):
        return None
    if result.get("code") is not None and (not isinstance(result["code"], str) or len(result["code"]) > 80):
        return None
    if event_type in {
        "storage_authority.deactivation_requested", "storage_authority.activation_requested",
    }:
        if result["outcome"] == "accepted" and result.get("code") != "activation_barrier_complete":
            return None
        if result["outcome"] == "duplicate" and result.get("code") != "duplicate_event":
            return None
    return result


def dispatch_one(repository, receiver, config: EventDeliveryConfig, *, worker_id: UUID, now=None):
    """Send one leased event; retry only transient failures and never log payloads."""
    row = repository.claim_next(worker_id)
    if row is None:
        return None
    if not repository.is_current_activation(row):
        return "quarantined" if repository.finish(
            row, worker_id, "quarantined", response_code="activation_fence_changed",
        ) else "lease_lost"

    version = row.payload.get("contract_version")
    try:
        if version == "1.1" and row.event_type == "file_share.created":
            validate_file_share_batch({"contract_version": "1.1", "events": [row.payload]})
        elif version == "1.0":
            validate_event_batch({"contract_version": "1.0", "events": [row.payload]})
        else:
            raise ValueError
        if row.payload.get("event_id") != row.event_id:
            raise ValueError
        body = canonical_json({"contract_version": version, "events": [row.payload]})
    except (TypeError, ValueError):
        return "quarantined" if repository.finish(
            row, worker_id, "quarantined", response_code="invalid_outbox_event",
        ) else "lease_lost"
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    idempotency_key = row.event_id
    if version == "1.1":
        import hashlib
        idempotency_key = f"vfx-share-batch-{hashlib.sha256(row.event_id.encode('ascii')).hexdigest()}"
    try:
        headers = signed_event_headers(
            key_id=config.key_id,
            secret=config.secret,
            timestamp=timestamp,
            nonce=secrets.token_urlsafe(24).replace("=", ""),
            raw_body=body,
            idempotency_key=idempotency_key,
        )
        status, response = receiver.post(config.endpoint, body, headers)
    except Exception:
        retry = row.attempts < MAX_ATTEMPTS
        finished = repository.finish(
            row, worker_id, "retry" if retry else "quarantined",
            response_code="transport_error",
            retry_after_seconds=_retry_delay(row.attempts) if retry else None,
        )
        return ("retry" if retry else "quarantined") if finished else "lease_lost"

    result = _valid_acceptance(status, response, row.event_id, row.payload.get("event_type"))
    if result is not None:
        if result["outcome"] == "quarantined":
            return "quarantined" if repository.finish(
                row, worker_id, "quarantined", response_code="receiver_quarantined",
            ) else "lease_lost"
        return "delivered" if repository.finish(
            row, worker_id, "delivered", response_code="202",
        ) else "lease_lost"

    if isinstance(status, int) and 400 <= status < 500 and status not in (408, 425, 429):
        return "quarantined" if repository.finish(
            row, worker_id, "quarantined", response_code=f"http_{status}",
        ) else "lease_lost"
    retry = row.attempts < MAX_ATTEMPTS
    finished = repository.finish(
        row, worker_id, "retry" if retry else "quarantined",
        response_code=f"http_{status}" if isinstance(status, int) and 100 <= status <= 599 else "invalid_response",
        retry_after_seconds=_retry_delay(row.attempts) if retry else None,
    )
    return ("retry" if retry else "quarantined") if finished else "lease_lost"


class SqlAlchemyEventOutbox:
    """PostgreSQL row-locked and lease-fenced adapter for the existing outbox table."""

    def __init__(self, session_factory, *, lease_seconds=60, contract_version_pin="1.0"):
        if not 15 <= lease_seconds <= 3600:
            raise ValueError("Event outbox lease duration is outside the allowed range")
        if contract_version_pin not in {"1.0", "1.1"}:
            raise ValueError("Event outbox contract version pin is invalid")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds
        self.contract_version_pin = contract_version_pin

    def claim_next(self, worker_id):
        from datetime import timedelta
        from sqlalchemy import func, or_, select, update
        from ..models import KitsuSyncEventOutbox

        with self.session_factory.begin() as session:
            exhausted = or_(
                KitsuSyncEventOutbox.state.in_(("queued", "retry")),
                (KitsuSyncEventOutbox.state == "leased") & (KitsuSyncEventOutbox.lease_expires_at < func.now()),
            )
            session.execute(update(KitsuSyncEventOutbox).where(
                exhausted,
                KitsuSyncEventOutbox.attempts >= MAX_ATTEMPTS,
            ).values(
                state="quarantined", lease_owner=None, lease_expires_at=None,
                last_response_code="max_attempts_exhausted",
            ))
            row = session.scalar(
                select(KitsuSyncEventOutbox).where(
                    KitsuSyncEventOutbox.state.in_(("queued", "retry", "leased")),
                    KitsuSyncEventOutbox.available_at <= func.now(),
                    KitsuSyncEventOutbox.attempts < MAX_ATTEMPTS,
                    or_(KitsuSyncEventOutbox.event_type != "file_share.created", self.contract_version_pin == "1.1"),
                    or_(KitsuSyncEventOutbox.state != "leased", KitsuSyncEventOutbox.lease_expires_at < func.now()),
                ).order_by(KitsuSyncEventOutbox.available_at, KitsuSyncEventOutbox.created_at, KitsuSyncEventOutbox.id)
                .with_for_update(skip_locked=True).limit(1)
            )
            if row is None:
                return None
            row.state = "leased"
            row.lease_owner = worker_id
            row.lease_generation += 1
            row.lease_expires_at = session.scalar(select(func.now())) + timedelta(seconds=self.lease_seconds)
            row.attempts += 1
            if row.event_type == "file_share.created" and self.contract_version_pin == "1.1":
                from ..models import LinkShareTarget
                target = session.scalar(select(LinkShareTarget).where(LinkShareTarget.event_id == row.event_id).with_for_update())
                if target is not None and target.notification_status == "blocked_receiver_version":
                    target.notification_status = "pending"
            session.flush()
            from types import SimpleNamespace
            return SimpleNamespace(
                id=row.id, production_id=row.production_id, event_id=row.event_id,
                storage_connection_id=row.storage_connection_id,
                event_type=row.event_type, payload=dict(row.payload), attempts=row.attempts,
                lease_generation=row.lease_generation,
            )

    def is_current_activation(self, row):
        from .storage_authority import effective_drive_authority

        try:
            event_epoch = row.payload["activation_epoch"]
            if isinstance(event_epoch, bool) or not isinstance(event_epoch, int) or event_epoch < 1:
                return False
            with self.session_factory() as session:
                state = effective_drive_authority(session, row.production_id)
                event_type = getattr(row, "event_type", "")
                if event_type in {
                    "storage_authority.deactivation_requested", "storage_authority.activation_requested",
                }:
                    next_epoch = row.payload.get("new_activation_epoch")
                    target_enabled = event_type == "storage_authority.activation_requested"
                    from ..models import ProductionDriveAuthority
                    authority = session.get(ProductionDriveAuthority, row.production_id)
                    return bool(
                        authority is not None and authority.owner == "plugin"
                        and authority.activation_transition_pending and not authority.activation_enabled
                        and state is not None and state["activation_epoch"] == next_epoch
                        and event_epoch + (0 if target_enabled else 1) == next_epoch
                    )
                return bool(
                    state is not None and state["activation_enabled"]
                    and state["activation_epoch"] == event_epoch
                    and state["connection_id"] is not None
                    and row.storage_connection_id is not None
                    and UUID(str(row.storage_connection_id)) == UUID(state["connection_id"])
                )
        except (KeyError, TypeError, ValueError):
            return False

    def finish(self, row, worker_id, state, *, response_code, retry_after_seconds=None):
        from datetime import timedelta
        from sqlalchemy import func, select, update
        from ..models import KitsuSyncEventOutbox

        if state not in ("delivered", "retry", "quarantined"):
            raise ValueError("Unsupported event outbox state")
        if retry_after_seconds is not None and not 1 <= retry_after_seconds <= MAX_RETRY_SECONDS:
            raise ValueError("Event retry delay is outside the allowed range")
        with self.session_factory.begin() as session:
            query = update(KitsuSyncEventOutbox).where(
                KitsuSyncEventOutbox.id == row.id,
                KitsuSyncEventOutbox.state == "leased",
                KitsuSyncEventOutbox.lease_owner == worker_id,
                KitsuSyncEventOutbox.lease_generation == row.lease_generation,
                KitsuSyncEventOutbox.lease_expires_at > func.now(),
            ).values(
                state=state, lease_owner=None, lease_expires_at=None,
                last_response_code=response_code[:64],
                available_at=(func.now() + timedelta(seconds=retry_after_seconds)) if retry_after_seconds else func.now(),
            )
            changed = session.execute(query).rowcount == 1
            if changed and getattr(row, "event_type", "") == "file_share.created":
                from ..models import LinkShareTarget
                target = session.scalar(select(LinkShareTarget).where(LinkShareTarget.event_id == row.event_id).with_for_update())
                if target is not None:
                    if state == "delivered":
                        target.notification_status = "inbox_accepted"
                    elif state == "quarantined":
                        target.notification_status = "quarantined"
            if changed and state == "delivered" and getattr(row, "event_type", "") in {
                "storage_authority.deactivation_requested", "storage_authority.activation_requested",
            }:
                from ..models import ProductionDriveAuthority

                new_epoch = row.payload.get("new_activation_epoch")
                if isinstance(new_epoch, int) and not isinstance(new_epoch, bool):
                    authority = session.scalar(select(ProductionDriveAuthority).where(
                        ProductionDriveAuthority.production_id == row.production_id,
                        ProductionDriveAuthority.owner == "plugin",
                        ProductionDriveAuthority.activation_epoch == new_epoch,
                        ProductionDriveAuthority.activation_transition_pending.is_(True),
                    ).with_for_update())
                    if authority is not None:
                        requested = bool(authority.desired_config.get("activation", {}).get("enabled", False)) if isinstance(authority.desired_config, dict) else False
                        authority.activation_enabled = requested
                        authority.activation_transition_pending = False
                        from ..models import SharedTemplateConfiguration
                        template = session.scalar(select(SharedTemplateConfiguration).where(
                            SharedTemplateConfiguration.production_id == row.production_id,
                        ).with_for_update())
                        if template is not None:
                            template.enabled = requested
            return changed
