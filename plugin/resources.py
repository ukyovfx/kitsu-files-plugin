"""Official Zou plugin routes using host cookies and same-origin CSRF proof."""

import os
import tempfile
from uuid import UUID

from flask import abort, current_app, request, send_file
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from werkzeug.exceptions import ServiceUnavailable

from .authz import Actor, Capability, NotFound, PermissionDenied, assert_compatible_host, authorize, issue_csrf_token, resolve_request_scope, verify_csrf_token
from .compat import HOST_ZOU_VERSION, SCHEMA_EPOCH, WORKER_BUILD, assert_runtime_host, current_actor_role


def _setting(name, default=None):
    configured = current_app.config.get(name)
    if configured not in (None, ""):
        return configured
    return os.environ.get(name, default)


def _request_actor(production_id):
    identity = get_jwt_identity()
    try:
        actor_id = UUID(str(identity))
        production_uuid = UUID(str(production_id))
    except (ValueError, TypeError, AttributeError):
        from .authz import NotFound

        raise NotFound from None
    role = current_actor_role(production_uuid)
    return Actor(actor_id=actor_id, project_role=role)


def _resolve_scope(actor, production_id, task_id=None):
    from zou.app.models.project import Project
    from zou.app.models.task import Task

    return resolve_request_scope(
        actor,
        production_id,
        task_id,
        lambda project_id: Project.query.filter_by(id=project_id).one_or_none(),
        lambda resolved_task_id: Task.query.filter_by(id=resolved_task_id).one_or_none(),
    )


def _require_mutation_origin(allowed_mimetypes=("application/json",)):
    configured_origin = _setting("VFX_FILES_KITSU_ORIGIN")
    if not configured_origin:
        raise PermissionDenied("Kitsu origin is not configured")
    origin = request.headers.get("Origin")
    if origin is None or origin.rstrip("/") != configured_origin.rstrip("/"):
        raise PermissionDenied("Cross-origin mutation rejected")
    if request.mimetype not in allowed_mimetypes:
        raise PermissionDenied("Unsupported mutation content type")
    try:
        actor_id = UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        raise PermissionDenied("Invalid authenticated identity") from None
    verify_csrf_token(current_app.config.get("SECRET_KEY", ""), actor_id, request.headers.get("X-VFX-Files-CSRF"))


def _require_enabled():
    enabled = _setting("VFX_FILES_ENABLED", "false")
    if enabled not in (True, "true", "1", 1):
        raise ServiceUnavailable("VFX Files is disabled")


def _assert_compatible():
    assert_runtime_host()
    from .models import PluginState

    state = PluginState.query.filter_by(id=1).one_or_none()
    if state is None:
        raise ServiceUnavailable("VFX Files schema is not installed")
    assert_compatible_host(HOST_ZOU_VERSION, state.schema_epoch, WORKER_BUILD)


def _task_actor_scope(task_id):
    from zou.app.models.task import Task

    task = Task.query.filter_by(id=task_id).one_or_none()
    if task is None:
        raise NotFound
    actor = _request_actor(str(task.project_id))
    scope = _resolve_scope(actor, str(task.project_id), str(task.id))
    authorize(actor, Capability.UPLOAD, scope)
    from .compat import authorize_task_mutation
    if not authorize_task_mutation(actor, task.id, "upload"):
        raise PermissionDenied("Actor is not allowed to upload to this Task")
    return actor, scope


def _upload_service():
    from .uploads.service import UploadService
    from .uploads.staging import FileSystemStaging
    from sqlalchemy.orm import sessionmaker

    staging_root = _setting("VFX_FILES_STAGING_ROOT")
    if not staging_root:
        raise ServiceUnavailable("VFX Files durable staging is not configured")
    # Route authorization has already queried through Flask-SQLAlchemy's
    # scoped request session. UploadService owns explicit transactions, so it
    # must use a separate session to avoid begin() on the active request one.
    db = current_app.extensions["sqlalchemy"]
    sessions = sessionmaker(bind=db.engine, expire_on_commit=False)
    return UploadService(sessions, FileSystemStaging(staging_root))


def _storage_adapter(connection):
    factory = current_app.extensions.get("vfx_files_storage_adapter_factory")
    if factory is not None:
        return factory(connection)
    if _setting("VFX_FILES_STORAGE_MODE") == "fake":
        from .storage.fake import PersistentFakeStorage

        fake_root = _setting("VFX_FILES_FAKE_STORAGE_ROOT")
        if not fake_root:
            raise ServiceUnavailable("FakeStorage is not configured")
        return PersistentFakeStorage(fake_root, root_id=_setting("VFX_FILES_FAKE_ROOT_ID", connection.allowed_root_id))
    raise ServiceUnavailable("No StorageAdapter is configured for downloads")


def _production_provision_scope(production_id):
    actor = _request_actor(str(production_id))
    scope = _resolve_scope(actor, str(production_id))
    authorize(actor, Capability.PROVISION, scope)
    return actor, scope


