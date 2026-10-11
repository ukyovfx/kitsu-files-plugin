"""Official Zou plugin routes using host cookies and same-origin CSRF proof."""

import os
from uuid import UUID

from flask import abort, current_app, request
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
    ("/uploads/<session_id>", UploadSessionResource),
    ("/uploads/<session_id>/parts/<int:part_index>", UploadPartResource),
    ("/uploads/<session_id>/finalize", FinalizeUpload),
]
