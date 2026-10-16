"""Official Zou plugin routes using host cookies and same-origin CSRF proof."""

import json
import os
import tempfile
from datetime import datetime, timezone
from uuid import UUID

from flask import abort, current_app, request, send_file
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from werkzeug.exceptions import ServiceUnavailable

from .authz import Actor, Capability, NotFound, PermissionDenied, assert_compatible_host, authorize, issue_csrf_token, resolve_request_scope, verify_csrf_token
from .compat import HOST_ZOU_VERSION, SCHEMA_EPOCH, WORKER_BUILD, assert_runtime_host, current_actor_role
from .packages.delivery_service import PackageDeliveryConflict, PackageDeliveryDenied
from .packages.publication import PackagePublicationConflict


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


def _output_package_read_task_id(actor, package):
    """Resolve an authorized source or recipient Task as the request context."""
    from .compat import authorize_task_read
    from .models import OutputPackageDelivery

    if authorize_task_read(actor, package.task_id):
        return package.task_id
    deliveries = OutputPackageDelivery.query.filter_by(
        production_id=package.production_id, package_id=package.id,
    ).all()
    return next((row.recipient_task_id for row in deliveries if authorize_task_read(actor, row.recipient_task_id)), None)


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


def _storage_adapter(connection, *, allow_unconfigured=False):
    factory = current_app.extensions.get("vfx_files_storage_adapter_factory")
    if factory is not None:
        return factory(connection)
    storage_mode = _setting("VFX_FILES_STORAGE_MODE")
    if storage_mode == "fake":
        from .storage.fake import PersistentFakeStorage

        fake_root = _setting("VFX_FILES_FAKE_STORAGE_ROOT")
        if not fake_root:
            raise ServiceUnavailable("FakeStorage is not configured")
        return PersistentFakeStorage(
            fake_root,
            root_id=_setting("VFX_FILES_FAKE_ROOT_ID", connection.allowed_root_id),
            drive_id=_setting("VFX_FILES_FAKE_DRIVE_ID", connection.drive_id),
        )
    if storage_mode == "google_drive":
        if not connection.enabled and not allow_unconfigured:
            raise ServiceUnavailable("Google Drive storage connection is unavailable")
        from sqlalchemy.orm import sessionmaker
        from .models import DriveCredential

        credential = DriveCredential.query.filter_by(connection_id=connection.id).one_or_none()
        if credential is not None:
            if credential.state != "active":
                raise ServiceUnavailable("Google Drive credential is unavailable")
            from .integrations.oauth import build_database_drive_adapter
            database = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=database.engine, expire_on_commit=False)
            return build_database_drive_adapter(connection, sessions, current_app.config, current_app.extensions)
        if _setting("VFX_FILES_ALLOW_LEGACY_CREDENTIAL_FILES", "false").lower() not in ("1", "true", "yes"):
            raise ServiceUnavailable("Encrypted Google Drive credentials are required")
        from .storage.worker_factory import build_google_drive_adapter

        credentials_dir = _setting("VFX_FILES_DRIVE_CREDENTIALS_DIR")
        if not credentials_dir:
            raise ServiceUnavailable("Google Drive credentials are not configured")
        return build_google_drive_adapter(connection, credentials_dir)
    raise ServiceUnavailable("No StorageAdapter is configured for downloads")


def _production_provision_scope(production_id):
    actor = _request_actor(str(production_id))
    scope = _resolve_scope(actor, str(production_id))
    authorize(actor, Capability.PROVISION, scope)
    return actor, scope


def _production_storage_connection(production_id):
    from .integrations.storage_authority import selected_storage_connection

    connection = selected_storage_connection(
        current_app.extensions["sqlalchemy"].session, production_id,
    )
    if connection is None:
        raise ServiceUnavailable("Select and verify the Production Drive connection through KitsuSync first")
    return connection


def _reject_plugin_owned_storage_configuration(production_id):
    """Prevent retired Files-page settings routes from competing with KitsuSync."""
    from .models import KitsuSyncDesiredConfiguration

    if KitsuSyncDesiredConfiguration.query.filter_by(production_id=production_id).one_or_none() is not None:
        abort(409, description="Drive connection, root, template selection and activation are managed by KitsuSync.")


def _template_tree_payload(tree):
    return {
        "folder_id": tree.provider_folder_id,
        "name": tree.name,
        "children": [_template_tree_payload(child) for child in tree.children],
    }


