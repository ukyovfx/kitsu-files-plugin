"""Durable, explicit KitsuSync v1 hierarchy Preview and Apply operations."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from .snapshots import run_snapshot_idempotent
from .v1 import ContractValidationError, canonical_json

PREVIEW_TTL = timedelta(minutes=15)
MAX_PREVIEW_CHANGES = 500
_OBJECT_TYPES = {"production", "episode", "sequence", "shot", "asset", "task"}


def _as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class SyncOperationError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def validate_preview_request(payload):
    if not isinstance(payload, dict) or set(payload) != {"contract_version", "config_revision", "snapshot_id"}:
        raise ContractValidationError("Sync Preview request does not match the published schema")
    if payload["contract_version"] != "1.0":
        raise ContractValidationError("Unsupported contract version")
    if isinstance(payload["config_revision"], bool) or not isinstance(payload["config_revision"], int) or payload["config_revision"] < 1:
        raise ContractValidationError("Configuration revision is invalid")
    _uuid(payload["snapshot_id"], "snapshot_id")
    return payload


def validate_apply_request(payload):
    if not isinstance(payload, dict) or set(payload) != {"contract_version", "preview_id", "config_revision"}:
        raise ContractValidationError("Sync Apply request does not match the published schema")
    if payload["contract_version"] != "1.0":
        raise ContractValidationError("Unsupported contract version")
    if isinstance(payload["config_revision"], bool) or not isinstance(payload["config_revision"], int) or payload["config_revision"] < 1:
        raise ContractValidationError("Configuration revision is invalid")
    _uuid(payload["preview_id"], "preview_id")
    return payload


def validate_operator_command(payload):
    if not isinstance(payload, dict) or set(payload) - {"contract_version", "reason_code"} or payload.get("contract_version") != "1.0":
        raise ContractValidationError("Operator command does not match the published schema")
    if "reason_code" in payload and (not isinstance(payload["reason_code"], str) or len(payload["reason_code"]) > 80):
        raise ContractValidationError("Operator reason code is invalid")
    return payload


def _uuid(value, field):
    try:
        parsed = UUID(str(value))
        if str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise ContractValidationError(f"{field} must be a canonical UUID") from None


def _scope_selected(obj, desired):
    policy = desired.get("sync_policy", {})
    scope = policy.get("scope", {})
    kind, object_id = obj["object_type"], obj["id"]
    if kind == "production":
        selected = True
    elif kind in {"episode", "sequence", "shot", "asset", "task"}:
        selected = bool(scope.get({"episode": "episodes", "sequence": "sequences", "shot": "shots", "asset": "assets", "task": "tasks"}[kind], False))
    else:
        selected = False
    if not obj["included_in_sync"]:
        selected = False
    if object_id in scope.get("exclude_ids", []):
        selected = False
    task_type_ids = scope.get("task_type_ids")
    if kind == "task" and task_type_ids and obj.get("task_type_id") not in task_type_ids:
        selected = False
    include_ids = scope.get("include_ids")
    if include_ids and object_id not in include_ids and kind != "production":
        selected = False
    return selected


def _scope_included(obj, desired, objects_by_id=None):
    if not _scope_selected(obj, desired):
        return False
    if objects_by_id is None:
        return True
    parent_id = obj.get("parent_id")
    visited = set()
    while parent_id is not None:
        if parent_id in visited:
            return False
        visited.add(parent_id)
        parent = objects_by_id.get(parent_id)
        if parent is None or not _scope_selected(parent, desired):
            return False
        parent_id = parent.get("parent_id")
    return True


def _template_supports(obj, objects_by_id, role_mappings, selected_scopes=None):
    kind = obj["object_type"]
    if kind == "production":
        return True
    if selected_scopes is not None and kind not in selected_scopes:
        return False
    if kind in {"shot", "asset", "sequence"}:
        return kind in role_mappings
    if kind == "task":
        parent = objects_by_id.get(obj.get("parent_id"))
        if selected_scopes is not None and parent is not None and parent["object_type"] not in selected_scopes:
            return False
        if "sequence" in role_mappings and selected_scopes is not None and "sequence" not in selected_scopes:
            return False
        return bool(
            "task" in role_mappings and parent is not None
            and parent["object_type"] in {"shot", "asset"}
            and parent["object_type"] in role_mappings
        )
    return False


def selected_template_scopes(desired):
    selections = desired.get("template_selection", {}).get("selections", [])
    try:
        from .template_materialization import validate_sync_template_scope
        policy = desired.get("sync_policy", {})
        validate_sync_template_scope(
            selections, policy.get("scope", {}), allow_empty=False,
        )
    except ValueError as exc:
        if "revision" in str(exc):
            raise SyncOperationError(409, "stale_config") from None
        raise SyncOperationError(409, "invalid_event") from None
    return {item["scope"] for item in selections}


def _binding_scope(production_id, obj):
    kind = obj["object_type"]
    if kind == "production":
        return f"production:{production_id}:root"
    return f"production:{production_id}:{kind}:{obj['id']}"


def build_sync_diff(*, production_id: UUID, root_id: str, desired: dict, objects, bindings,
                    role_mappings=None):
    """Plan only stable-ID operations; names never adopt or reconnect folders."""
    require_supported_sync_mode(desired)
    by_scope = {row.canonical_scope_key: row for row in bindings}
    role_mappings = role_mappings or {}
    selected_scopes = selected_template_scopes(desired)
    objects_by_id = {obj["id"]: obj for obj in objects}
    materialized_ids = set()
    for candidate in objects:
        if (
            candidate["object_type"] != "task"
            or not _scope_included(candidate, desired, objects_by_id)
            or not _template_supports(candidate, objects_by_id, role_mappings, selected_scopes)
        ):
            continue
        current = candidate
        visited = set()
        while current is not None and current["id"] not in visited:
            visited.add(current["id"])
            materialized_ids.add(current["id"])
            parent_id = current.get("parent_id")
            current = objects_by_id.get(parent_id) if parent_id is not None else None
    changes = []
    summary = {"existing": 0, "create": 0, "link": 0, "excluded": 0, "warnings": 0}
    ordered = sorted(objects, key=lambda obj: (obj["object_type"], obj["id"]))
    for obj in ordered:
        kind = obj["object_type"]
        if kind not in _OBJECT_TYPES:
            summary["excluded"] += 1
            continue
        selected = _scope_included(obj, desired, objects_by_id)
        scope_key = _binding_scope(production_id, obj)
        binding = by_scope.get(scope_key)
        parent_id = obj.get("parent_id")
        display_name = obj["name"]
        if not selected:
            action, reason = "exclude", "excluded by current sync scope"
        elif kind == "production" and obj["id"] == str(production_id):
            action, reason = "existing", "maps to the configured verified Production root"
        elif not _template_supports(obj, objects_by_id, role_mappings, selected_scopes):
            action, reason = "warning", "the active shared template has no safe role mapping for this Kitsu object"
        elif kind != "task" and obj["id"] not in materialized_ids:
            action, reason = "warning", "no selected compatible Task will materialize this hierarchy folder"
        elif binding is not None and binding.state == "ready" and binding.provider_folder_id:
            action, reason = "existing", "stable Kitsu UUID already has a ready folder binding"
        elif binding is not None:
            action, reason = "warning", "existing folder reservation requires reconciliation"
        else:
            action, reason = "create", "included by current scope; create through the configured template"
        summary[action if action in summary else "warnings"] += 1
        if len(changes) < MAX_PREVIEW_CHANGES:
            changes.append({
                "action": action,
                "object_type": kind,
                "object_id": obj["id"],
                "display_name": display_name[:240],
                "parent_id": parent_id,
                "reason": reason[:500],
            })
    return summary, changes


def require_supported_sync_mode(desired):
    """Fail closed until every KitsuSync folder policy has safe semantics.

    ``adopt_existing`` needs provider identity evidence and ``future_only``
    needs a durable creation boundary. Neither can share the new-Production
    creation plan safely.
    """
    policy = desired.get("sync_policy") if isinstance(desired, dict) else None
    if not isinstance(policy, dict) or policy.get("mode") != "new_production":
        raise SyncOperationError(409, "invalid_event")


def _models():
    from ..models import (
        FolderBinding,
        Job,
        KitsuSyncDesiredConfiguration,
        KitsuSyncHierarchySnapshot,
        KitsuSyncHierarchySnapshotPage,
        KitsuSyncSyncPreview,
        SharedTemplateConfiguration,
        StorageConnection,
    )
    return FolderBinding, Job, KitsuSyncDesiredConfiguration, KitsuSyncHierarchySnapshot, KitsuSyncHierarchySnapshotPage, KitsuSyncSyncPreview, SharedTemplateConfiguration, StorageConnection


def _require_current_config(session, production_id, config_revision, *, require_active=True):
    _FolderBinding, _Job, Desired, _Snapshot, _Page, _Preview, _Template, Connection = _models()
    config = session.scalar(select(Desired).where(Desired.production_id == production_id).with_for_update())
    if config is None or config.config_revision != config_revision:
        raise SyncOperationError(409, "stale_config")
    require_supported_sync_mode(config.desired_config)
    activation = config.desired_config.get("activation", {})
    drive = config.desired_config.get("drive", {})
    if require_active and not activation.get("enabled"):
        raise SyncOperationError(409, "connection_not_ready")
    connection_id = _uuid(config.desired_config.get("connection_id"), "connection_id")
    connection = session.scalar(select(Connection).where(Connection.id == connection_id).with_for_update())
    if (
        connection is None or connection.production_id != production_id or not connection.enabled
        or connection.allowed_root_id != drive.get("root_id") or drive.get("root_state") != "verified"
    ):
        raise SyncOperationError(409, "root_unverified")
    if connection.account_mode == "my_drive" and (not connection.owner_subject or connection.owner_subject != drive.get("owner_subject")):
        raise SyncOperationError(409, "root_unverified")
    if connection.account_mode == "shared_drive" and connection.drive_id != drive.get("drive_id"):
        raise SyncOperationError(409, "root_unverified")
    return config, connection, int(activation.get("notification_epoch", config.notification_epoch))


def _snapshot_objects(session, snapshot):
    _FolderBinding, _Job, _Desired, _Snapshot, Page, _Preview, _Template, _Connection = _models()
    pages = session.scalars(select(Page).where(Page.snapshot_id == snapshot.id).order_by(Page.page_index)).all()
    return [obj for page in pages for obj in page.objects]


def _preview_payload(row):
    return {
        "contract_version": "1.0", "preview_id": str(row.id),
        "production_id": str(row.production_id), "config_revision": row.config_revision,
        "root_id": row.root_id, "state": "preview_ready", "summary": dict(row.summary),
        "changes": list(row.changes),
        "expires_at": _as_utc(row.expires_at).isoformat().replace("+00:00", "Z"),
    }


def create_sync_preview(session, *, production_id: UUID, actor_id: UUID, payload: dict, idempotency_key: str):
    payload = validate_preview_request(payload)
    body_bytes = canonical_json(payload)
    request_hash = hashlib.sha256(body_bytes).hexdigest()
    FolderBinding, _Job, Desired, Snapshot, _Page, Preview, Template, _Connection = _models()
    def operation():
        # Preview is read-only and is part of configuration review; the
        # contract permits it before activation. Apply and the Worker retain
        # the default active/epoch/root fences.
        config, connection, epoch = _require_current_config(
            session, production_id, payload["config_revision"], require_active=False,
        )
        selected_template_scopes(dict(config.desired_config))
        snapshot_id = _uuid(payload["snapshot_id"], "snapshot_id")
        snapshot = session.scalar(select(Snapshot).where(Snapshot.id == snapshot_id).with_for_update())
        if snapshot is None or snapshot.production_id != production_id:
            raise SyncOperationError(404, "invalid_event")
        if snapshot.config_revision != config.config_revision:
            raise SyncOperationError(409, "stale_config")
        if snapshot.state != "complete":
            raise SyncOperationError(409, "stale_source_snapshot")
        template = session.scalar(select(Template).where(
            Template.production_id == production_id,
            Template.storage_connection_id == connection.id,
            Template.allowed_root_id == connection.allowed_root_id,
            Template.enabled.is_(True),
        ))
        if template is None:
            raise SyncOperationError(409, "connection_not_ready")
        objects = _snapshot_objects(session, snapshot)
        bindings = session.scalars(select(FolderBinding).where(
            FolderBinding.storage_connection_id == connection.id,
            FolderBinding.allowed_root_id == connection.allowed_root_id,
        )).all()
        summary, changes = build_sync_diff(
            production_id=production_id, root_id=connection.allowed_root_id,
            desired=dict(config.desired_config), objects=objects, bindings=bindings,
            role_mappings=dict(template.role_mappings),
        )
        row = Preview(
            id=uuid4(), production_id=production_id, snapshot_id=snapshot.id,
            template_configuration_id=template.id, template_version=template.version,
            config_revision=config.config_revision, activation_epoch=epoch,
            storage_connection_id=connection.id, root_id=connection.allowed_root_id,
            summary=summary, changes=changes, state="preview_ready",
            expires_at=datetime.now(timezone.utc) + PREVIEW_TTL, created_by=actor_id,
        )
        session.add(row)
        session.flush()
        return _preview_payload(row), 200, None
    return run_snapshot_idempotent(
        session, production_id=production_id, actor_id=actor_id,
        idempotency_key=idempotency_key, operation="sync.preview",
        request_body={"body_sha256": request_hash}, apply=operation,
    )


def _job_payload(job):
    payload = dict(job.payload)
    state = {"leased": "running", "retry": "queued", "blocked_authorization": "failed"}.get(job.state, job.state)
    total = payload.get("progress", {}).get("total", 0)
    completed = payload.get("progress", {}).get("completed", 0)
    return {
        "contract_version": "1.0", "id": str(job.id),
        "production_id": payload["production_id"], "config_revision": payload["config_revision"],
        "state": state, "progress": {"completed": completed, "total": total},
        "failure_code": job.last_error_code,
        "created_at": _as_utc(job.created_at).isoformat().replace("+00:00", "Z"),
        "updated_at": _as_utc(job.updated_at).isoformat().replace("+00:00", "Z"),
    }


def apply_sync_preview(session, *, production_id: UUID, actor_id: UUID, payload: dict, idempotency_key: str):
    payload = validate_apply_request(payload)
    _FolderBinding, Job, _Desired, Snapshot, _Page, Preview, Template, _Connection = _models()
    def operation():
        config, connection, epoch = _require_current_config(session, production_id, payload["config_revision"])
        selected_template_scopes(dict(config.desired_config))
        preview = session.scalar(select(Preview).where(Preview.id == _uuid(payload["preview_id"], "preview_id")).with_for_update())
        if preview is None or preview.production_id != production_id:
            raise SyncOperationError(404, "invalid_event")
        if _as_utc(preview.expires_at) <= datetime.now(timezone.utc):
            raise SyncOperationError(409, "preview_expired")
        if preview.state != "preview_ready":
            raise SyncOperationError(409, "preview_mismatch")
        snapshot = session.get(Snapshot, preview.snapshot_id)
        if snapshot is None or snapshot.state != "complete" or snapshot.production_id != production_id:
            raise SyncOperationError(409, "stale_source_snapshot")
        shared_template = session.scalar(select(Template).where(
            Template.production_id == production_id,
            Template.storage_connection_id == connection.id,
            Template.allowed_root_id == connection.allowed_root_id,
            Template.enabled.is_(True),
        ))
        if shared_template is None:
            raise SyncOperationError(409, "connection_not_ready")
        if (
            (preview.config_revision, preview.activation_epoch, preview.storage_connection_id, preview.root_id)
            != (config.config_revision, epoch, connection.id, connection.allowed_root_id)
            or preview.template_configuration_id != shared_template.id
            or preview.template_version != shared_template.version
        ):
            raise SyncOperationError(409, "stale_config")
        snapshot_objects = _snapshot_objects(session, snapshot)
        bindings = session.scalars(select(_FolderBinding).where(
            _FolderBinding.storage_connection_id == connection.id,
            _FolderBinding.allowed_root_id == connection.allowed_root_id,
        )).all()
        _summary, _display_plan = build_sync_diff(
            production_id=production_id, root_id=connection.allowed_root_id,
            desired=dict(config.desired_config), objects=snapshot_objects, bindings=bindings,
            role_mappings=dict(shared_template.role_mappings),
        )
        # The public preview is capped at 500 display rows. The worker derives
        # its complete bounded task set from the immutable snapshot instead.
        # The overall action count includes other entities; determine Task
        # count directly with the same scope/binding rules and no copied plan.
        binding_scopes = {item.canonical_scope_key: item for item in bindings}
        objects_by_id = {item["id"]: item for item in snapshot_objects}
        tasks = sum(
            1 for obj in snapshot_objects
            if obj["object_type"] == "task" and _scope_included(obj, dict(config.desired_config), objects_by_id)
            and _template_supports(obj, objects_by_id, dict(shared_template.role_mappings), selected_template_scopes(dict(config.desired_config)))
            and (
                _binding_scope(production_id, obj) not in binding_scopes
                or binding_scopes[_binding_scope(production_id, obj)].state != "ready"
            )
        )
        job_id = uuid4()
        job = Job(
            id=job_id, job_type="sync_hierarchy", idempotency_key=f"kitsusync-sync:{production_id}:{idempotency_key}",
            payload={
                "job_type": "sync_hierarchy",
                "actor_id": str(actor_id), "production_id": str(production_id),
                "config_revision": config.config_revision, "activation_epoch": epoch,
                "connection_id": str(connection.id), "root_id": connection.allowed_root_id,
                "snapshot_id": str(snapshot.id), "preview_id": str(preview.id),
                "configuration_id": str(shared_template.id), "configuration_version": shared_template.version,
                "progress": {"completed": 0, "total": tasks}, "retry_count": 0,
            },
            state="queued", max_attempts=5,
        )
        session.add(job)
        preview.state = "applied"
        session.flush()
        return _job_payload(job), 202, None
    return run_snapshot_idempotent(
        session, production_id=production_id, actor_id=actor_id,
        idempotency_key=idempotency_key, operation="sync.apply", request_body=payload, apply=operation,
    )


def get_sync_job(session, *, job_id: UUID, production_id: UUID, actor_id: UUID):
    _FolderBinding, Job, _Desired, _Snapshot, _Page, _Preview, _Template, _Connection = _models()
    job = session.scalar(select(Job).where(Job.id == job_id))
    if job is None or job.payload.get("production_id") != str(production_id):
        raise SyncOperationError(404, "invalid_event")
    if job.payload.get("actor_id") != str(actor_id):
        raise SyncOperationError(403, "production_access_denied")
    return _job_payload(job)


def enqueue_sync_job_command(session, *, job_id: UUID, production_id: UUID, actor_id: UUID,
                             command: dict, idempotency_key: str, operation: str):
    command = validate_operator_command(command)
    _FolderBinding, Job, _Desired, _Snapshot, _Page, _Preview, _Template, _Connection = _models()
    def apply():
        original = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if original is None or original.payload.get("production_id") != str(production_id):
            raise SyncOperationError(404, "invalid_event")
        if original.payload.get("actor_id") != str(actor_id):
            raise SyncOperationError(403, "production_access_denied")
        if operation == "retry" and original.state != "failed":
            code = "reconcile_required" if original.state == "reconcile_required" else "invalid_event"
            raise SyncOperationError(409, code)
        if operation == "reconcile" and original.state != "reconcile_required":
            raise SyncOperationError(409, "invalid_event")
        source = dict(original.payload)
        retries = int(source.get("retry_count", 0)) + 1
        if retries > 3:
            raise SyncOperationError(429, "rate_limited")
        kind = "retry" if operation == "retry" else "reconcile"
        new_job = Job(
            id=uuid4(), job_type="sync_hierarchy", idempotency_key=f"kitsusync-sync-{kind}:{production_id}:{idempotency_key}",
            payload={**source, "parent_job_id": str(original.id), "retry_count": retries,
                     "progress": {"completed": 0, "total": source.get("progress", {}).get("total", 0)}},
            state="queued", max_attempts=5,
        )
        session.add(new_job)
        session.flush()
        return _job_payload(new_job), 202, None

    return run_snapshot_idempotent(
        session, production_id=production_id, actor_id=actor_id,
        idempotency_key=idempotency_key, operation=f"sync.{operation}:{job_id}",
        request_body=command, apply=apply,
    )