def _production_storage_connection(production_id):
    from .models import StorageConnection

    connections = StorageConnection.query.filter_by(production_id=production_id, enabled=True).limit(2).all()
    if len(connections) != 1:
        raise ServiceUnavailable("Production must have exactly one enabled StorageConnection")
    return connections[0]


def _template_tree_payload(tree):
    return {
        "folder_id": tree.provider_folder_id,
        "name": tree.name,
        "children": [_template_tree_payload(child) for child in tree.children],
    }


class Health(MethodView):
    @jwt_required()
    def get(self):
        _assert_compatible()

        enabled = _setting("VFX_FILES_ENABLED", "false")
        from zou import __version__ as zou_version

        return {"status": "ok", "enabled": enabled in (True, "true", "1", 1), "zou_version": zou_version, "schema_epoch": SCHEMA_EPOCH}


class ProductionOverview(MethodView):
    @jwt_required()
    def get(self):
        _require_enabled()
        _assert_compatible()
        production_id = request.args.get("production_id", "")
        task_id = request.args.get("task_id")
        try:
            actor = _request_actor(production_id)
            scope = _resolve_scope(actor, production_id, task_id)
            authorize(actor, Capability.READ, scope)
        except NotFound:
            abort(404)
        except PermissionDenied:
            abort(403)
        from sqlalchemy import select
        from .models import Handoff, OutputFamilyMapping, Publish, PublishPointer, StorageConnection, UploadSession
        from zou.app.models.task import Task

        if scope.task_id is not None:
            task_rows = [Task.query.filter_by(id=scope.task_id).one()]
            tasks_truncated = False
        else:
            task_rows = Task.query.filter_by(project_id=scope.production_id).order_by(Task.id).limit(1001).all()
            tasks_truncated = len(task_rows) > 1000
            task_rows = task_rows[:1000]
        task_ids = [row.id for row in task_rows]
        if not task_ids:
            return {"production_id": str(scope.production_id), "task_id": str(scope.task_id) if scope.task_id else None, "tasks": [], "tasks_truncated": False, "upload_ready": False, "files": [], "publishes": [], "handoffs": []}
        publishes = Publish.query.filter(Publish.task_id.in_(task_ids)).order_by(Publish.created_at.desc()).limit(200).all()
        pointers = {
            (row.task_id, row.output_family_id): row
            for row in PublishPointer.query.filter(PublishPointer.task_id.in_(task_ids)).all()
        }
        families = {row.id: row for row in OutputFamilyMapping.query.filter(OutputFamilyMapping.id.in_({p.output_family_id for p in publishes})).all()}
        publish_rows = []
        for publish in publishes:
            pointer = pointers.get((publish.task_id, publish.output_family_id))
            family = families.get(publish.output_family_id)
            publish_rows.append({
                "id": str(publish.id),
                "task_id": str(publish.task_id),
                "output_family_id": str(publish.output_family_id),
                "output_family_name": family.name if family else "Unknown output family",
                "version": publish.version,
                "state": publish.state,
                "qc_state": publish.qc_state,
                "manifest_sha256": publish.manifest_sha256,
                "latest_published": bool(pointer and pointer.latest_published_id == publish.id),
                "latest_approved": bool(pointer and pointer.latest_approved_id == publish.id),
            })
        handoffs = Handoff.query.filter(Handoff.task_id.in_(task_ids)).order_by(Handoff.created_at.desc()).limit(200).all()
        uploads = UploadSession.query.filter(UploadSession.task_id.in_(task_ids)).order_by(UploadSession.created_at.desc()).limit(500).all()
        storage_connections = StorageConnection.query.filter_by(production_id=scope.production_id, enabled=True).limit(2).all()
        worker_enabled = _setting("VFX_FILES_WORKER_ENABLED", "false") in (True, "true", "1", 1)
        staging_root = _setting("VFX_FILES_STAGING_ROOT")
        upload_ready = len(storage_connections) == 1 and bool(staging_root) and worker_enabled and not tasks_truncated
        return {
            "production_id": str(scope.production_id),
            "task_id": str(scope.task_id) if scope.task_id else None,
            "tasks": [{"id": str(row.id), "entity_id": str(row.entity_id), "task_type_id": str(row.task_type_id)} for row in task_rows],
            "tasks_truncated": tasks_truncated,
            "upload_ready": upload_ready,
            "files": [{
                "id": str(row.id),
                "task_id": str(row.task_id),
                "name": row.file_name,
                "state": row.state,
                "size": row.provider_size if row.state == "completed" else row.expected_size,
                "sha256": row.provider_sha256 if row.state == "completed" else row.expected_sha256,
                "provider_object_id": row.provider_object_id if row.state == "completed" else None,
                "failure_code": row.failure_code,
            } for row in uploads],
            "publishes": publish_rows,
            "handoffs": [{"id": str(row.id), "task_id": str(row.task_id), "publish_id": str(row.publish_id), "recipient_id": str(row.recipient_id), "state": row.state} for row in handoffs],
        }


