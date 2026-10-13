"""Leased worker execution for an explicitly applied, immutable hierarchy Preview."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from ..folders.provisioning import provision_task_folders
from ..jobs.queue import JobResult
from .sync import (
    SyncOperationError,
    _binding_scope,
    _models,
    _require_current_config,
    _scope_included,
    _snapshot_objects,
    _template_supports,
    selected_template_scopes,
)
from .v1 import validate_snapshot_production


def _authorized_manager(actor_id, production_id):
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink

    actor = Person.query.filter_by(id=actor_id).one_or_none()
    if actor is None or not getattr(actor, "active", True):
        return False
    membership = ProjectPersonLink.query.filter_by(project_id=production_id, person_id=actor_id).one_or_none()
    role = (membership.role if membership is not None else None) or actor.role
    return getattr(role, "code", role) == "admin" or (getattr(role, "code", role) == "manager" and membership is not None)


def _snapshot_task_matches_kitsu(task_object, production_id):
    from zou.app.models.entity import Entity
    from zou.app.models.task import Task
    from zou.app.models.task_type import TaskType

    task = Task.query.filter_by(id=UUID(task_object["id"])).one_or_none()
    if task is None or task.project_id != production_id or str(task.task_type_id) != task_object.get("task_type_id"):
        return False
    task_type = TaskType.query.filter_by(id=task.task_type_id).one_or_none()
    entity = Entity.query.filter_by(id=task.entity_id).one_or_none()
    return bool(
        task_type is not None and task_type.for_entity in ("Shot", "Asset")
        and entity is not None and entity.project_id == production_id
    )


def make_hierarchy_sync_handler(session_factory, adapter):
    def handle(leased_job):
        payload = leased_job.payload
        try:
            production_id = UUID(payload["production_id"])
            actor_id = UUID(payload["actor_id"])
            snapshot_id = UUID(payload["snapshot_id"])
            preview_id = UUID(payload["preview_id"])
            connection_id = UUID(payload["connection_id"])
            template_id = UUID(payload["configuration_id"])
            config_revision = int(payload["config_revision"])
            activation_epoch = int(payload["activation_epoch"])
            template_version = int(payload["configuration_version"])
        except (KeyError, ValueError, TypeError):
            return JobResult("failed", error_code="invalid_sync_job")

        if not _authorized_manager(actor_id, production_id):
            return JobResult("blocked_authorization", error_code="permission_revoked")

        FolderBinding, Job, _Desired, Snapshot, _Page, Preview, Template, Connection = _models()

        def current_plan():
            with session_factory.begin() as session:
                job = session.scalar(select(Job).where(Job.id == leased_job.job_id).with_for_update())
                now = session.scalar(select(func.now()))
                if (
                    job is None or job.state != "leased" or job.lease_owner != leased_job.worker_id
                    or job.lease_generation != leased_job.generation or job.lease_expires_at is None
                    or job.lease_expires_at <= now
                ):
                    return None, None, "lease_lost"
                try:
                    config, connection, epoch = _require_current_config(session, production_id, config_revision)
                except SyncOperationError:
                    return None, None, "configuration_changed"
                if (
                    epoch != activation_epoch or connection.id != connection_id
                    or connection.allowed_root_id != payload.get("root_id")
                ):
                    return None, None, "configuration_changed"
                template = session.scalar(select(Template).where(
                    Template.id == template_id, Template.production_id == production_id,
                    Template.storage_connection_id == connection_id,
                    Template.allowed_root_id == connection.allowed_root_id,
                    Template.enabled.is_(True), Template.version == template_version,
                ))
                preview = session.scalar(select(Preview).where(
                    Preview.id == preview_id, Preview.production_id == production_id,
                    Preview.snapshot_id == snapshot_id,
                ))
                snapshot = session.scalar(select(Snapshot).where(
                    Snapshot.id == snapshot_id, Snapshot.production_id == production_id,
                    Snapshot.state == "complete", Snapshot.config_revision == config_revision,
                ))
                if template is None or preview is None or preview.state != "applied" or snapshot is None:
                    return None, None, "configuration_changed"
                if (
                    preview.config_revision != config_revision
                    or preview.activation_epoch != epoch
                    or preview.storage_connection_id != connection.id
                    or preview.root_id != connection.allowed_root_id
                    or preview.template_configuration_id != template.id
                    or preview.template_version != template_version
                ):
                    return None, None, "configuration_changed"
                objects = _snapshot_objects(session, snapshot)
                validate_snapshot_production(objects, str(production_id))
                bindings = session.scalars(select(FolderBinding).where(
                    FolderBinding.storage_connection_id == connection_id,
                    FolderBinding.allowed_root_id == connection.allowed_root_id,
                )).all()
                by_scope = {item.canonical_scope_key: item for item in bindings}
                objects_by_id = {obj["id"]: obj for obj in objects}
                roles = dict(template.role_mappings)
                selected_scopes = selected_template_scopes(dict(config.desired_config))
                tasks = [
                    obj for obj in objects
                    if obj["object_type"] == "task"
                    and _scope_included(obj, dict(config.desired_config), objects_by_id)
                    and _template_supports(obj, objects_by_id, roles, selected_scopes)
                ]
                return (session, [(obj, by_scope.get(_binding_scope(production_id, obj))) for obj in tasks], None)

        # Recompute from the immutable snapshot and current stable-ID bindings.
        # Existing bindings are safe no-ops; unresolved reservations never cause
        # a blind second provider create.
        result = current_plan()
        if result[2] == "lease_lost":
            return JobResult("retry", error_code="sync_lease_lost")
        if result[2]:
            return JobResult("blocked_authorization", error_code="sync_configuration_changed")
        session, task_bindings, _ = result
        # End the inspection transaction before provider I/O.
        session.close()

        tasks_to_provision = []
        for obj, binding in task_bindings:
            if binding is None or binding.state != "ready":
                tasks_to_provision.append(obj)

        total = len(tasks_to_provision)
        for index, obj in enumerate(tasks_to_provision):
            if not _authorized_manager(actor_id, production_id):
                return JobResult("blocked_authorization", error_code="permission_revoked")
            if not _snapshot_task_matches_kitsu(obj, production_id):
                return JobResult("reconcile_required", error_code="snapshot_task_binding_changed")
            # Fence configuration and lease immediately before each Task's
            # existing idempotent provisioning workflow.
            refreshed = current_plan()
            if refreshed[2] == "lease_lost":
                return JobResult("retry", error_code="sync_lease_lost")
            if refreshed[2]:
                return JobResult("blocked_authorization", error_code="sync_configuration_changed")
            inspect_session, current_tasks, _ = refreshed
            inspect_session.close()
            current_task = next((task for task, _binding in current_tasks if task["id"] == obj["id"]), None)
            if current_task is None:
                continue
            try:
                provision_task_folders(
                    session_factory, adapter, production_id=production_id,
                    task_id=UUID(current_task["id"]), configuration_id=template_id,
                    configuration_version=template_version,
                    before_external=lambda: _sync_fence_current(
                        session_factory, leased_job, payload, production_id, actor_id,
                    ),
                )
            except Exception as exc:
                from ..folders.service import FolderProvisioningError
                if isinstance(exc, FolderProvisioningError) and "sync authorization" in str(exc).lower():
                    return JobResult("blocked_authorization", error_code="sync_fence_changed")
                if isinstance(exc, FolderProvisioningError) and ("reconciliation" in str(exc).lower() or "ambiguous" in str(exc).lower()):
                    return JobResult("reconcile_required", error_code="folder_outcome_requires_reconciliation")
                return JobResult("retry", error_code="folder_provisioning_failed")
            with session_factory.begin() as session:
                job = session.scalar(select(Job).where(
                    Job.id == leased_job.job_id, Job.state == "leased",
                    Job.lease_owner == leased_job.worker_id,
                    Job.lease_generation == leased_job.generation,
                    Job.lease_expires_at > func.now(),
                ).with_for_update())
                if job is None:
                    return JobResult("retry", error_code="sync_lease_lost_after_provision")
                current_payload = dict(job.payload)
                current_payload["progress"] = {"completed": index + 1, "total": total}
                job.payload = current_payload
                flag_modified(job, "payload")
                job.updated_at = func.now()
        return JobResult("completed")

    return handle


def _sync_fence_current(session_factory, leased_job, payload, production_id, actor_id):
    if not _authorized_manager(actor_id, production_id):
        return False
    _FolderBinding, Job, _Desired, _Snapshot, _Page, _Preview, _Template, _Connection = _models()
    try:
        with session_factory.begin() as session:
            now = session.scalar(select(func.now()))
            job = session.scalar(select(Job).where(
                Job.id == leased_job.job_id, Job.state == "leased",
                Job.lease_owner == leased_job.worker_id,
                Job.lease_generation == leased_job.generation,
                Job.lease_expires_at > now,
            ))
            if job is None:
                return False
            _config, connection, epoch = _require_current_config(
                session, production_id, int(payload["config_revision"]),
            )
            preview = session.scalar(select(_Preview).where(
                _Preview.id == UUID(payload["preview_id"]),
                _Preview.production_id == production_id,
                _Preview.snapshot_id == UUID(payload["snapshot_id"]),
                _Preview.state == "applied",
            ))
            return bool(preview is not None and (
                epoch == int(payload["activation_epoch"])
                and str(connection.id) == payload["connection_id"]
                and connection.allowed_root_id == payload["root_id"]
                and preview.config_revision == int(payload["config_revision"])
                and preview.activation_epoch == epoch
                and str(preview.storage_connection_id) == payload["connection_id"]
                and preview.root_id == payload["root_id"]
                and str(preview.template_configuration_id) == payload["configuration_id"]
                and preview.template_version == int(payload["configuration_version"])
            ))
    except SyncOperationError:
        return False