class OutputFamilies(MethodView):
    @jwt_required()
    def get(self, production_id):
        _require_enabled()
        _assert_compatible()
        try:
            actor, scope = _production_provision_scope(UUID(str(production_id)))
        except (PermissionDenied, NotFound, ValueError, TypeError):
            abort(403)
        from .models import OutputFamilyMapping
        rows = OutputFamilyMapping.query.filter_by(production_id=scope.production_id).order_by(OutputFamilyMapping.family_key).all()
        return [{
            "id": str(row.id), "family_key": row.family_key,
            "output_type_id": str(row.output_type_id), "task_type_id": str(row.task_type_id),
            "name": row.name, "representation": row.representation, "extension": row.extension,
            "file_status_id": str(row.file_status_id), "publish_folder_id": row.publish_folder_id,
            "enabled": row.enabled,
        } for row in rows]

    @jwt_required()
    def post(self, production_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            actor, scope = _production_provision_scope(UUID(str(production_id)))
            payload = request.get_json(silent=False) or {}
            from .models import FolderBinding, OutputFamilyMapping
            from zou.app.models.file_status import FileStatus
            from zou.app.models.output_type import OutputType
            from zou.app.models.task_type import TaskType
            db_ext = current_app.extensions["sqlalchemy"]
            key = str(payload.get("family_key", "")).strip()
            output_type_id = UUID(str(payload.get("output_type_id", "")))
            task_type_id = UUID(str(payload.get("task_type_id", "")))
            file_status_id = UUID(str(payload.get("file_status_id", "")))
            publish_folder_id = str(payload.get("publish_folder_id", "")).strip()
            name = str(payload.get("name", "main")).strip()
            representation = str(payload.get("representation", "")).strip()
            extension = str(payload.get("extension", "")).strip().lstrip(".")
            if (
                not key or len(key) > 128 or not name or len(name) > 250
                or len(representation) > 20 or len(extension) > 10 or not publish_folder_id
            ):
                abort(400)
            if (
                db_ext.session.get(OutputType, output_type_id) is None
                or db_ext.session.get(TaskType, task_type_id) is None
                or db_ext.session.get(FileStatus, file_status_id) is None
            ):
                abort(400, description="Output type, Task type and file status must be valid Kitsu records")
            connection = _production_storage_connection(scope.production_id)
            binding = FolderBinding.query.filter_by(
                storage_connection_id=connection.id,
                allowed_root_id=connection.allowed_root_id,
                provider_folder_id=publish_folder_id,
                state="ready",
            ).one_or_none()
            if binding is None:
                abort(400, description="Publish destination must be a ready Production folder binding")
            adapter = _storage_adapter(connection)
            capabilities = adapter.capabilities(connection.allowed_root_id)
            if not capabilities.root_visible:
                abort(409, description="Allowed storage root is unavailable")
            folder = adapter.get(publish_folder_id)
            if folder is None or folder.trashed or folder.mime_type != "folder":
                abort(400, description="Publish destination folder is unavailable")
            adapter.assert_parent_within_root(publish_folder_id)
            existing = OutputFamilyMapping.query.filter_by(
                production_id=scope.production_id, family_key=key
            ).one_or_none()
            if existing is not None:
                abort(409, description="Output family key already exists")
            row = OutputFamilyMapping(
                production_id=scope.production_id, family_key=key,
                output_type_id=output_type_id, task_type_id=task_type_id,
                name=name, representation=representation, extension=extension,
                file_status_id=file_status_id, publish_folder_id=publish_folder_id,
                enabled=False,
            )
            db_ext.session.add(row)
            db_ext.session.flush()
            db_ext.session.commit()
        except (PermissionDenied, NotFound, ValueError, TypeError):
            abort(403)
        return {"id": str(row.id), "family_key": row.family_key, "publish_folder_id": row.publish_folder_id, "enabled": False}, 201


class OutputFamilyActivation(MethodView):
    @jwt_required()
    def post(self, production_id, family_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            _actor, scope = _production_provision_scope(UUID(str(production_id)))
            family_uuid = UUID(str(family_id))
        except (PermissionDenied, NotFound, ValueError, TypeError):
            abort(403)
        from .models import FolderBinding, OutputFamilyMapping
        family = OutputFamilyMapping.query.filter_by(id=family_uuid, production_id=scope.production_id).one_or_none()
        if family is None or not family.publish_folder_id:
            abort(404)
        try:
            connection = _production_storage_connection(scope.production_id)
        except ServiceUnavailable:
            abort(409)
        binding = FolderBinding.query.filter_by(
            storage_connection_id=connection.id, allowed_root_id=connection.allowed_root_id,
            provider_folder_id=family.publish_folder_id, state="ready",
        ).one_or_none()
        if binding is None:
            abort(409)
        adapter = _storage_adapter(connection)
        capabilities = adapter.capabilities(connection.allowed_root_id)
        if not capabilities.root_visible:
            abort(409, description="Allowed storage root is unavailable")
        folder = adapter.get(family.publish_folder_id)
        if folder is None or folder.trashed or folder.mime_type != "folder":
            abort(409)
        adapter.assert_parent_within_root(family.publish_folder_id)
        family.enabled = True
        current_app.extensions["sqlalchemy"].session.flush()
        current_app.extensions["sqlalchemy"].session.commit()
        return {"id": str(family.id), "enabled": True}, 200


class TaskOutputPackages(MethodView):
    @jwt_required()
    def get(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            parsed_task_id = UUID(str(task_id))
            from zou.app.models.task import Task
            task = Task.query.filter_by(id=parsed_task_id).one_or_none()
            if task is None:
                abort(404)
            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(task.id))
            authorize(actor, Capability.READ, scope)
            from .compat import authorize_task_read
            if not authorize_task_read(actor, parsed_task_id):
                abort(403)
        except (PermissionDenied, NotFound, ValueError, TypeError):
            abort(403)
        from .models import OutputPackage, OutputPackageFile, OutputPackagePointer, OutputPackagePublication, Publish
        try:
            limit = int(request.args.get("limit", 50))
            offset = int(request.args.get("offset", 0))
        except ValueError:
            abort(400)
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
            abort(400)
        rows = OutputPackage.query.filter_by(task_id=parsed_task_id).order_by(
            OutputPackage.created_at.desc(), OutputPackage.id.desc()
        ).limit(limit).offset(offset).all()
        jobs = {}
        if rows:
            from .models import Job
            for job in Job.query.filter(Job.payload["package_id"].astext.in_([str(row.id) for row in rows])).all():
                jobs[job.payload["package_id"]] = job
        pointers = {
            (str(row.output_type_id), row.package_type): row
            for row in OutputPackagePointer.query.filter_by(task_id=parsed_task_id).all()
        }
        publications = {
            str(row.package_id): row
            for row in OutputPackagePublication.query.filter(
                OutputPackagePublication.package_id.in_([row.id for row in rows])
            ).all()
        } if rows else {}
        package_publish_ids = [row.publish_id for row in publications.values()]
        formal_publishes = {
            str(row.id): row for row in Publish.query.filter(Publish.id.in_(package_publish_ids)).all()
        } if package_publish_ids else {}
        from .packages.publication import approved_publish_candidates
        session = current_app.extensions["sqlalchemy"].session
        result = []
        for row in rows:
            pointer = pointers.get((str(row.output_type_id), row.package_type))
            binding = publications.get(str(row.id))
            formal_publish = formal_publishes.get(str(binding.publish_id)) if binding else None
            candidates = approved_publish_candidates(session, row)
            result.append({
                "id": str(row.id), "production_id": str(row.production_id),
                "entity_id": str(row.entity_id), "task_id": str(row.task_id),
                "output_type_id": str(row.output_type_id), "package_type": row.package_type,
                "version": row.version, "state": row.state, "qc_state": row.qc_state,
                "job_state": jobs[str(row.id)].state if str(row.id) in jobs else None,
                "job_error_code": jobs[str(row.id)].last_error_code if str(row.id) in jobs else None,
                "qc_report": row.qc_report, "manifest_sha256": row.manifest_sha256,
                "file_count": OutputPackageFile.query.filter_by(package_id=row.id).count(),
                "manifest_summary": {
                    key: value for key, value in json.loads(row.manifest_bytes).items() if key != "files"
                },
                "formal_publish": ({
                    "id": str(formal_publish.id), "version": formal_publish.version,
                    "state": formal_publish.state, "qc_state": formal_publish.qc_state,
                    "manifest_sha256": formal_publish.manifest_sha256,
                    "approval_active": any(item.id == formal_publish.id for item in candidates),
                } if formal_publish else None),
                "approved_publish_candidates": [{"id": str(item.id), "version": item.version} for item in candidates],
                "latest_working": pointer.latest_output_id == row.id if pointer else False,
                "latest_published": pointer.latest_published_id == row.id if pointer else False,
                "latest_approved": pointer.latest_approved_id == row.id if pointer else False,
                "latest_final_delivery": pointer.latest_final_delivery_id == row.id if pointer else False,
            })
        return result

    @jwt_required()
    def post(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            parsed_task_id = UUID(str(task_id))
            actor, scope = _task_actor_scope(parsed_task_id)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, parsed_task_id, "publish"):
                abort(403)
            payload = request.get_json(silent=False) or {}
            output_type_id = UUID(str(payload.get("output_type_id", "")))
            package_type = payload.get("package_type")
            source_revision_id = UUID(str(payload["source_working_revision_id"])) if payload.get("source_working_revision_id") else None
            frame_start = payload.get("frame_start")
            frame_end = payload.get("frame_end")
            color_space = payload.get("color_space")
            entries = payload.get("files")
            if not isinstance(entries, list) or not 1 <= len(entries) <= 10000:
                abort(400)
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 128:
                abort(400)
        except (PermissionDenied, NotFound, ValueError, TypeError, AttributeError):
            abort(403)

        from .packages.manifest import canonical_package_manifest
        from .packages.service import PackageConflict, reserve_output_package
        from zou.app.models.output_type import OutputType
        from zou.app.models.task import Task
        task = Task.query.filter_by(id=parsed_task_id).one_or_none()
        if task is None or task.project_id != scope.production_id:
            abort(404)
        if current_app.extensions["sqlalchemy"].session.get(OutputType, output_type_id) is None:
            abort(400, description="Output Type must be an existing Kitsu record")
        connection = _production_storage_connection(scope.production_id)
        adapter = _storage_adapter(connection)
        capabilities = adapter.capabilities(connection.allowed_root_id)
        if not capabilities.root_visible or not capabilities.can_add_children:
            abort(409, description="Allowed storage root is not available for package files")
        normalized = []
        total_bytes = 0
        try:
            for entry in entries:
                if not isinstance(entry, dict):
                    abort(400)
                provider_id = entry.get("provider_object_id")
                if not isinstance(provider_id, str) or not provider_id or len(provider_id) > 255:
                    abort(400)
                obj = adapter.get(provider_id)
                if obj is None or obj.object_id != provider_id or obj.trashed or obj.mime_type == "folder" or obj.parent_id is None:
                    abort(404)
                adapter.assert_parent_within_root(obj.parent_id)
                if obj.size is None:
                    abort(409, description="Provider must supply the file size before package registration")
                requested_sha256 = entry.get("sha256")
                if requested_sha256 and obj.sha256 and requested_sha256.lower() != obj.sha256.lower():
                    abort(409, description="Provided SHA-256 differs from provider metadata")
                total_bytes += obj.size
                normalized.append({
                    "source_provider_object_id": obj.object_id, "file_name": obj.name or "unnamed",
                    "byte_count": obj.size, "sha256": requested_sha256 or obj.sha256,
                    "frame_number": entry.get("frame_number"),
                    "file_format": entry.get("file_format"), "color_space": entry.get("color_space"),
                    "is_proxy": entry.get("is_proxy", False),
                })
            if total_bytes + connection.used_bytes > connection.quota_bytes:
                abort(413, description="Output Package exceeds the Production storage quota")
            if source_revision_id is not None:
                from .models import WorkingRevision
                source = WorkingRevision.query.filter_by(id=source_revision_id).one_or_none()
                if (source is None or source.task_id != parsed_task_id or source.production_id != scope.production_id
                        or source.state != "completed"):
                    abort(400, description="Source Working revision must be completed in the same Task and Production")
                from .models import WorkingRevisionFile
                source_object_ids = {
                    row.provider_object_id for row in WorkingRevisionFile.query.filter_by(
                        revision_id=source.id, state="completed",
                    ).all() if row.provider_object_id
                }
                if any(item["source_provider_object_id"] not in source_object_ids for item in normalized):
                    abort(400, description="Every Output Package source file must belong to the selected Working revision")
            # Validate before opening the reservation transaction; the service repeats canonical encoding.
            canonical_package_manifest(
                output_type_id=str(output_type_id), package_type=package_type, files=normalized,
                frame_start=frame_start, frame_end=frame_end, color_space=color_space,
            )
            from sqlalchemy.orm import sessionmaker
            sessions = sessionmaker(bind=current_app.extensions["sqlalchemy"].engine, expire_on_commit=False)
            result = reserve_output_package(
                sessions, task_id=parsed_task_id, production_id=scope.production_id,
                entity_id=task.entity_id, output_type_id=output_type_id,
                storage_connection_id=connection.id,
                package_type=package_type, actor_id=actor.actor_id, idempotency_key=key,
                files=normalized, frame_start=frame_start, frame_end=frame_end,
                color_space=color_space, source_working_revision_id=source_revision_id,
            )
        except (PermissionDenied, NotFound):
            abort(403)
        except PackageConflict as error:
            abort(409, description=str(error))
        except (ValueError, TypeError) as error:
            abort(400, description=str(error))
        return {"id": str(result.package_id), "job_id": str(result.job_id), "version": result.version, "state": result.state}, 202


class TaskOutputPackageOptions(MethodView):
    @jwt_required()
    def get(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            parsed_task_id = UUID(str(task_id))
            from zou.app.models.output_type import OutputType
            from zou.app.models.task import Task
            task = Task.query.filter_by(id=parsed_task_id).one_or_none()
            if task is None:
                abort(404)
            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(task.id))
            authorize(actor, Capability.READ, scope)
            from .compat import authorize_task_read
            if not authorize_task_read(actor, task.id):
                abort(403)
            connection = _production_storage_connection(scope.production_id)
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError):
            abort(404)
        adapter = _storage_adapter(connection)
        capabilities = adapter.capabilities(connection.allowed_root_id)
        if not capabilities.root_visible:
            abort(409, description="Allowed storage root is unavailable")
        output_types = OutputType.query.order_by(OutputType.name).limit(500).all()
        return {
            "task_id": str(task.id), "production_id": str(task.project_id),
            "storage_root_id": connection.allowed_root_id,
            "output_types": [{"id": str(row.id), "name": row.name} for row in output_types],
        }


class TaskStorageObjects(MethodView):
    @jwt_required()
    def get(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            parsed_task_id = UUID(str(task_id))
            from zou.app.models.task import Task
            task = Task.query.filter_by(id=parsed_task_id).one_or_none()
            if task is None:
                abort(404)
            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(task.id))
            authorize(actor, Capability.READ, scope)
            from .compat import authorize_task_read
            if not authorize_task_read(actor, task.id):
                abort(403)
            connection = _production_storage_connection(scope.production_id)
            adapter = _storage_adapter(connection)
            capabilities = adapter.capabilities(connection.allowed_root_id)
            if not capabilities.root_visible:
                abort(409, description="Allowed storage root is unavailable")
            parent_id = request.args.get("parent_id") or connection.allowed_root_id
            if len(parent_id) > 255:
                abort(400)
            if parent_id != connection.allowed_root_id:
                parent = adapter.get(parent_id)
                if parent is None or parent.trashed or parent.mime_type != "folder":
                    abort(404)
            adapter.assert_parent_within_root(parent_id)
            page_token = request.args.get("page_token")
            if page_token is not None and len(page_token) > 2048:
                abort(400)
            listing = adapter.list_children(parent_id, page_token=page_token)
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError):
            abort(404)
        return {
            "task_id": str(task.id), "parent_id": parent_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "sync_status": "confirmed" if listing.complete and listing.next_page_token is None else "partial",
            "items": [{
                "id": item.object_id, "parent_id": item.parent_id, "name": item.name,
                "mime_type": item.mime_type, "is_folder": item.mime_type == "folder",
                "size": item.size, "sha256": item.sha256, "trashed": item.trashed,
                "modified_time": item.modified_time, "md5_checksum": item.md5_checksum,
            } for item in listing.items if not item.trashed],
            "next_page_token": listing.next_page_token,
            "complete": listing.complete,
        }