class MutationProbe(MethodView):
    @jwt_required()
    def post(self):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
        except PermissionDenied:
            abort(403)
        return {"status": "accepted"}, 202


class HandoffResponse(MethodView):
    @jwt_required()
    def post(self, handoff_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            actor_id = UUID(str(get_jwt_identity()))
            handoff_uuid = UUID(str(handoff_id))
        except (PermissionDenied, ValueError, TypeError, AttributeError):
            abort(403)
        from .handoffs.service import transition_handoff
        from .models import Handoff
        handoff = Handoff.query.filter_by(id=handoff_uuid).one_or_none()
        if handoff is None:
            abort(404)
        try:
            # Resolve project ownership from the authoritative Task row; do not use the body for scope.
            from zou.app.models.task import Task
            task = Task.query.filter_by(id=handoff.task_id).one_or_none()
            if task is None:
                abort(404)
            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(handoff.task_id))
            authorize(actor, Capability.HANDOFF, scope)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, task.id, "handoff"):
                abort(403)
            payload = request.get_json(silent=False) or {}
            idempotency_key = request.headers.get("Idempotency-Key", "").strip()
            if not idempotency_key or len(idempotency_key) > 128:
                abort(400)
            result = transition_handoff(
                current_app.extensions["sqlalchemy"].session,
                actor_id=actor_id,
                handoff_id=handoff_uuid,
                target=payload.get("target"),
                feedback=payload.get("feedback"),
                idempotency_key=idempotency_key,
            )
        except NotFound:
            abort(404)
        except PermissionDenied:
            abort(403)
        except (ValueError, TypeError):
            abort(400)
        return {"id": str(result.id), "state": result.state, "publish_id": str(result.publish_id)}


