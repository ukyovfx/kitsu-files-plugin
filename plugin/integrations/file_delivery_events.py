"""Canonical KitsuSync v1 event payloads for recipient deliveries."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid5

_EVENT_NAMESPACE = UUID("a57f9dd0-729b-4376-89d9-69e38787b861")
_ENTITY_TYPES = {"shot": "shot", "asset": "asset", "sequence": "sequence"}
_DELIVERY_STATES = {
    "file_delivery.created": "created",
    "file_delivery.ready": "ready",
    "file_delivery.accepted": "accepted",
    "file_delivery.needs_fix": "needs_fix",
    "file_delivery.revised": "revised",
}


def _uuid_text(value, label: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{label} must be a UUID") from None
    return str(parsed)


def _display_name(person) -> str:
    name = " ".join(
        part.strip() for part in (getattr(person, "first_name", ""), getattr(person, "last_name", ""))
        if isinstance(part, str) and part.strip()
    )
    return name[:160] or _uuid_text(person.id, "Sender ID")


def _event_time(value) -> str:
    if not isinstance(value, datetime):
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_file_delivery_event(
    *, delivery, package, source_task, source_task_type, recipient_task_type,
    entity, entity_type_name, sender, production_name, files,
    event_type: str, activation_epoch: int,
) -> dict:
    """Build one exact v1 per-recipient event; omit comments and URLs."""
    status = _DELIVERY_STATES.get(event_type)
    if status is None:
        raise ValueError("Unsupported file-delivery event type")
    normalized_type = entity_type_name.casefold() if isinstance(entity_type_name, str) else ""
    entity_type = _ENTITY_TYPES.get(normalized_type)
    if entity_type is None:
        raise ValueError("Kitsu entity type is not representable by the v1 file-event contract")
    if isinstance(activation_epoch, bool) or not isinstance(activation_epoch, int) or activation_epoch < 1:
        raise ValueError("Activation epoch must be a positive integer")
    revision = delivery.delivery_revision
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("Delivery revision must be a positive integer")
    if len(files) > 100:
        raise ValueError("The v1 file-event object limit does not allow truncating this delivery manifest")

    delivery_id = _uuid_text(delivery.id, "Delivery ID")
    production_id = _uuid_text(delivery.production_id, "Production ID")
    recipient_task_id = _uuid_text(delivery.recipient_task_id, "Recipient Task ID")
    recipient_task_type_id = _uuid_text(delivery.recipient_task_type_id, "Recipient Task Type ID")
    package_id = _uuid_text(package.id, "Output Package ID")
    source_task_id = _uuid_text(source_task.id, "Source Task ID")
    source_task_type_id = _uuid_text(source_task.task_type_id, "Source Task Type ID")
    sender_id = _uuid_text(sender.id, "Sender ID")
    package_sha256 = package.manifest_sha256
    if not isinstance(package_sha256, str) or len(package_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in package_sha256):
        raise ValueError("Output Package manifest SHA-256 is invalid")

    event_id = str(uuid5(
        _EVENT_NAMESPACE,
        f"{production_id}:{delivery_id}:{revision}:{event_type}",
    ))
    objects = []
    if status in {"created", "ready", "revised"}:
        for row in files:
            object_id = getattr(row, "provider_object_id", None)
            sha256 = getattr(row, "sha256", None)
            size = getattr(row, "byte_count", None)
            classification = getattr(row, "file_format", None) or package.package_type
            if (
                not isinstance(object_id, str) or not object_id or len(object_id) > 200
                or not isinstance(sha256, str) or len(sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in sha256)
                or isinstance(size, bool) or not isinstance(size, int) or size < 0
                or not isinstance(classification, str) or len(classification) > 100
            ):
                raise ValueError("Output Package object metadata is incomplete")
            objects.append({
                "drive_file_id": object_id,
                "classification": classification,
                "size_bytes": size,
                "sha256": sha256,
            })

    return {
        "contract_version": "1.0",
        "producer_id": "vfx-files-plugin",
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": _event_time(getattr(delivery, "updated_at", None)),
        "production_id": production_id,
        "activation_epoch": activation_epoch,
        "production_name": str(production_name)[:240],
        "entity": {
            "type": entity_type,
            "id": _uuid_text(entity.id, "Entity ID"),
            "name": str(entity.name)[:240],
            "code": getattr(entity, "code", None)[:120] if isinstance(getattr(entity, "code", None), str) else None,
        },
        "source": {
            "id": source_task_id,
            "task_type_id": source_task_type_id,
            "task_type_name": str(source_task_type.name)[:160],
        },
        "sender": {"id": sender_id, "display_name": _display_name(sender)},
        "output_package": {
            "id": package_id,
            "version": package.version,
            "classification": str(package.package_type)[:100],
            "manifest_sha256": package_sha256,
            "frame_range": None,
            "objects": objects,
        },
        "delivery": {
            "id": delivery_id,
            "revision": revision,
            "status": status,
            "recipient_task_id": recipient_task_id,
            "recipient_task_type_id": recipient_task_type_id,
            "recipient_task_type_name": str(recipient_task_type.name)[:160],
        },
    }