class OutputPackagePublicationBinding(MethodView):
    @jwt_required()
    def post(self, package_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            package_uuid = UUID(str(package_id))
            from .models import OutputPackage
            package = OutputPackage.query.filter_by(id=package_uuid).one_or_none()
            if package is None:
                abort(404)
            actor = _request_actor(str(package.production_id))
            scope = _resolve_scope(actor, str(package.production_id), str(package.task_id))
            authorize(actor, Capability.PUBLISH, scope)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, package.task_id, "publish"):
                abort(403)
            payload = request.get_json(silent=False) or {}
            if set(payload) != {"publish_id"}:
                abort(400)
            publish_uuid = UUID(str(payload["publish_id"]))
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 255:
                abort(400)
            from sqlalchemy.orm import sessionmaker
            db_ext = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db_ext.engine, expire_on_commit=False)
            from .packages.publication import bind_approved_publish
            result = bind_approved_publish(
                sessions, package_id=package_uuid, publish_id=publish_uuid,
                actor_id=actor.actor_id, idempotency_key=key,
            )
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError) as error:
            abort(400, description=str(error))
        except PackagePublicationConflict as error:
            abort(409, description=str(error))
        return {
            "id": str(result.id), "package_id": str(result.package_id),
            "publish_id": str(result.publish_id), "replayed": result.replayed,
        }, 200 if result.replayed else 201


class OutputPackageDeliveryTargets(MethodView):
    @jwt_required()
    def get(self, package_id):
        _require_enabled()
        _assert_compatible()
        try:
            package_uuid = UUID(str(package_id))
            from .models import OutputPackage
            from zou.app.models.department import Department
            from zou.app.models.project import ProjectTaskTypeLink
            from zou.app.models.task import Task
            from zou.app.models.task_type import TaskType
            package = OutputPackage.query.filter_by(id=package_uuid).one_or_none()
            if package is None:
                abort(404)
            actor = _request_actor(str(package.production_id))
            scope = _resolve_scope(actor, str(package.production_id), str(package.task_id))
            authorize(actor, Capability.HANDOFF, scope)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, package.task_id, "handoff"):
                abort(403)
            source_task = Task.query.filter_by(id=package.task_id).one_or_none()
            if source_task is None or package.state != "completed" or package.qc_state != "passed":
                abort(409)
            target_tasks = Task.query.filter_by(
                project_id=package.production_id, entity_id=package.entity_id,
            ).order_by(Task.task_type_id, Task.name, Task.id).limit(501).all()
            if len(target_tasks) > 500:
                abort(409, description="Too many recipient Tasks; narrow the Production Task Types")
            result = []
            for target in target_tasks:
                if target.id == source_task.id:
                    continue
                task_type = TaskType.query.filter_by(id=target.task_type_id).one_or_none()
                if task_type is None or ProjectTaskTypeLink.query.filter_by(
                    project_id=package.production_id, task_type_id=task_type.id,
                ).one_or_none() is None:
                    continue
                department = Department.query.filter_by(id=task_type.department_id).one_or_none() if task_type.department_id else None
                if department is None:
                    continue
                result.append({
                    "task_id": str(target.id), "task_name": target.name,
                    "task_type_id": str(task_type.id), "task_type_name": task_type.name,
                    "department_id": str(department.id) if department else None,
                    "department_name": department.name if department else None,
                })
            return {"package_id": str(package.id), "source_task_id": str(source_task.id), "targets": result}
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError):
            abort(404)