class TaskUploads(MethodView):
    @jwt_required()
    def get(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            parsed_task_id = UUID(str(task_id))
            actor, _scope = _task_actor_scope(parsed_task_id)
        except (PermissionDenied, NotFound, ValueError, TypeError):
            abort(403)
        from .models import UploadSession
        rows = UploadSession.query.filter(
            UploadSession.task_id == parsed_task_id,
            UploadSession.actor_id == actor.actor_id,
            UploadSession.state.in_(("receiving", "verifying", "staged", "transferring", "completed", "failed", "reconcile_required")),
        ).order_by(UploadSession.created_at.desc()).limit(20).all()
        return [{"id": str(row.id), "state": row.state, "file_name": row.file_name, "expected_size": row.expected_size, "expected_sha256": row.expected_sha256, "received_size": row.received_size, "provider_object_id": row.provider_object_id, "expires_at": row.expires_at.isoformat()} for row in rows]

    @jwt_required()
    def post(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            parsed_task_id = UUID(str(task_id))
            actor, scope = _task_actor_scope(parsed_task_id)
            payload = request.get_json(silent=False) or {}
            size = int(payload.get("size", 0))
            sha256_hex = str(payload.get("sha256", ""))
            file_name = str(payload.get("file_name", "unnamed"))
        except (PermissionDenied, NotFound, ValueError, TypeError):
            abort(403)
        from .models import StorageConnection
        connections = StorageConnection.query.filter_by(production_id=scope.production_id, enabled=True).limit(2).all()
        if len(connections) != 1:
            raise ServiceUnavailable("Production must have exactly one enabled StorageConnection")
        from .uploads.service import UploadDenied
        try:
            created = _upload_service().create_upload(
                task_id=parsed_task_id,
                actor_id=actor.actor_id,
                storage_connection_id=connections[0].id,
                size=size,
                sha256_hex=sha256_hex,
                file_name=file_name,
            )
        except UploadDenied as error:
            abort(403, description=str(error))
        except ValueError as error:
            abort(400, description=str(error))
        return {"id": str(created.session_id), "expires_at": created.expires_at.isoformat(), "max_chunk_bytes": created.max_chunk_bytes}, 201


def _working_revision_payload(revision):
    from .models import UploadSession, WorkingRevisionFile

    files = WorkingRevisionFile.query.filter_by(revision_id=revision.id).order_by(WorkingRevisionFile.ordinal).all()
    upload_sessions = {
        row.working_revision_file_id: row
        for row in UploadSession.query.filter(UploadSession.working_revision_file_id.in_([item.id for item in files])).all()
    } if files else {}
    return {
        "id": str(revision.id),
        "production_id": str(revision.production_id),
        "task_id": str(revision.task_id),
        "version": revision.version,
        "version_label": f"w{revision.version:03d}",
        "state": revision.state,
        "base_revision_id": str(revision.base_revision_id) if revision.base_revision_id else None,
        "created_by": str(revision.created_by),
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
        "completed_at": revision.completed_at.isoformat() if revision.completed_at else None,
        "manifest_sha256": revision.manifest_sha256,
        "files": [{
            "id": str(row.id),
            "name": row.file_name,
            "category": row.category,
            "size": row.provider_size if row.state == "completed" else row.expected_size,
            "sha256": row.provider_sha256 if row.state == "completed" else row.expected_sha256,
            "state": row.state,
            "provider_object_id": row.provider_object_id if row.state == "completed" else None,
            "upload_session_id": str(upload_sessions[row.id].id) if row.id in upload_sessions else None,
        } for row in files],
    }


class TaskWorkingRevisions(MethodView):
    @jwt_required()
    def get(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            parsed_task_id = UUID(str(task_id))
            from .models import LatestWorkingPointer, WorkingRevision
            from zou.app.models.task import Task
            task = Task.query.filter_by(id=parsed_task_id).one_or_none()
            if task is None:
                abort(404)
            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(parsed_task_id))
            authorize(actor, Capability.READ, scope)
            revisions = WorkingRevision.query.filter_by(task_id=parsed_task_id).order_by(WorkingRevision.version.desc()).limit(100).all()
            pointer = LatestWorkingPointer.query.filter_by(task_id=parsed_task_id).one_or_none()
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError):
            abort(404)
        return {
            "task_id": str(parsed_task_id),
            "latest_working_id": str(pointer.revision_id) if pointer else None,
            "revisions": [_working_revision_payload(row) for row in revisions],
        }

    @jwt_required()
    def post(self, task_id):
        _require_enabled()
        _assert_compatible()
        from .working_files.service import WorkingConflict, WorkingDenied, reserve_working_revision
        try:
            _require_mutation_origin()
            parsed_task_id = UUID(str(task_id))
            actor, scope = _task_actor_scope(parsed_task_id)
            payload = request.get_json(silent=False) or {}
            from .models import StorageConnection
            connections = StorageConnection.query.filter_by(production_id=scope.production_id, enabled=True).limit(2).all()
            if len(connections) != 1:
                raise ServiceUnavailable("Production must have exactly one enabled StorageConnection")
            from sqlalchemy.orm import sessionmaker
            db = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db.engine, expire_on_commit=False)
            from .models import WorkingRevision
            key = request.headers.get("Idempotency-Key", "").strip()
            receipt = reserve_working_revision(
                sessions,
                production_id=scope.production_id,
                task_id=parsed_task_id,
                storage_connection_id=connections[0].id,
                actor_id=actor.actor_id,
                base_revision_id=UUID(str(payload["base_revision_id"])) if payload.get("base_revision_id") else None,
                idempotency_key=key,
                files=payload.get("files"),
                dependencies=payload.get("dependencies", []),
                include_intermediate=payload.get("include_intermediate", False) is True,
                max_chunk_bytes=int(_setting("VFX_FILES_MAX_CHUNK_BYTES", 8 * 1024 * 1024)),
                ttl_hours=int(_setting("VFX_FILES_UPLOAD_TTL_HOURS", 168)),
            )
            revision = WorkingRevision.query.filter_by(id=receipt.id).one()
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError, KeyError) as error:
            abort(400, description=str(error))
        except WorkingConflict as error:
            abort(409, description=str(error))
        except WorkingDenied as error:
            abort(403, description=str(error))
        result = _working_revision_payload(revision)
        result["files"] = [
            {**file_payload, "upload_session_id": str(file_receipt.upload_session_id)}
            for file_payload, file_receipt in zip(result["files"], receipt.files, strict=True)
        ]
        return result, 201


class WorkingFileDownload(MethodView):
    @jwt_required()
    def get(self, revision_file_id):
        _require_enabled()
        _assert_compatible()
        try:
            file_uuid = UUID(str(revision_file_id))
            from .models import StorageConnection, WorkingRevision, WorkingRevisionFile
            file_row = WorkingRevisionFile.query.filter_by(id=file_uuid).one_or_none()
            revision = WorkingRevision.query.filter_by(id=file_row.revision_id).one_or_none() if file_row else None
            if file_row is None or revision is None or revision.state != "completed" or file_row.state != "completed":
                abort(404)
            actor = _request_actor(str(revision.production_id))
            scope = _resolve_scope(actor, str(revision.production_id), str(revision.task_id))
            authorize(actor, Capability.READ, scope)
            connection = StorageConnection.query.filter_by(production_id=revision.production_id, enabled=True).one_or_none()
            if connection is None or not file_row.provider_object_id:
                abort(404)
            adapter = _storage_adapter(connection)
            provider_object = adapter.get(file_row.provider_object_id)
            if provider_object is None or provider_object.trashed or provider_object.parent_id is None:
                abort(404)
            adapter.assert_parent_within_root(provider_object.parent_id)
            if not adapter.verify(file_row.provider_object_id, file_row.provider_size, file_row.provider_sha256):
                abort(409, description="Stored Working File failed integrity verification")
            stream = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
            try:
                count, digest = adapter.download_to(file_row.provider_object_id, stream)
                if count != file_row.provider_size or digest != file_row.provider_sha256:
                    abort(409, description="Downloaded Working File failed integrity verification")
                stream.seek(0)
                response = send_file(
                    stream,
                    mimetype=file_row.mime_type or "application/octet-stream",
                    as_attachment=True,
                    download_name=file_row.file_name,
                    conditional=False,
                )
                response.call_on_close(stream.close)
                return response
            except Exception:
                stream.close()
                raise
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError):
            abort(404)


