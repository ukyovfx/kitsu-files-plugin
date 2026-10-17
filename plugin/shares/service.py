"""Durable, idempotent creation of immutable Drive reference shares."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from uuid import UUID, uuid4

from sqlalchemy import select

from ..integrations.share_v1_1 import ShareContractError, validate_file_share_event
from ..integrations.v1 import canonical_json

_KEY = re.compile(r"^[A-Za-z0-9._~:-]{16,128}$")
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


class ShareConflict(ValueError):
    pass


class ShareDenied(PermissionError):
    pass


def canonical_request_sha256(*, production_id, source_task_id, actor_id, object_id, recipient_task_ids):
    value = {
        "production_id": str(production_id), "source_task_id": str(source_task_id),
        "actor_id": str(actor_id), "object_id": object_id,
        "recipient_task_ids": sorted(str(item) for item in recipient_task_ids),
    }
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_share_request(object_id, recipient_task_ids, idempotency_key):
    if not isinstance(object_id, str) or not _DRIVE_ID.fullmatch(object_id):
        raise ValueError("A valid Drive object ID is required")
    if not isinstance(recipient_task_ids, list) or not 1 <= len(recipient_task_ids) <= 100:
        raise ValueError("Choose between 1 and 100 recipient Tasks")
    parsed = []
    for value in recipient_task_ids:
        try:
            parsed.append(UUID(str(value)))
        except (TypeError, ValueError):
            raise ValueError("A recipient Task ID is invalid") from None
    if len(set(parsed)) != len(parsed):
        raise ValueError("Recipient Tasks must be unique")
    if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
        raise ValueError("A 16 to 128 character Idempotency-Key is required")
    return parsed


def resolve_authorized_tasks(session, *, actor, production_id, source_task_id, recipient_task_ids):
    """Resolve current Zou task/entity/type/department identities and access."""
    from zou.app.models.department import Department
    from zou.app.models.entity import Entity
    from zou.app.models.person import Person
    from zou.app.models.project import Project, ProjectPersonLink, ProjectTaskTypeLink
    from zou.app.models.task import Task, TaskPersonLink
    from zou.app.models.task_type import TaskType

    project = session.get(Project, production_id)
    person = session.get(Person, actor.actor_id)
    if project is None or person is None or not getattr(person, "active", True):
        raise ShareDenied("Production or active Kitsu actor is unavailable")
    role = actor.project_role
    project_member = role == "admin" or session.scalar(select(ProjectPersonLink).where(
        ProjectPersonLink.project_id == production_id, ProjectPersonLink.person_id == actor.actor_id,
    )) is not None
    if not project_member:
        raise ShareDenied("Actor has no current Production membership")
    source = session.scalar(select(Task).where(Task.id == source_task_id, Task.project_id == production_id))
    if source is None:
        raise ShareDenied("Source Task is outside this Production")
    source_type = session.get(TaskType, source.task_type_id)
    entity = session.get(Entity, source.entity_id)
    if (
        source_type is None or source_type.for_entity not in ("Shot", "Asset")
        or entity is None or entity.project_id != production_id
        or session.scalar(select(ProjectTaskTypeLink).where(
            ProjectTaskTypeLink.project_id == production_id,
            ProjectTaskTypeLink.task_type_id == source.task_type_id,
        )) is None
    ):
        raise ShareDenied("Source Task is not a current Shot or Asset Task in this Production")
    if role not in ("admin", "manager") and session.scalar(select(TaskPersonLink).where(
        TaskPersonLink.task_id == source.id, TaskPersonLink.person_id == actor.actor_id,
    )) is None:
        raise ShareDenied("Actor is not assigned to the source Task")

    recipients = []
    for task_id in recipient_task_ids:
        task = session.scalar(select(Task).where(Task.id == task_id, Task.project_id == production_id))
        task_type = session.get(TaskType, task.task_type_id) if task else None
        if (
            task is None or task.id == source.id or task_type is None
            or task_type.for_entity not in ("Shot", "Asset")
            or session.scalar(select(ProjectTaskTypeLink).where(
                ProjectTaskTypeLink.project_id == production_id,
                ProjectTaskTypeLink.task_type_id == task.task_type_id,
            )) is None
        ):
            raise ShareDenied("A recipient Task is not a current Shot or Asset Task in this Production")
        if role not in ("admin", "manager") and session.scalar(select(TaskPersonLink).where(
            TaskPersonLink.task_id == task.id, TaskPersonLink.person_id == actor.actor_id,
        )) is None:
            raise ShareDenied("Actor is not authorized to address a recipient Task")
        department = session.get(Department, task_type.department_id) if task_type.department_id else None
        if task_type.department_id and department is None:
            raise ShareDenied("Recipient Department is no longer available")
        recipients.append({"task": task, "task_type": task_type, "department": department})
    return {"source": source, "source_type": source_type, "entity": entity, "recipients": recipients}


def reserve_share(session, *, production_id, source_task_id, actor_id, connection_id, object_id,
                  source_entity_id, source_entity_type, recipient_task_ids,
                  recipient_task_details, request_key, request_sha256):
    """Persist stable operation/target IDs before provider reads begin."""
    from ..models import LinkShare, LinkShareTarget

    operation = session.scalar(select(LinkShare).where(
        LinkShare.production_id == production_id,
        LinkShare.request_idempotency_key == request_key,
    ).with_for_update())
    if operation is not None:
        if operation.request_sha256 != request_sha256 or operation.actor_id != actor_id:
            raise ShareConflict("Idempotency-Key was already used for a different share request")
        if operation.state == "created":
            targets = session.scalars(select(LinkShareTarget).where(
                LinkShareTarget.share_id == operation.id,
            ).order_by(LinkShareTarget.created_at, LinkShareTarget.id)).all()
            return operation, targets, True
        operation.state = "verifying"
        targets = session.scalars(select(LinkShareTarget).where(
            LinkShareTarget.share_id == operation.id,
        ).order_by(LinkShareTarget.recipient_task_id).with_for_update()).all()
        return operation, targets, False

    operation = LinkShare(
        id=uuid4(), production_id=production_id, source_task_id=source_task_id,
        source_entity_id=source_entity_id, source_entity_type=source_entity_type, actor_id=actor_id,
        storage_connection_id=connection_id, object_id=object_id, object_type="file",
        object_url="", object_snapshot={}, request_idempotency_key=request_key,
        request_sha256=request_sha256, state="verifying",
    )
    session.add(operation)
    session.flush()
    targets = []
    for task_id in sorted(recipient_task_ids, key=str):
        target_id = uuid4()
        target = LinkShareTarget(
            id=target_id, share_id=operation.id, recipient_task_id=task_id,
            recipient_task_type_id=recipient_task_details[task_id]["task_type_id"],
            recipient_department_id=recipient_task_details[task_id]["department_id"],
            event_id=str(uuid4()),
            idempotency_key=f"share-{target_id}-create-v1",
            notification_status="blocked_receiver_version",
        )
        session.add(target)
        targets.append(target)
    session.flush()
    return operation, targets, False


def _timestamp(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _label(value, limit):
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit]


def complete_share(session, *, operation_id, actor, context, connection, drive_object,
                   access_verified_at, notification_pin):
    """Revalidate authority and persist immutable target events + durable outbox."""
    from ..integrations.event_producers import current_notification_epoch
    from ..integrations.v1 import sha256_hex
    from ..models import KitsuSyncEventOutbox, LinkShare, LinkShareTarget, ProductionDriveAuthority, StorageConnection

    operation = session.scalar(select(LinkShare).where(LinkShare.id == operation_id).with_for_update())
    if operation is None:
        raise ShareConflict("Share reservation is unavailable")
    targets = session.scalars(select(LinkShareTarget).where(
        LinkShareTarget.share_id == operation.id,
    ).order_by(LinkShareTarget.recipient_task_id).with_for_update()).all()
    if operation.state == "created":
        return operation, targets
    if operation.actor_id != actor.actor_id:
        raise ShareConflict("Share request actor changed")

    # Revalidate host objects, membership and task authorization immediately
    # before committing the share and its event records.
    requested = [row.recipient_task_id for row in targets]
    current = resolve_authorized_tasks(
        session, actor=actor, production_id=operation.production_id,
        source_task_id=operation.source_task_id, recipient_task_ids=requested,
    )
    state = current_notification_epoch(
        session, production_id=operation.production_id,
        selected_connection_id=operation.storage_connection_id,
    )
    if state is None:
        raise ShareDenied("Production Drive activation is not current")
    authority = session.scalar(select(ProductionDriveAuthority).where(
        ProductionDriveAuthority.production_id == operation.production_id,
    ).with_for_update())
    selected = session.scalar(select(StorageConnection).where(
        StorageConnection.id == operation.storage_connection_id,
        StorageConnection.production_id == operation.production_id,
        StorageConnection.enabled.is_(True),
    ).with_for_update())
    if (
        authority is None or authority.owner != "plugin" or authority.state != "configured"
        or not authority.activation_enabled or authority.activation_transition_pending
        or authority.activation_epoch != state or selected is None
        or authority.connection_id != selected.id or authority.root_id != selected.allowed_root_id
    ):
        raise ShareDenied("Production Drive authority changed during share verification")

    object_id = operation.object_id
    if drive_object is None or drive_object.object_id != object_id or drive_object.trashed:
        raise ShareDenied("Drive object identity or type is unavailable")
    if not isinstance(drive_object.mime_type, str) or not drive_object.mime_type:
        raise ShareDenied("Drive object type is unavailable")
    object_type = "folder" if drive_object.mime_type in ("folder", "application/vnd.google-apps.folder") else "file"
    if getattr(drive_object, "resource_key", None):
        raise ShareDenied("Resource-key-protected Drive objects are unsupported for link sharing")
    if selected.drive_id and drive_object.drive_id != selected.drive_id:
        raise ShareDenied("Drive object is outside the selected Production Drive")
    # The caller already performed ancestry proof; repeat it against the live
    # adapter after this transaction through its validation callback.
    url = (
        f"https://drive.google.com/file/d/{object_id}/view" if object_type == "file"
        else f"https://drive.google.com/drive/folders/{object_id}"
    )
    now = _timestamp()
    metadata = {
        "status": "complete", "observed_at": now,
        "name": _label(drive_object.name, 255), "mime_type": _label(drive_object.mime_type, 127),
        "size_bytes": drive_object.size, "modified_at": drive_object.modified_time,
        "sha256": drive_object.sha256,
    }
    inventory = {
        "status": "unavailable", "observed_at": now, "scope": "object",
        "file_count": None, "observed_file_count_lower_bound": None, "frame_summary": None,
    }
    access = {
        "status": "verified_service_only", "access_verified_at": access_verified_at,
        "check_subject_type": "plugin_service_identity", "check_subject_id": f"connection-{selected.id}",
    }
    source = current["source"]
    source_type = current["source_type"]
    entity = current["entity"]
    entity_type = source_type.for_entity.lower()
    operation.source_entity_id = entity.id
    operation.source_entity_type = entity_type
    operation.object_type = object_type
    operation.object_url = url
    operation.object_snapshot = {"object": {"provider": "google_drive", "object_id": object_id, "object_type": object_type, "url": url}, "metadata": metadata, "inventory": inventory, "access": access}

    recipient_by_id = {item["task"].id: item for item in current["recipients"]}
    epoch = state
    for target in targets:
        recipient_item = recipient_by_id[target.recipient_task_id]
        recipient_task, recipient_type, department = recipient_item["task"], recipient_item["task_type"], recipient_item["department"]
        if (
            target.recipient_task_type_id != recipient_task.task_type_id
            or target.recipient_department_id != (department.id if department else None)
        ):
            raise ShareDenied("Recipient Task routing changed during share verification")
        event = {
            "contract_version": "1.1", "producer_id": "vfx-files-plugin",
            "event_id": target.event_id, "event_type": "file_share.created",
            "occurred_at": now, "production_id": str(operation.production_id),
            "activation_epoch": epoch,
            "source": {
                "entity_type": entity_type, "entity_id": str(entity.id),
                "entity_name": _label(entity.name, 200), "task_id": str(source.id),
                "task_type_id": str(source.task_type_id), "task_type_name": _label(source_type.name, 120),
            },
            "recipient": {
                "task_id": str(recipient_task.id), "task_type_id": str(recipient_task.task_type_id),
                "task_type_name": _label(recipient_type.name, 120),
                "department_id": str(department.id) if department else None,
                "department_name": _label(department.name, 120) if department else None,
            },
            "share": {
                "id": str(operation.id), "share_target_id": str(target.id), "revision": 1,
                "shared_by_id": str(actor.actor_id), "shared_at": now,
                "idempotency_key": target.idempotency_key,
                "object": operation.object_snapshot["object"], "metadata": metadata,
                "inventory": inventory, "access": access,
            },
        }
        validate_file_share_event(event)
        digest = sha256_hex(canonical_json(event))
        target.event_payload = event
        target.event_payload_sha256 = digest
        target.notification_status = "pending" if notification_pin == "1.1" else "blocked_receiver_version"
        existing = session.scalar(select(KitsuSyncEventOutbox).where(KitsuSyncEventOutbox.event_id == target.event_id).with_for_update())
        if existing is None:
            session.add(KitsuSyncEventOutbox(
                id=uuid4(), production_id=operation.production_id,
                storage_connection_id=selected.id, event_id=target.event_id,
                event_type="file_share.created", payload=event,
                payload_sha256=digest, state="queued", attempts=0, lease_generation=0,
            ))
        elif existing.payload_sha256 != digest or existing.production_id != operation.production_id:
            raise ShareConflict("Share event identity already exists with different content")
    operation.state = "created"
    session.flush()
    return operation, targets


def target_response(operation, targets):
    return {
        "share_id": str(operation.id),
        "targets": [{
            "share_target_id": str(row.id), "recipient_task_id": str(row.recipient_task_id),
            "status": "created", "notification_status": row.notification_status,
        } for row in targets],
    }


def event_share_payload(row):
    event = row.event_payload
    share = event["share"]
    return {
        "share_id": share["id"], "share_target_id": share["share_target_id"],
        "source": event["source"], "recipient": event["recipient"],
        "object": share["object"], "metadata": share["metadata"],
        "shared_at": share["shared_at"], "notification_status": row.notification_status,
    }