class TaskPackageDeliveries(MethodView):
    @jwt_required()
    def get(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            task_uuid = UUID(str(task_id))
            from .models import OutputPackage, OutputPackageDelivery, OutputPackageDeliveryEvent
            from zou.app.models.task import Task
            task = Task.query.filter_by(id=task_uuid).one_or_none()
            if task is None:
                abort(404)
            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(task.id))
            authorize(actor, Capability.READ, scope)
            from .compat import authorize_task_read
            if not authorize_task_read(actor, task.id):
                abort(403)
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError):
            abort(404)

        rows = OutputPackageDelivery.query.filter_by(
            production_id=task.project_id,
            recipient_task_id=task.id,
        ).order_by(OutputPackageDelivery.updated_at.desc(), OutputPackageDelivery.id.desc()).limit(200).all()
        outgoing_packages = OutputPackage.query.filter_by(
            production_id=task.project_id, task_id=task.id,
        ).order_by(OutputPackage.created_at.desc()).limit(200).all()
        outgoing_ids = {row.id for row in outgoing_packages}
        outgoing_rows = OutputPackageDelivery.query.filter(
            OutputPackageDelivery.package_id.in_(outgoing_ids),
        ).order_by(OutputPackageDelivery.updated_at.desc(), OutputPackageDelivery.id.desc()).limit(200).all() if outgoing_ids else []
        deliveries = {row.id: row for row in rows + outgoing_rows}
        events = OutputPackageDeliveryEvent.query.filter(
            OutputPackageDeliveryEvent.delivery_id.in_(deliveries),
        ).order_by(OutputPackageDeliveryEvent.created_at, OutputPackageDeliveryEvent.id).all() if deliveries else []
        from zou.app.models.task import Task as ZouTask
        from zou.app.models.task_type import TaskType
        output = []
        for delivery in deliveries.values():
            package = OutputPackage.query.filter_by(id=delivery.package_id).one_or_none()
            if package is None or package.production_id != task.project_id:
                continue
            source_task = ZouTask.query.filter_by(id=package.task_id).one_or_none()
            source_type = TaskType.query.filter_by(id=source_task.task_type_id).one_or_none() if source_task else None
            recipient_type = TaskType.query.filter_by(id=delivery.recipient_task_type_id).one_or_none()
            output.append({
                "id": str(delivery.id), "direction": "incoming" if delivery.recipient_task_id == task.id else "outgoing",
                "package_id": str(package.id), "package_version": package.version,
                "package_type": package.package_type, "manifest_sha256": package.manifest_sha256,
                "source_task_id": str(package.task_id), "source_task_type_name": source_type.name if source_type else None,
                "recipient_task_id": str(delivery.recipient_task_id),
                "recipient_task_type_id": str(delivery.recipient_task_type_id),
                "recipient_task_type_name": recipient_type.name if recipient_type else None,
                "revision": delivery.delivery_revision, "state": delivery.state,
                "feedback": delivery.feedback if delivery.recipient_task_id == task.id else None,
                "can_respond": delivery.recipient_task_id == task.id and delivery.state in {"available", "delivered", "in_review", "revised"},
                "history": [{
                    "event_type": event.event_type, "feedback": event.feedback,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                } for event in events if event.delivery_id == delivery.id],
                "files_url": f"/api/plugins/vfx_files/output-packages/{package.id}/files?limit=100&offset=0",
            })
        return {"task_id": str(task.id), "deliveries": output}


class OutputPackageDeliveryCreate(MethodView):
    @jwt_required()
    def post(self, package_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            package_uuid = UUID(str(package_id))
            from .models import OutputPackage
            package = OutputPackage.query.filter_by(id=package_uuid).one_or_none()
            if package is None:
                abort(404)
            actor = _request_actor(str(package.production_id))
            scope = _resolve_scope(actor, str(package.production_id), str(package.task_id))
            authorize(actor, Capability.HANDOFF, scope)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, package.task_id, "handoff"):
                abort(403)
            payload = request.get_json(silent=False) or {}
            if set(payload) != {"recipient_task_id", "recipient_task_type_id"}:
                abort(400)
            recipient_task_id = UUID(str(payload["recipient_task_id"]))
            recipient_task_type_id = UUID(str(payload["recipient_task_type_id"]))
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 255:
                abort(400)
            from sqlalchemy.orm import sessionmaker
            db_ext = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db_ext.engine, expire_on_commit=False)
            from .packages.delivery_service import create_package_delivery
            result = create_package_delivery(
                sessions, package_id=package_uuid,
                recipient_task_id=recipient_task_id,
                recipient_task_type_id=recipient_task_type_id,
                sender_id=actor.actor_id, idempotency_key=key,
            )
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError) as error:
            abort(400, description=str(error))
        except PackageDeliveryDenied as error:
            abort(403, description=str(error))
        except PackageDeliveryConflict as error:
            abort(409, description=str(error))
        return _package_delivery_response(result), 201 if not result.replayed else 200


class OutputPackageDeliveryResponse(MethodView):
    @jwt_required()
    def post(self, delivery_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            delivery_uuid = UUID(str(delivery_id))
            from .models import OutputPackage, OutputPackageDelivery
            from zou.app.models.task import Task, TaskPersonLink
            from zou.app.models.person import DepartmentLink, Person
            from zou.app.models.task_type import TaskType
            delivery = OutputPackageDelivery.query.filter_by(id=delivery_uuid).one_or_none()
            package = OutputPackage.query.filter_by(id=delivery.package_id).one_or_none() if delivery else None
            recipient = Task.query.filter_by(id=delivery.recipient_task_id).one_or_none() if delivery else None
            if (
                delivery is None or package is None or recipient is None
                or package.production_id != delivery.production_id
                or package.entity_id != recipient.entity_id
                or package.task_id == recipient.id
                or recipient.task_type_id != delivery.recipient_task_type_id
            ):
                abort(404)
            actor_id = UUID(str(get_jwt_identity()))
            actor = _request_actor(str(delivery.production_id))
            scope = _resolve_scope(actor, str(delivery.production_id), str(recipient.id))
            authorize(actor, Capability.HANDOFF, scope)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, recipient.id, "handoff"):
                abort(403)
            from zou.app.models.project import ProjectPersonLink, ProjectTaskTypeLink
            person = current_app.extensions["sqlalchemy"].session.get(Person, actor_id)
            task_type = TaskType.query.filter_by(id=recipient.task_type_id).one_or_none()
            if person is None or not getattr(person, "active", True) or task_type is None:
                abort(403)
            if ProjectTaskTypeLink.query.filter_by(
                project_id=recipient.project_id, task_type_id=recipient.task_type_id,
            ).one_or_none() is None:
                abort(403)
            # Recipient actions require current assignment and department
            # membership. Production admins/managers still need the exact Task
            # authorization checked above and cannot cross Productions.
            if actor.project_role not in {"admin", "manager"} and (
                TaskPersonLink.query.filter_by(task_id=recipient.id, person_id=actor_id).one_or_none() is None
                or task_type.department_id is None
                or DepartmentLink.query.filter_by(department_id=task_type.department_id, person_id=actor_id).one_or_none() is None
                or ProjectPersonLink.query.filter_by(project_id=delivery.production_id, person_id=actor_id).one_or_none() is None
            ):
                abort(403)
            payload = request.get_json(silent=False) or {}
            if set(payload) - {"status", "feedback"} or payload.get("status") not in {"accepted", "needs_fix"}:
                abort(400)
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 255:
                abort(400)
            from sqlalchemy.orm import sessionmaker
            db_ext = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db_ext.engine, expire_on_commit=False)
            from .packages.delivery_service import respond_to_package_delivery
            result = respond_to_package_delivery(
                sessions, delivery_id=delivery_uuid, actor_id=actor_id,
                target=payload["status"], feedback=payload.get("feedback"),
                idempotency_key=key,
            )
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError) as error:
            abort(400, description=str(error))
        except PackageDeliveryDenied as error:
            abort(403, description=str(error))
        except PackageDeliveryConflict as error:
            abort(409, description=str(error))
        return _package_delivery_response(result), 200


def _package_delivery_response(result):
    return {
        "id": str(result.id), "package_id": str(result.package_id),
        "production_id": str(result.production_id),
        "recipient_task_id": str(result.recipient_task_id),
        "recipient_task_type_id": str(result.recipient_task_type_id),
        "revision": result.revision, "state": result.state, "replayed": result.replayed,
    }


class OutputPackageFiles(MethodView):
    @jwt_required()
    def get(self, package_id):
        _require_enabled()
        _assert_compatible()
        try:
            parsed_package_id = UUID(str(package_id))
            from .models import OutputPackage, OutputPackageFile
            package = OutputPackage.query.filter_by(id=parsed_package_id).one_or_none()
            if package is None:
                abort(404)
            actor = _request_actor(str(package.production_id))
            authorized_task_id = _output_package_read_task_id(actor, package)
            if authorized_task_id is None:
                abort(403)
            scope = _resolve_scope(actor, str(package.production_id), str(authorized_task_id))
            authorize(actor, Capability.READ, scope)
            limit = int(request.args.get("limit", 200))
            offset = int(request.args.get("offset", 0))
            if not 1 <= limit <= 500 or not 0 <= offset <= 10000000:
                abort(400)
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError):
            abort(404)
        rows = OutputPackageFile.query.filter_by(package_id=package.id).order_by(
            OutputPackageFile.ordinal
        ).limit(limit + 1).offset(offset).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "package_id": str(package.id), "limit": limit, "offset": offset,
            "next_offset": offset + limit if has_more else None,
            "files": [{
                "id": str(row.id), "ordinal": row.ordinal, "file_name": row.file_name,
                "provider_object_id": row.provider_object_id if package.state == "completed" else None,
                "frame_number": row.frame_number, "byte_count": row.byte_count,
                "sha256": row.sha256, "file_format": row.file_format,
                "color_space": row.color_space, "is_proxy": row.is_proxy,
                "download_url": f"/api/vfx_files/output-package-files/{row.id}/download" if package.state == "completed" else None,
            } for row in rows],
        }