class SharedTemplateCandidates(MethodView):
    @jwt_required()
    def get(self, production_id):
        _require_enabled()
        _assert_compatible()
        try:
            actor, scope = _production_provision_scope(production_id)
            connection = _production_storage_connection(scope.production_id)
            current_app.extensions["sqlalchemy"].session.remove()
            from .folders.templates import discover_candidates
            candidate = discover_candidates(
                _storage_adapter(connection),
                root_url=request.args.get("root_url", ""),
                authorized_root_id=connection.allowed_root_id,
            )
        except (NotFound, ValueError):
            abort(400)
        except (PermissionDenied,):
            abort(403)
        except ServiceUnavailable:
            raise
        except PermissionError:
            abort(403)
        except Exception as error:
            from .folders.templates import TemplateDenied
            from .storage.base import AmbiguousOutcome, PermissionFailure
            if isinstance(error, TemplateDenied) or isinstance(error, PermissionFailure):
                abort(403, description=str(error))
            if isinstance(error, AmbiguousOutcome):
                abort(409, description="Drive listing is incomplete or ambiguous; discovery may be retried.")
            raise
        return {
            "production_id": str(scope.production_id),
            "storage_connection_id": str(connection.id),
            "allowed_root_id": candidate.root_id,
            "drive_id": candidate.drive_id,
            "templates": [_template_tree_payload(tree) for tree in candidate.templates],
        }


class SharedTemplateConfigurationResource(MethodView):
    @jwt_required()
    def get(self, production_id):
        _require_enabled()
        _assert_compatible()
        try:
            _actor, scope = _production_provision_scope(production_id)
            from .models import SharedTemplateConfiguration
            row = SharedTemplateConfiguration.query.filter_by(production_id=scope.production_id).one_or_none()
        except (NotFound, ValueError, TypeError):
            abort(404)
        except PermissionDenied:
            abort(403)
        if row is None:
            return {"production_id": str(scope.production_id), "configured": False, "enabled": False}
        return {
            "production_id": str(row.production_id),
            "configured": True,
            "id": str(row.id),
            "storage_connection_id": str(row.storage_connection_id),
            "allowed_root_id": row.allowed_root_id,
            "template_folder_id": row.template_folder_id,
            "template_tree": row.template_tree,
            "role_mappings": row.role_mappings,
            "version": row.version,
            "enabled": row.enabled,
        }

    @jwt_required()
    def post(self, production_id):
        _require_enabled()
        _assert_compatible()
        from .folders.templates import (
            TemplateConflict,
            TemplateDenied,
            discover_candidates,
            normalize_role_mappings,
            save_configuration,
        )
        try:
            _require_mutation_origin()
            actor, scope = _production_provision_scope(production_id)
            payload = request.get_json(silent=False) or {}
            connection = _production_storage_connection(scope.production_id)
            current_app.extensions["sqlalchemy"].session.remove()
            adapter = _storage_adapter(connection)
            candidate = discover_candidates(
                adapter,
                root_url=payload.get("root_url", ""),
                authorized_root_id=connection.allowed_root_id,
            )
            template_folder_id = payload.get("template_folder_id")
            selected = next((tree for tree in candidate.templates if tree.provider_folder_id == template_folder_id), None)
            if selected is None:
                abort(400, description="Selected template is not a discovered folder under the authorized root")
            mappings = normalize_role_mappings(selected, payload.get("role_mappings"))
            from sqlalchemy.orm import sessionmaker
            db = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db.engine, expire_on_commit=False)
            expected_version = payload.get("expected_version")
            if expected_version is not None and (not isinstance(expected_version, int) or isinstance(expected_version, bool)):
                abort(400)
            row = save_configuration(
                sessions,
                production_id=scope.production_id,
                storage_connection_id=connection.id,
                allowed_root_id=connection.allowed_root_id,
                tree=selected,
                role_mappings=mappings,
                actor_id=actor.actor_id,
                expected_version=expected_version,
            )
        except (PermissionDenied, NotFound, TemplateDenied):
            abort(403)
        except TemplateConflict as error:
            abort(409, description=str(error))
        except (ValueError, TypeError) as error:
            abort(400, description=str(error))
        except PermissionError:
            abort(403)
        return {"id": str(row.id), "version": row.version, "enabled": row.enabled, "template_folder_id": row.template_folder_id}, 201


