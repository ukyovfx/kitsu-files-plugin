"""Contract-limited business event creation bound to KitsuSync-owned config."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid5

from sqlalchemy import select

from .outbox import append_event_batch, SqlAlchemyEventRepository

_EVENT_NAMESPACE = UUID("c1edfb56-6b81-4cc2-94bc-a388c0a19db4")


def _time_text(value):
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def current_notification_epoch(session, *, production_id, selected_connection_id):
    """Require the exact enabled KitsuSync connection/root before emitting."""
    from ..models import KitsuSyncDesiredConfiguration
    from .storage_authority import selected_storage_connection

    desired = session.scalar(select(KitsuSyncDesiredConfiguration).where(
        KitsuSyncDesiredConfiguration.production_id == production_id,
    ).with_for_update())
    if desired is None:
        return None
    config = dict(desired.desired_config)
    activation = config.get("activation") or {}
    drive = config.get("drive") or {}
    try:
        connection_id = UUID(str(config["connection_id"]))
    except (KeyError, TypeError, ValueError):
        return None
    epoch = desired.notification_epoch
    if (
        not activation.get("enabled")
        or isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1
        or activation.get("notification_epoch") != epoch
        or connection_id != UUID(str(selected_connection_id))
        or drive.get("root_state") != "verified"
    ):
        return None
    connection = selected_storage_connection(session, production_id)
    if connection is None or connection.id != connection_id:
        return None
    return epoch


def append_eligible_event(session, event, *, selected_connection_id):
    """Append one immutable contract event; inactive/bootstrap state is silent."""
    production_id = UUID(event["production_id"])
    epoch = current_notification_epoch(
        session, production_id=production_id, selected_connection_id=selected_connection_id,
    )
    if epoch is None:
        return False
    event = dict(event)
    event["activation_epoch"] = epoch
    batch = {"contract_version": "1.0", "events": [event]}
    append_event_batch(
        SqlAlchemyEventRepository(session), batch,
        storage_connection_id=UUID(str(selected_connection_id)),
    )
    return True


def _event_id(production_id, event_type, object_id, epoch):
    return str(uuid5(_EVENT_NAMESPACE, f"{production_id}:{event_type}:{object_id}:{epoch}"))


def append_output_package_event(
    session, package, *, event_type, selected_connection_id, expected_activation_epoch=None,
):
    """Emit package-created/ready only when its complete file list fits v1."""
    from zou.app.models.entity import Entity
    from zou.app.models.person import Person
    from zou.app.models.task import Task
    from zou.app.models.task_type import TaskType
    from ..models import OutputPackageFile

    if event_type not in {"output_package.created", "output_package.ready"}:
        return False
    epoch = current_notification_epoch(
        session, production_id=package.production_id, selected_connection_id=selected_connection_id,
    )
    if epoch is None or expected_activation_epoch is None or expected_activation_epoch != epoch:
        return False
    files = session.scalars(select(OutputPackageFile).where(
        OutputPackageFile.package_id == package.id,
    ).order_by(OutputPackageFile.ordinal)).all()
    # The canonical v1 contract bounds each package event to 100 Drive objects.
    # Do not truncate a large sequence into a misleading partial package event.
    if len(files) > 100 or not files:
        return False
    objects = []
    if event_type == "output_package.ready":
        if any(not row.sha256 or row.byte_count is None or not row.provider_object_id for row in files):
            return False
        objects = [{
            "drive_file_id": row.provider_object_id,
            "classification": package.package_type,
            "size_bytes": row.byte_count,
            "sha256": row.sha256,
        } for row in files]
    elif any(not row.sha256 or row.byte_count is None for row in files):
        return False
    task = session.get(Task, package.task_id)
    entity = session.get(Entity, package.entity_id)
    task_type = session.get(TaskType, task.task_type_id) if task is not None else None
    person = session.get(Person, package.created_by)
    if (
        task is None or entity is None or task_type is None
        or task.project_id != package.production_id or entity.project_id != package.production_id
    ):
        return False
    occurred_at = package.completed_at if event_type == "output_package.ready" else package.created_at
    event = {
        "contract_version": "1.0",
        "producer_id": "vfx-files-plugin",
        "event_id": _event_id(package.production_id, event_type, package.id, epoch),
        "event_type": event_type,
        "occurred_at": _time_text(occurred_at),
        "production_id": str(package.production_id),
        "activation_epoch": epoch,
        "source": {
            "task_id": str(task.id),
            "task_type_id": str(task.task_type_id),
            "task_type_name": task_type.name,
        },
        "sender_person_id": str(package.created_by),
        "output_package": {
            "id": str(package.id),
            "version": package.version,
            "classification": package.package_type,
            "manifest_sha256": package.manifest_sha256,
            "objects": objects,
        },
    }
    if person is not None:
        event["sender_person_id"] = str(person.id)
    return append_eligible_event(session, event, selected_connection_id=selected_connection_id)


def append_new_task_folder_event(
    session, *, production_id, task_id, selected_connection_id, expected_activation_epoch=None,
):
    """Emit only for a newly bound Task folder, never for hierarchy bootstrap."""
    from zou.app.models.task import Task
    from ..models import FolderBinding

    task = session.get(Task, task_id)
    if task is None or task.project_id != production_id:
        return False
    scope = f"production:{production_id}:task:{task_id}"
    binding = session.scalar(select(FolderBinding).where(
        FolderBinding.storage_connection_id == selected_connection_id,
        FolderBinding.canonical_scope_key == scope,
        FolderBinding.state == "ready",
    ))
    if binding is None or not binding.provider_folder_id:
        return False
    epoch = current_notification_epoch(
        session, production_id=production_id, selected_connection_id=selected_connection_id,
    )
    if epoch is None or expected_activation_epoch is None or expected_activation_epoch != epoch:
        return False
    event = {
        "contract_version": "1.0",
        "producer_id": "vfx-files-plugin",
        "event_id": _event_id(production_id, "folder.provisioning.completed", binding.id, epoch),
        "event_type": "folder.provisioning.completed",
        "occurred_at": _time_text(binding.created_at),
        "production_id": str(production_id),
        "activation_epoch": epoch,
        "folder": {
            "object_type": "task",
            "object_id": str(task_id),
            "drive_file_id": binding.provider_folder_id,
            "binding_state": "ready",
        },
    }
    return append_eligible_event(session, event, selected_connection_id=selected_connection_id)