class OutputPackageFileDownload(MethodView):
    @jwt_required()
    def get(self, package_file_id):
        _require_enabled()
        _assert_compatible()
        try:
            package_file_uuid = UUID(str(package_file_id))
            from .models import OutputPackage, OutputPackageFile
            file_row = OutputPackageFile.query.filter_by(id=package_file_uuid).one_or_none()
            package = OutputPackage.query.filter_by(id=file_row.package_id).one_or_none() if file_row else None
            if package is None or file_row is None or package.state != "completed":
                abort(404)
            actor = _request_actor(str(package.production_id))
            authorized_task_id = _output_package_read_task_id(actor, package)
            if authorized_task_id is None:
                abort(403)
            scope = _resolve_scope(actor, str(package.production_id), str(authorized_task_id))
            authorize(actor, Capability.READ, scope)
            connection = _production_storage_connection(package.production_id)
            adapter = _storage_adapter(connection)
            capabilities = adapter.capabilities(connection.allowed_root_id)
            if not capabilities.root_visible:
                abort(403)
            obj = adapter.get(file_row.provider_object_id)
            if obj is None or obj.trashed or obj.parent_id is None:
                abort(404)
            adapter.assert_parent_within_root(obj.parent_id)
            if not adapter.verify(file_row.provider_object_id, file_row.byte_count, file_row.sha256):
                abort(409, description="Output Package file failed integrity verification")
            stream = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
            try:
                count, digest = adapter.download_to(file_row.provider_object_id, stream)
                if count != file_row.byte_count or digest != file_row.sha256:
                    abort(409, description="Downloaded Output Package file failed integrity verification")
                stream.seek(0)
                response = send_file(stream, mimetype="application/octet-stream", as_attachment=True,
                                     download_name=file_row.file_name, conditional=False)
                response.call_on_close(stream.close)
                return response
            except Exception:
                stream.close()
                raise
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError):
            abort(404)


class TaskPublishes(MethodView):
    @jwt_required()
    def post(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            _require_mutation_origin()
            parsed_task_id = UUID(str(task_id))
            actor, _scope = _task_actor_scope(parsed_task_id)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, parsed_task_id, "publish"):
                abort(403)
            payload = request.get_json(silent=False) or {}
            family_id = UUID(str(payload.get("output_family_id", "")))
            source_file_id = UUID(str(payload.get("source_revision_file_id", "")))
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 128:
                abort(400)
            from sqlalchemy.orm import sessionmaker
            from .publishes.service import PublishConflict, reserve_working_revision_publish
            db_ext = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db_ext.engine, expire_on_commit=False)
            result = reserve_working_revision_publish(
                sessions, task_id=parsed_task_id, output_family_id=family_id,
                source_revision_file_id=source_file_id, actor_id=actor.actor_id,
                idempotency_key=key,
            )
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError) as error:
            abort(400, description=str(error))
        except PublishConflict as error:
            abort(409, description=str(error))
        return {"id": str(result.id), "version": result.version, "state": result.state}, 202