class SharedTemplateActivation(MethodView):
    @jwt_required()
    def post(self, production_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            actor, scope = _production_provision_scope(production_id)
            payload = request.get_json(silent=False) or {}
            expected_version = payload.get("expected_version")
            enabled = payload.get("enabled")
            if not isinstance(expected_version, int) or isinstance(expected_version, bool) or not isinstance(enabled, bool):
                abort(400)
            if enabled:
                connection = _production_storage_connection(scope.production_id)
                current_app.extensions["sqlalchemy"].session.remove()
                capabilities = _storage_adapter(connection).capabilities(connection.allowed_root_id)
                if (
                    not capabilities.root_visible
                    or not capabilities.can_add_children
                    or capabilities.root_id != connection.allowed_root_id
                ):
                    abort(403, description="Configured storage root is not writable")
            from sqlalchemy.orm import sessionmaker
            db = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db.engine, expire_on_commit=False)
            from .folders.templates import TemplateConflict, TemplateDenied, set_configuration_enabled
            from .models import SharedTemplateConfiguration
            existing = SharedTemplateConfiguration.query.filter_by(production_id=scope.production_id).one_or_none()
            row = set_configuration_enabled(
                sessions,
                production_id=scope.production_id,
                actor_id=actor.actor_id,
                expected_version=expected_version,
                enabled=enabled,
                storage_connection_id=connection.id if enabled else (existing.storage_connection_id if existing else None),
                authorized_root_id=connection.allowed_root_id if enabled else (existing.allowed_root_id if existing else None),
            )
        except (PermissionDenied, NotFound, TemplateDenied):
            abort(403)
        except TemplateConflict as error:
            abort(409, description=str(error))
        except (ValueError, TypeError):
            abort(400)
        except PermissionError:
            abort(403)
        return {"id": str(row.id), "version": row.version, "enabled": row.enabled}, 200