class TaskPublishOptions(MethodView):
    @jwt_required()
    def get(self, task_id):
        _require_enabled()
        _assert_compatible()
        try:
            parsed_task_id = UUID(str(task_id))
            from zou.app.models.task import Task, TaskPersonLink
            from zou.app.models.project import ProjectTaskTypeLink
            from zou.app.models.task_type import TaskType
            from zou.app.models.person import Person
            from .models import OutputFamilyMapping
            task = Task.query.filter_by(id=parsed_task_id).one_or_none()
            if task is None:
                abort(404)
            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(task.id))
            authorize(actor, Capability.READ, scope)
            from .compat import authorize_task_read
            if not authorize_task_read(actor, task.id):
                abort(403)
            families = OutputFamilyMapping.query.filter_by(
                production_id=task.project_id, task_type_id=task.task_type_id, enabled=True
            ).order_by(OutputFamilyMapping.family_key).all()
            assignees = (
                Person.query.join(TaskPersonLink, TaskPersonLink.person_id == Person.id)
                .filter(TaskPersonLink.task_id == task.id, Person.active.is_(True))
                .order_by(Person.last_name, Person.first_name).all()
            )
            targets = (
                Task.query.join(TaskType, TaskType.id == Task.task_type_id)
                .join(ProjectTaskTypeLink, ProjectTaskTypeLink.task_type_id == TaskType.id)
                .filter(Task.project_id == task.project_id, Task.entity_id == task.entity_id, Task.id != task.id)
                .filter(ProjectTaskTypeLink.project_id == task.project_id, TaskType.archived.is_(False))
                .order_by(TaskType.name, Task.id).all()
            )
            target_types = {item.id: item for item in TaskType.query.filter(TaskType.id.in_({target.task_type_id for target in targets})).all()} if targets else {}
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError):
            abort(404)
        return {
            "task_id": str(task.id),
            "actor_id": str(actor.actor_id),
            "role": actor.project_role,
            "families": [{"id": str(row.id), "name": row.name, "family_key": row.family_key} for row in families],
            "recipients": [{"id": str(row.id), "name": f"{row.first_name} {row.last_name}".strip()} for row in assignees if row.id != actor.actor_id],
            "department_targets": [
                {"task_id": str(target.id), "task_type_id": str(target.task_type_id), "department_id": str(target_types[target.task_type_id].department_id), "task_type_name": target_types[target.task_type_id].name}
                for target in targets if target.task_type_id in target_types and target_types[target.task_type_id].department_id is not None
            ],
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
        from sqlalchemy import or_
        from .models import Handoff, Job, OutputFamilyMapping, Publish, PublishPointer, UploadSession
        from zou.app.models.entity import Entity
        from zou.app.models.task import Task
        from zou.app.models.task_type import TaskType

        if scope.task_id is not None:
            task_rows = [Task.query.filter_by(id=scope.task_id).one()]
            tasks_truncated = False
        else:
            task_rows = Task.query.filter_by(project_id=scope.production_id).order_by(Task.id).limit(1001).all()
            tasks_truncated = len(task_rows) > 1000
            task_rows = task_rows[:1000]
        from .compat import authorize_task_read
        if scope.task_id is not None:
            if not authorize_task_read(actor, scope.task_id):
                abort(403)
        else:
            task_rows = [row for row in task_rows if authorize_task_read(actor, row.id)]
        task_ids = [row.id for row in task_rows]
        if not task_ids:
            return {"production_id": str(scope.production_id), "task_id": str(scope.task_id) if scope.task_id else None, "actor_id": str(actor.actor_id), "role": actor.project_role, "tasks": [], "tasks_truncated": False, "upload_ready": False, "files": [], "publishes": [], "handoffs": []}
        entity_names = {
            row.id: row.name for row in Entity.query.filter(
                Entity.id.in_({task.entity_id for task in task_rows})
            ).all()
        }
        task_type_names = {
            row.id: row.name for row in TaskType.query.filter(
                TaskType.id.in_({task.task_type_id for task in task_rows})
            ).all()
        }
        publishes = Publish.query.filter(Publish.task_id.in_(task_ids)).order_by(Publish.created_at.desc()).limit(200).all()
        jobs = Job.query.filter_by(job_type="publish_artifact").order_by(Job.created_at.desc()).limit(500).all()
        publish_jobs = {str(job.payload.get("publish_id")): job for job in jobs if job.payload.get("publish_id")}
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
                "created_by": str(publish.created_by),
                "manifest_sha256": publish.manifest_sha256,
                "byte_count": publish.byte_count,
                "sha256": publish.sha256,
                "provider_artifact_id": publish.provider_artifact_id if publish.state == "completed" else None,
                "native_output_file_id": str(publish.native_output_file_id) if publish.native_output_file_id else None,
                "job_state": publish_jobs.get(str(publish.id)).state if publish_jobs.get(str(publish.id)) else None,
                "job_error": publish_jobs.get(str(publish.id)).last_error_code if publish_jobs.get(str(publish.id)) else None,
                "manifest": json.loads(publish.manifest_bytes) if publish.manifest_bytes else None,
                "latest_published": bool(pointer and pointer.latest_published_id == publish.id),
                "latest_approved": bool(pointer and pointer.latest_approved_id == publish.id),
            })
        handoffs = Handoff.query.filter(or_(Handoff.task_id.in_(task_ids), Handoff.recipient_task_id.in_(task_ids))).order_by(Handoff.created_at.desc()).limit(200).all()
        uploads = UploadSession.query.filter(UploadSession.task_id.in_(task_ids)).order_by(UploadSession.created_at.desc()).limit(500).all()
        try:
            storage_connection = _production_storage_connection(scope.production_id)
        except ServiceUnavailable:
            storage_connection = None
        worker_enabled = _setting("VFX_FILES_WORKER_ENABLED", "false") in (True, "true", "1", 1)
        staging_root = _setting("VFX_FILES_STAGING_ROOT")
        upload_ready = storage_connection is not None and bool(staging_root) and worker_enabled and not tasks_truncated
        return {
            "production_id": str(scope.production_id),
            "task_id": str(scope.task_id) if scope.task_id else None,
            "actor_id": str(actor.actor_id),
            "role": actor.project_role,
            "tasks": [{
                "id": str(row.id), "name": row.name,
                "entity_id": str(row.entity_id), "entity_name": entity_names.get(row.entity_id),
                "task_type_id": str(row.task_type_id), "task_type_name": task_type_names.get(row.task_type_id),
            } for row in task_rows],
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
            "handoffs": [{"id": str(row.id), "task_id": str(row.task_id), "publish_id": str(row.publish_id), "sender_id": str(row.sender_id), "recipient_id": str(row.recipient_id) if row.recipient_id else None, "recipient_department_id": str(row.recipient_department_id) if row.recipient_department_id else None, "recipient_task_id": str(row.recipient_task_id) if row.recipient_task_id else None, "recipient_person_id": str(row.recipient_person_id) if row.recipient_person_id else None, "state": row.state} for row in handoffs],
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
        from .handoffs.service import HandoffDenied, transition_handoff
        from sqlalchemy.orm import sessionmaker
        db_ext = current_app.extensions["sqlalchemy"]
        from .models import Handoff
        handoff = Handoff.query.filter_by(id=handoff_uuid).one_or_none()
        if handoff is None:
            abort(404)
        try:
            # Resolve project ownership from the authoritative Task row; do not use the body for scope.
            from zou.app.models.task import Task, TaskPersonLink
            from zou.app.models.task_type import TaskType
            from zou.app.models.person import DepartmentLink, Person
            from zou.app.models.project import ProjectPersonLink, ProjectTaskTypeLink
            source_task = Task.query.filter_by(id=handoff.task_id).one_or_none()
            task = Task.query.filter_by(id=handoff.recipient_task_id or handoff.task_id).one_or_none()
            if source_task is None or task is None:
                abort(404)
            actor = _request_actor(str(task.project_id))
            department_recipient_authorized = handoff.recipient_department_id is not None
            if department_recipient_authorized:
                task_type = TaskType.query.filter_by(id=task.task_type_id).one_or_none()
                from .handoffs.targets import department_recipient_is_current_member, department_target_matches
                target_matches = task_type is not None and department_target_matches(
                    source_project_id=source_task.project_id, source_entity_id=source_task.entity_id,
                    target_project_id=task.project_id, target_entity_id=task.entity_id,
                    target_department_id=task_type.department_id, requested_department_id=handoff.recipient_department_id,
                    department_exists=True,
                    task_type_in_project=ProjectTaskTypeLink.query.filter_by(project_id=task.project_id, task_type_id=task.task_type_id).one_or_none() is not None,
                )
                person_row = db_ext.session.get(Person, actor_id)
                member = department_recipient_is_current_member(
                    actor_id=actor_id, designated_person_id=handoff.recipient_person_id,
                    active=bool(person_row is not None and getattr(person_row, "active", True)),
                    project_member=ProjectPersonLink.query.filter_by(project_id=task.project_id, person_id=actor_id).one_or_none() is not None,
                    department_member=DepartmentLink.query.filter_by(department_id=handoff.recipient_department_id, person_id=actor_id).one_or_none() is not None,
                    task_assignee=TaskPersonLink.query.filter_by(task_id=task.id, person_id=actor_id).one_or_none() is not None,
                )
                if not target_matches or not member:
                    abort(403)
            else:
                person_row = db_ext.session.get(Person, actor_id)
                if (
                    handoff.recipient_id != actor_id
                    or person_row is None or not getattr(person_row, "active", True)
                    or ProjectPersonLink.query.filter_by(project_id=task.project_id, person_id=actor_id).one_or_none() is None
                    or TaskPersonLink.query.filter_by(task_id=task.id, person_id=actor_id).one_or_none() is None
                ):
                    abort(403)
            scope = _resolve_scope(actor, str(task.project_id), str(task.id))
            authorize(actor, Capability.HANDOFF, scope)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, task.id, "handoff"):
                abort(403)
            payload = request.get_json(silent=False) or {}
            idempotency_key = request.headers.get("Idempotency-Key", "").strip()
            if not idempotency_key or len(idempotency_key) > 128:
                abort(400)
            def current_department_membership(session, record, responder_id):
                if record.recipient_task_id is None or record.recipient_department_id is None:
                    return False
                source = session.get(Task, record.task_id)
                destination = session.get(Task, record.recipient_task_id)
                destination_type = session.get(TaskType, destination.task_type_id) if destination is not None else None
                person = session.get(Person, responder_id)
                return bool(
                    source is not None and destination is not None and destination_type is not None
                    and department_target_matches(
                        source_project_id=source.project_id, source_entity_id=source.entity_id,
                        target_project_id=destination.project_id, target_entity_id=destination.entity_id,
                        target_department_id=destination_type.department_id,
                        requested_department_id=record.recipient_department_id, department_exists=True,
                        task_type_in_project=session.query(ProjectTaskTypeLink.id).filter_by(
                            project_id=destination.project_id,
                            task_type_id=destination.task_type_id,
                        ).one_or_none() is not None,
                    )
                    and department_recipient_is_current_member(
                        actor_id=responder_id, designated_person_id=record.recipient_person_id,
                        active=bool(person is not None and getattr(person, "active", True)),
                        project_member=session.get(ProjectPersonLink, (destination.project_id, responder_id)) is not None,
                        department_member=session.get(DepartmentLink, (responder_id, record.recipient_department_id)) is not None,
                        task_assignee=session.get(TaskPersonLink, (destination.id, responder_id)) is not None,
                    )
                )

            result = transition_handoff(
                sessionmaker(bind=db_ext.engine, expire_on_commit=False),
                actor_id=actor_id,
                handoff_id=handoff_uuid,
                target=payload.get("target"),
                feedback=payload.get("feedback"),
                idempotency_key=idempotency_key,
                authorize_department_recipient=current_department_membership,
            )
        except NotFound:
            abort(404)
        except PermissionDenied:
            abort(403)
        except HandoffDenied:
            abort(403)
        except (ValueError, TypeError):
            abort(400)
        return {"id": str(result.id), "state": result.state, "publish_id": str(result.publish_id)}


class PublishApproval(MethodView):
    @jwt_required()
    def post(self, publish_id):
        _require_enabled()
        _assert_compatible()
        from .models import Publish
        row = Publish.query.filter_by(id=publish_id).one_or_none()
        if row is None:
            abort(404)
        from zou.app.models.task import Task
        task = Task.query.filter_by(id=row.task_id).one_or_none()
        if task is None:
            abort(404)
        from .approvals.service import ApprovalDenied, approve_publish
        try:
            _require_mutation_origin()
            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(task.id))
            authorize(actor, Capability.APPROVE, scope)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, task.id, "approve"):
                abort(403)
            payload = request.get_json(silent=False) or {}
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 128:
                abort(400)
            from sqlalchemy.orm import sessionmaker
            db_ext = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db_ext.engine, expire_on_commit=False)
            approval_id = approve_publish(
                sessions,
                actor_id=actor.actor_id,
                actor_role=actor.project_role,
                publish_id=UUID(str(row.id)),
                policy_id=str(payload.get("policy_id", "")),
                policy_sha256=str(payload.get("policy_sha256", "")),
                idempotency_key=key,
            )
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError) as error:
            abort(400, description=str(error))
        except ApprovalDenied as error:
            abort(403, description=str(error))
        from .models import PublishPointer
        pointer = PublishPointer.query.filter_by(task_id=task.id, output_family_id=row.output_family_id).one_or_none()
        return {"approval_id": str(approval_id), "latest_approved_id": str(pointer.latest_approved_id) if pointer and pointer.latest_approved_id else None}, 201


class PublishApprovalRevoke(MethodView):
    @jwt_required()
    def post(self, publish_id, approval_id):
        _require_enabled()
        _assert_compatible()
        from .approvals.service import ApprovalDenied, revoke_approval
        try:
            _require_mutation_origin()
            parsed_publish_id = UUID(str(publish_id))
            parsed_approval_id = UUID(str(approval_id))
            from .models import ApprovalEvent, Publish, PublishPointer
            row = Publish.query.filter_by(id=parsed_publish_id).one_or_none()
            if row is None:
                abort(404)
            approval = ApprovalEvent.query.filter_by(
                id=parsed_approval_id, publish_id=row.id, event_type="approved",
            ).one_or_none()
            if approval is None:
                abort(404)
            from zou.app.models.task import Task
            task = Task.query.filter_by(id=row.task_id).one_or_none()
            if task is None:
                abort(404)

            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(task.id))
            authorize(actor, Capability.APPROVE, scope)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, task.id, "approve"):
                abort(403)

            payload = request.get_json(silent=False) or {}
            if not isinstance(payload, dict) or set(payload) != {"reason"} or not isinstance(payload.get("reason"), str):
                abort(400)
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 128:
                abort(400)

            from sqlalchemy.orm import sessionmaker
            db_ext = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db_ext.engine, expire_on_commit=False)
            revocation_id = revoke_approval(
                sessions, actor_id=actor.actor_id, actor_role=actor.project_role,
                approval_id=parsed_approval_id, reason=payload["reason"],
                idempotency_key=key,
            )
            pointer = PublishPointer.query.filter_by(
                task_id=task.id, output_family_id=row.output_family_id,
            ).one_or_none()
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError) as error:
            abort(400, description=str(error))
        except ApprovalDenied as error:
            abort(403, description=str(error))
        except PublishConflict as error:
            abort(409, description=str(error))
        return {
            "approval_id": str(parsed_approval_id),
            "revocation_id": str(revocation_id),
            "latest_approved_id": str(pointer.latest_approved_id) if pointer and pointer.latest_approved_id else None,
        }, 200