class TaskSharedFolders(MethodView):
    @jwt_required()
    def get(self, production_id, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            actor = _request_actor(production_id)
            scope = _resolve_scope(actor, str(production_id), str(task_id))
            authorize(actor, Capability.READ, scope)
        except (PermissionDenied, NotFound):
            abort(403)
        from sqlalchemy import select
        from .models import FolderBinding, SharedTemplateConfiguration
        from zou.app.models.entity import Entity
        from zou.app.models.task import Task
        from zou.app.models.task_type import TaskType

        config = SharedTemplateConfiguration.query.filter_by(production_id=scope.production_id).one_or_none()
        if config is None:
            return {"configured": False, "folders": []}
        task = Task.query.filter_by(id=scope.task_id, project_id=scope.production_id).one_or_none()
        entity = Entity.query.filter_by(id=task.entity_id).one_or_none() if task is not None else None
        task_type = TaskType.query.filter_by(id=task.task_type_id).one_or_none() if task is not None else None
        if task is None or entity is None or task_type is None or task_type.for_entity != "Shot":
            abort(404)
        sequence_id = sequence_name = None
        if "sequence" in config.role_mappings:
            sequence = Entity.query.filter_by(id=entity.parent_id).one_or_none() if entity.parent_id else None
            if sequence is None or sequence.project_id != scope.production_id:
                abort(409, description="Configured Sequence role has no Kitsu Sequence ancestor")
            sequence_id, sequence_name = str(sequence.id), sequence.name
        try:
            from .folders.provisioning import build_folder_plan
            plan = build_folder_plan(
                tree=config.template_tree,
                role_mappings=config.role_mappings,
                production_id=str(scope.production_id),
                shot_id=str(entity.id),
                shot_name=entity.name,
                sequence_id=sequence_id,
                sequence_name=sequence_name,
                task_id=str(task.id),
                task_name=task.name,
            )
        except (ValueError, TypeError):
            abort(409, description="Current template mappings cannot resolve this Task")
        connection = _production_storage_connection(scope.production_id)
        by_scope = {
            row.canonical_scope_key: {
                "provider_folder_id": row.provider_folder_id,
                "state": row.state,
                "parent_provider_id": row.parent_provider_id,
            }
            for row in FolderBinding.query.filter(
                FolderBinding.storage_connection_id == connection.id,
                FolderBinding.allowed_root_id == config.allowed_root_id,
                FolderBinding.canonical_scope_key.in_([item.scope_key for item in plan]),
            ).all()
        }
        current_app.extensions["sqlalchemy"].session.remove()
        adapter = _storage_adapter(connection)
        folders = []
        for item in plan:
            binding = by_scope.get(item.scope_key)
            ready = False
            provider_folder_id = binding["provider_folder_id"] if binding else None
            if binding is not None and binding["state"] == "ready" and provider_folder_id:
                try:
                    provider_object = adapter.get(provider_folder_id)
                    if (
                        provider_object is not None and provider_object.mime_type == "folder"
                        and not provider_object.trashed
                        and provider_object.parent_id == binding["parent_provider_id"]
                    ):
                        adapter.assert_parent_within_root(provider_object.parent_id)
                        ready = True
                except Exception:
                    ready = False
            folders.append({
                "scope_key": item.scope_key,
                "name": item.display_name,
                "state": "ready" if ready else (binding["state"] if binding is not None else "pending"),
                "provider_folder_id": provider_folder_id if ready else None,
                "url": f"https://drive.google.com/drive/folders/{provider_folder_id}" if ready else None,
            })
        return {"configured": True, "enabled": bool(config.enabled), "folders": folders}

    @jwt_required()
    def post(self, production_id, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            actor, scope = _production_provision_scope(production_id)
            task_scope = _resolve_scope(actor, str(scope.production_id), str(task_id))
            authorize(actor, Capability.PROVISION, task_scope)
            from sqlalchemy import select
            from sqlalchemy.orm import sessionmaker
            from .models import Job, SharedTemplateConfiguration, StorageConnection
            from zou.app.models.task import Task
            from zou.app.models.task_type import TaskType

            db = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db.engine, expire_on_commit=False)
            config_snapshot = SharedTemplateConfiguration.query.filter_by(
                production_id=scope.production_id
            ).one_or_none()
            if config_snapshot is None or not config_snapshot.enabled:
                abort(409, description="Shared folder provisioning is not configured and active")
            connection_snapshot = StorageConnection.query.filter_by(
                id=config_snapshot.storage_connection_id,
                production_id=scope.production_id,
                enabled=True,
            ).one_or_none()
            if (
                connection_snapshot is None
                or connection_snapshot.allowed_root_id != config_snapshot.allowed_root_id
            ):
                abort(409, description="Production storage configuration is unavailable")
            # Check the provider outside the database transaction. Configuration is
            # re-read and locked below before the durable job is created.
            config_id = config_snapshot.id
            config_version = config_snapshot.version
            storage_connection_id = connection_snapshot.id
            allowed_root_id = connection_snapshot.allowed_root_id
            adapter = _storage_adapter(connection_snapshot)
            current_app.extensions["sqlalchemy"].session.remove()
            capabilities = adapter.capabilities(allowed_root_id)
            if (
                not capabilities.root_visible or not capabilities.can_add_children
                or capabilities.root_id != allowed_root_id
            ):
                abort(403, description="Production storage root is not writable")
            with sessions.begin() as session:
                config = session.scalar(
                    select(SharedTemplateConfiguration)
                    .where(SharedTemplateConfiguration.production_id == scope.production_id)
                    .with_for_update()
                )
                if config is None or not config.enabled:
                    abort(409, description="Shared folder provisioning is not configured and active")
                if config.id != config_id or config.version != config_version:
                    abort(409, description="Shared folder configuration changed; retry provisioning")
                connection = session.scalar(
                    select(StorageConnection)
                    .where(StorageConnection.id == config.storage_connection_id)
                    .with_for_update()
                )
                if (
                    connection is None or not connection.enabled
                    or connection.production_id != scope.production_id
                    or connection.allowed_root_id != config.allowed_root_id
                    or connection.id != storage_connection_id
                    or connection.allowed_root_id != allowed_root_id
                ):
                    abort(409, description="Production storage configuration is unavailable")
                task = session.get(Task, task_scope.task_id)
                task_type = session.get(TaskType, task.task_type_id) if task is not None else None
                if task is None or task_type is None or task_type.for_entity != "Shot":
                    abort(404)
                idempotency_key = f"task-folders:{config.id}:{config.version}:{task_scope.task_id}"
                job = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key).with_for_update())
                if job is None:
                    job = Job(
                        job_type="provision_task_folders",
                        idempotency_key=idempotency_key,
                        payload={
                            "job_type": "provision_task_folders",
                            "actor_id": str(actor.actor_id),
                            "production_id": str(scope.production_id),
                            "task_id": str(task_scope.task_id),
                            "configuration_id": str(config.id),
                            "configuration_version": config.version,
                        },
                        state="queued",
                        attempts=0,
                        max_attempts=5,
                    )
                    session.add(job)
                    session.flush()
                return {"job_id": str(job.id), "state": job.state}, 202
        except (PermissionDenied, NotFound):
            abort(403)


class UploadSessionResource(MethodView):
    @jwt_required()
    def get(self, session_id):
        _require_enabled()
        _assert_compatible()
        from .models import UploadPart, UploadSession
        try:
            actor_id = UUID(str(get_jwt_identity()))
            session_uuid = UUID(str(session_id))
        except (ValueError, TypeError, AttributeError):
            abort(404)
        upload = UploadSession.query.filter_by(id=session_uuid, actor_id=actor_id).one_or_none()
        if upload is None:
            abort(404)
        try:
            _task_actor_scope(upload.task_id)
        except (PermissionDenied, NotFound):
            abort(404)
        parts = UploadPart.query.filter_by(session_id=session_uuid).order_by(UploadPart.part_index).all()
        return {"id": str(upload.id), "state": upload.state, "file_name": upload.file_name, "expected_size": upload.expected_size, "received_size": upload.received_size, "provider_object_id": upload.provider_object_id, "failure_code": upload.failure_code, "expires_at": upload.expires_at.isoformat(), "max_chunk_bytes": int(_setting("VFX_FILES_MAX_CHUNK_BYTES", 8 * 1024 * 1024)), "parts": [{"index": p.part_index, "byte_start": p.byte_start, "byte_count": p.byte_count, "sha256": p.sha256, "state": p.state} for p in parts]}