class HandoffCreate(MethodView):
    @jwt_required()
    def post(self, publish_id):
        _require_enabled()
        _assert_compatible()
        from .models import Publish
        row = Publish.query.filter_by(id=publish_id).one_or_none()
        if row is None:
            abort(404)
        from zou.app.models.person import DepartmentLink, Person
        from zou.app.models.project import ProjectPersonLink, ProjectTaskTypeLink
        from zou.app.models.task import Task, TaskPersonLink
        from zou.app.models.task_type import TaskType
        from zou.app.models.department import Department
        from .handoffs.service import HandoffDenied, create_handoff
        from sqlalchemy.orm import sessionmaker
        db_ext = current_app.extensions["sqlalchemy"]
        task = Task.query.filter_by(id=row.task_id).one_or_none()
        if task is None:
            abort(404)
        try:
            _require_mutation_origin()
            actor = _request_actor(str(task.project_id))
            scope = _resolve_scope(actor, str(task.project_id), str(task.id))
            authorize(actor, Capability.HANDOFF, scope)
            from .compat import authorize_task_mutation
            if not authorize_task_mutation(actor, task.id, "handoff"):
                abort(403)
            payload = request.get_json(silent=False) or {}
            recipient_id = recipient_department_id = recipient_task_id = recipient_person_id = None
            if payload.get("recipient_id"):
                recipient_id = UUID(str(payload.get("recipient_id")))
                recipient = db_ext.session.get(Person, recipient_id)
                is_task_assignee = TaskPersonLink.query.filter_by(task_id=task.id, person_id=recipient_id).one_or_none() is not None
                is_project_member = ProjectPersonLink.query.filter_by(project_id=task.project_id, person_id=recipient_id).one_or_none() is not None
                if recipient is None or not getattr(recipient, "active", True) or not (is_task_assignee and is_project_member):
                    abort(403)
            else:
                recipient_department_id = UUID(str(payload.get("recipient_department_id", "")))
                recipient_task_id = UUID(str(payload.get("recipient_task_id", "")))
                target_task = Task.query.filter_by(id=recipient_task_id).one_or_none()
                target_type = TaskType.query.filter_by(id=target_task.task_type_id).one_or_none() if target_task is not None else None
                department = db_ext.session.get(Department, recipient_department_id)
                from .handoffs.targets import department_target_matches
                if target_task is None or target_task.id == task.id or target_type is None or not department_target_matches(
                    source_project_id=task.project_id, source_entity_id=task.entity_id,
                    target_project_id=target_task.project_id, target_entity_id=target_task.entity_id,
                    target_department_id=target_type.department_id, requested_department_id=recipient_department_id,
                    department_exists=department is not None,
                    task_type_in_project=ProjectTaskTypeLink.query.filter_by(project_id=target_task.project_id, task_type_id=target_task.task_type_id).one_or_none() is not None,
                ):
                    abort(403)
                if payload.get("recipient_person_id"):
                    recipient_person_id = UUID(str(payload["recipient_person_id"]))
                    person = db_ext.session.get(Person, recipient_person_id)
                    if (
                        person is None or not getattr(person, "active", True)
                        or ProjectPersonLink.query.filter_by(project_id=task.project_id, person_id=recipient_person_id).one_or_none() is None
                        or DepartmentLink.query.filter_by(department_id=recipient_department_id, person_id=recipient_person_id).one_or_none() is None
                        or TaskPersonLink.query.filter_by(task_id=recipient_task_id, person_id=recipient_person_id).one_or_none() is None
                    ):
                        abort(403)
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 128:
                abort(400)
            sessions = sessionmaker(bind=db_ext.engine, expire_on_commit=False)
            result = create_handoff(
                sessions, task_id=task.id, publish_id=UUID(str(row.id)),
                sender_id=actor.actor_id, recipient_id=recipient_id,
                recipient_department_id=recipient_department_id, recipient_task_id=recipient_task_id,
                recipient_person_id=recipient_person_id, idempotency_key=key,
            )
        except (PermissionDenied, NotFound):
            abort(403)
        except (ValueError, TypeError) as error:
            abort(400, description=str(error))
        except HandoffDenied as error:
            abort(403, description=str(error))
        return {
            "id": str(result.id), "state": result.state, "publish_id": str(result.publish_id),
            "recipient_id": str(recipient_id) if recipient_id else None,
            "recipient_department_id": str(recipient_department_id) if recipient_department_id else None,
            "recipient_task_id": str(recipient_task_id) if recipient_task_id else None,
            "recipient_person_id": str(recipient_person_id) if recipient_person_id else None,
        }, 201


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
        connection = _production_storage_connection(scope.production_id)
        from .uploads.service import UploadDenied
        try:
            created = _upload_service().create_upload(
                task_id=parsed_task_id,
                actor_id=actor.actor_id,
                storage_connection_id=connection.id,
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
            from .compat import authorize_task_read
            if not authorize_task_read(actor, task.id):
                abort(403)
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
            connection = _production_storage_connection(scope.production_id)
            from sqlalchemy.orm import sessionmaker
            db = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=db.engine, expire_on_commit=False)
            from .models import WorkingRevision
            key = request.headers.get("Idempotency-Key", "").strip()
            receipt = reserve_working_revision(
                sessions,
                production_id=scope.production_id,
                task_id=parsed_task_id,
                storage_connection_id=connection.id,
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
            from .models import WorkingRevision, WorkingRevisionFile
            file_row = WorkingRevisionFile.query.filter_by(id=file_uuid).one_or_none()
            revision = WorkingRevision.query.filter_by(id=file_row.revision_id).one_or_none() if file_row else None
            if file_row is None or revision is None or revision.state != "completed" or file_row.state != "completed":
                abort(404)
            actor = _request_actor(str(revision.production_id))
            scope = _resolve_scope(actor, str(revision.production_id), str(revision.task_id))
            authorize(actor, Capability.READ, scope)
            from .compat import authorize_task_read
            if not authorize_task_read(actor, revision.task_id):
                abort(403)
            try:
                connection = _production_storage_connection(revision.production_id)
            except ServiceUnavailable:
                abort(404)
            if not file_row.provider_object_id:
                abort(404)
            adapter = _storage_adapter(connection)
            capabilities = adapter.capabilities(connection.allowed_root_id)
            if not capabilities.root_visible:
                abort(403)
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
            _reject_plugin_owned_storage_configuration(scope.production_id)
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
            _reject_plugin_owned_storage_configuration(scope.production_id)
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
            _reject_plugin_owned_storage_configuration(scope.production_id)
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
        from .folders.templates import TemplateConflict, TemplateDenied, set_configuration_enabled
        try:
            _require_mutation_origin()
            actor, scope = _production_provision_scope(production_id)
            _reject_plugin_owned_storage_configuration(scope.production_id)
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
        from .models import FolderBinding, KitsuSyncDesiredConfiguration, SharedTemplateConfiguration
        from zou.app.models.entity import Entity
        from zou.app.models.task import Task
        from zou.app.models.task_type import TaskType

        task = Task.query.filter_by(id=scope.task_id, project_id=scope.production_id).one_or_none()
        entity = Entity.query.filter_by(id=task.entity_id).one_or_none() if task is not None else None
        task_type = TaskType.query.filter_by(id=task.task_type_id).one_or_none() if task is not None else None
        if task is None or entity is None or task_type is None or task_type.for_entity not in ("Shot", "Asset"):
            abort(404)

        desired = KitsuSyncDesiredConfiguration.query.filter_by(production_id=scope.production_id).one_or_none()
        if desired is not None:
            # KitsuSync owns the selected connection, root, template selection
            # and activation. The Files page may only read already-persisted
            # Task bindings and must never derive a replacement tree locally.
            connection = _production_storage_connection(scope.production_id)
            task_scope_key = f"production:{scope.production_id}:task:{task.id}"
            bindings = [
                {
                    "scope_key": row.canonical_scope_key,
                    "provider_folder_id": row.provider_folder_id,
                    "state": row.state,
                    "parent_provider_id": row.parent_provider_id,
                }
                for row in FolderBinding.query.filter_by(
                    storage_connection_id=connection.id,
                    allowed_root_id=connection.allowed_root_id,
                ).all()
                if row.canonical_scope_key == task_scope_key
                or row.canonical_scope_key.startswith(f"{task_scope_key}:template:")
            ]
            enabled = bool(((desired.desired_config or {}).get("activation") or {}).get("enabled"))
            current_app.extensions["sqlalchemy"].session.remove()
            adapter = _storage_adapter(connection)
            folders = []
            for binding in sorted(bindings, key=lambda row: row["scope_key"]):
                state = binding["state"]
                provider_folder_id = binding["provider_folder_id"]
                folder_name = binding["scope_key"].rsplit(":", 1)[-1]
                url = None
                if state == "ready" and provider_folder_id:
                    try:
                        provider_object = adapter.get(provider_folder_id)
                        if provider_object is None or provider_object.trashed:
                            state = "missing"
                        elif (
                            provider_object.mime_type != "folder"
                            or provider_object.parent_id != binding["parent_provider_id"]
                            or (connection.drive_id and provider_object.drive_id != connection.drive_id)
                        ):
                            state = "inaccessible"
                        else:
                            adapter.assert_parent_within_root(provider_object.parent_id)
                            folder_name = provider_object.name or folder_name
                            url = f"https://drive.google.com/drive/folders/{provider_folder_id}"
                    except Exception:
                        state = "inaccessible"
                folders.append({
                    "scope_key": binding["scope_key"],
                    "name": folder_name,
                    "state": state,
                    "provider_folder_id": provider_folder_id if url else None,
                    "url": url,
                })
            return {
                "configured": True,
                "managed_by_kitsusync": True,
                "enabled": enabled,
                "folders": folders,
            }

        config = SharedTemplateConfiguration.query.filter_by(production_id=scope.production_id).one_or_none()
        if config is None:
            return {"configured": False, "managed_by_kitsusync": False, "folders": []}
        sequence_id = sequence_name = None
        if "sequence" in config.role_mappings:
            sequence = Entity.query.filter_by(id=entity.parent_id).one_or_none() if entity.parent_id else None
            if sequence is None or sequence.project_id != scope.production_id:
                abort(409, description="Configured Sequence role has no Kitsu Sequence ancestor")
            if task_type.for_entity != "Shot":
                abort(409, description="Sequence template roles apply only to Shot Tasks")
            sequence_id, sequence_name = str(sequence.id), sequence.name
        try:
            from .folders.provisioning import build_folder_plan
            plan = build_folder_plan(
                tree=config.template_tree,
                role_mappings=config.role_mappings,
                production_id=str(scope.production_id),
                shot_id=str(entity.id) if task_type.for_entity == "Shot" else "",
                shot_name=entity.name if task_type.for_entity == "Shot" else "",
                asset_id=str(entity.id) if task_type.for_entity == "Asset" else None,
                asset_name=entity.name if task_type.for_entity == "Asset" else None,
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
            _reject_plugin_owned_storage_configuration(scope.production_id)
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
                    from .integrations.event_producers import current_notification_epoch

                    event_activation_epoch = current_notification_epoch(
                        session, production_id=scope.production_id,
                        selected_connection_id=connection.id,
                    )
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
                            "event_activation_epoch": event_activation_epoch,
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


from .integrations.http import (  # noqa: E402 - route declarations stay after local resource definitions
    ActivationResource,
    DeactivationResource,
    DesiredConfigurationResource,
    DriveAuthorizationResource,
    DriveConnectionsResource,
    DriveConnectionRevokeResource,
    DriveConnectionStatusResource,
    DriveRootsResource,
    GoogleOAuthCallbackResource,
    TemplatesResource,
    SyncSnapshotStartResource,
    SyncSnapshotPageResource,
    SyncSnapshotCompleteResource,
    SyncPreviewResource,
    SyncApplyResource,
    SyncJobResource,
    SyncJobRetryResource,
    SyncJobReconcileResource,
)


routes = [
    ("/integrations/v1/drive-connections/authorizations", DriveAuthorizationResource),
    ("/integrations/v1/drive-connections", DriveConnectionsResource),
    ("/integrations/v1/drive-connections/<connection_id>", DriveConnectionStatusResource),
    ("/integrations/v1/drive-connections/<connection_id>/revoke", DriveConnectionRevokeResource),
    ("/integrations/v1/drive-connections/<connection_id>/roots", DriveRootsResource),
    ("/oauth/google/callback", GoogleOAuthCallbackResource),
    ("/integrations/v1/templates", TemplatesResource),
    ("/integrations/v1/productions/<production_id>/desired-config", DesiredConfigurationResource),
    ("/integrations/v1/productions/<production_id>/activation", ActivationResource),
    ("/integrations/v1/productions/<production_id>/deactivation", DeactivationResource),
    ("/integrations/v1/productions/<production_id>/sync-snapshots", SyncSnapshotStartResource),
    ("/integrations/v1/sync-snapshots/<snapshot_id>/pages/<int:page_index>", SyncSnapshotPageResource),
    ("/integrations/v1/sync-snapshots/<snapshot_id>/complete", SyncSnapshotCompleteResource),
    ("/integrations/v1/productions/<production_id>/sync-previews", SyncPreviewResource),
    ("/integrations/v1/productions/<production_id>/sync-jobs", SyncApplyResource),
    ("/integrations/v1/sync-jobs/<job_id>", SyncJobResource),
    ("/integrations/v1/sync-jobs/<job_id>/retry", SyncJobRetryResource),
    ("/integrations/v1/sync-jobs/<job_id>/reconcile", SyncJobReconcileResource),
    ("/health", Health),
    ("/csrf-token", CsrfToken),
    ("/overview", ProductionOverview),
    ("/mutation-probe", MutationProbe),
    ("/handoffs/<handoff_id>/respond", HandoffResponse),
    ("/publishes/<publish_id>/approvals", PublishApproval),
    ("/publishes/<publish_id>/approvals/<approval_id>/revoke", PublishApprovalRevoke),
    ("/publishes/<publish_id>/handoffs", HandoffCreate),
    ("/productions/<production_id>/output-families", OutputFamilies),
    ("/productions/<production_id>/output-families/<family_id>/activate", OutputFamilyActivation),
    ("/tasks/<task_id>/output-packages", TaskOutputPackages),
    ("/tasks/<task_id>/output-package-options", TaskOutputPackageOptions),
    ("/tasks/<task_id>/output-package-deliveries", TaskPackageDeliveries),
    ("/tasks/<task_id>/storage-objects", TaskStorageObjects),
    ("/output-packages/<package_id>/publication-binding", OutputPackagePublicationBinding),
    ("/output-packages/<package_id>/delivery-targets", OutputPackageDeliveryTargets),
    ("/output-packages/<package_id>/deliveries", OutputPackageDeliveryCreate),
    ("/output-package-deliveries/<delivery_id>/respond", OutputPackageDeliveryResponse),
    ("/output-packages/<package_id>/files", OutputPackageFiles),
    ("/output-package-files/<package_file_id>/download", OutputPackageFileDownload),
    ("/tasks/<task_id>/publishes", TaskPublishes),
    ("/tasks/<task_id>/publish-options", TaskPublishOptions),
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