class UploadPartResource(MethodView):
    @jwt_required()
    def put(self, session_id, part_index):
        _require_enabled()
        _assert_compatible()
        from .models import UploadSession
        from .uploads.service import UploadConflict, UploadDenied
        try:
            _require_mutation_origin(("application/octet-stream",))
            actor_id = UUID(str(get_jwt_identity()))
            session_uuid = UUID(str(session_id))
            index = int(part_index)
            upload = UploadSession.query.filter_by(id=session_uuid, actor_id=actor_id).one_or_none()
            if upload is None:
                abort(404)
            actor, _scope = _task_actor_scope(upload.task_id)
            raw_range = request.headers.get("Content-Range", "")
            import re
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", raw_range)
            if not match:
                abort(400)
            start, end, total = map(int, match.groups())
            content_length = request.content_length
            max_chunk = int(current_app.config.get("VFX_FILES_MAX_CHUNK_BYTES", 8 * 1024 * 1024))
            if end < start or end - start + 1 > max_chunk or total != upload.expected_size or content_length != end - start + 1:
                abort(413)
            body = request.get_data(cache=False)
            if len(body) != content_length:
                abort(400)
            part = _upload_service().put_part(
                session_id=session_uuid,
                actor_id=actor.actor_id,
                part_index=index,
                byte_start=start,
                body=body,
                part_sha256=request.headers.get("X-Chunk-SHA256", ""),
            )
        except (PermissionDenied, NotFound):
            abort(403)
        except UploadDenied:
            abort(403)
        except UploadConflict as error:
            abort(409, description=str(error))
        except ValueError:
            abort(400)
        return {"index": part.part_index, "byte_start": part.byte_start, "byte_count": part.byte_count, "sha256": part.sha256, "state": part.state}, 200


class FinalizeUpload(MethodView):
    @jwt_required()
    def post(self, session_id):
        _require_enabled()
        _assert_compatible()
        from .uploads.service import UploadConflict, UploadDenied
        try:
            _require_mutation_origin()
            actor_id = UUID(str(get_jwt_identity()))
            session_uuid = UUID(str(session_id))
            from .models import UploadSession
            upload = UploadSession.query.filter_by(id=session_uuid, actor_id=actor_id).one_or_none()
            if upload is None:
                abort(404)
            actor, _scope = _task_actor_scope(upload.task_id)
            result = _upload_service().finalize_upload(session_id=session_uuid, actor_id=actor.actor_id)
        except (PermissionDenied, NotFound):
            abort(403)
        except UploadDenied:
            abort(403)
        except UploadConflict as error:
            abort(409, description=str(error))
        except ValueError as error:
            abort(400, description=str(error))
        return {"job_id": str(result.id), "state": result.state}, 202


class CsrfToken(MethodView):
    @jwt_required()
    def get(self):
        identity = get_jwt_identity()
        try:
            actor_id = UUID(str(identity))
        except (ValueError, TypeError, AttributeError):
            abort(401)
        secret_key = current_app.config.get("SECRET_KEY", "")
        if not secret_key:
            raise ServiceUnavailable("Kitsu secret key is unavailable")
        return {"csrf_token": issue_csrf_token(secret_key, actor_id), "expires_in": 900}


routes = [
    ("/health", Health),
    ("/csrf-token", CsrfToken),
    ("/overview", ProductionOverview),
    ("/mutation-probe", MutationProbe),
    ("/handoffs/<handoff_id>/respond", HandoffResponse),
    ("/tasks/<task_id>/uploads", TaskUploads),
    ("/tasks/<task_id>/working-revisions", TaskWorkingRevisions),
    ("/working-files/<revision_file_id>/download", WorkingFileDownload),
    ("/productions/<production_id>/shared-template/candidates", SharedTemplateCandidates),
    ("/productions/<production_id>/shared-template", SharedTemplateConfigurationResource),
    ("/productions/<production_id>/shared-template/activate", SharedTemplateActivation),
    ("/productions/<production_id>/tasks/<task_id>/shared-folders", TaskSharedFolders),
    ("/uploads/<session_id>", UploadSessionResource),
    ("/uploads/<session_id>/parts/<int:part_index>", UploadPartResource),
    ("/uploads/<session_id>/finalize", FinalizeUpload),
]
