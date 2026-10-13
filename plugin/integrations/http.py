"""Authenticated Zou HTTP handlers for the KitsuSync contract v1 config slice."""

from uuid import uuid4

from flask import current_app, jsonify, request
from flask.views import MethodView

from .configuration import (
    ConfigurationError,
    get_desired_config,
    put_desired_config,
    set_activation,
)
from .providers import (
    ProviderContractError,
    connection_payload,
    root_verification_payload,
    templates_payload,
)
from .service_auth import service_scope
from .v1 import (
    ContractValidationError,
    SnapshotConflict,
    validate_snapshot_complete_request,
    validate_snapshot_page_request,
    validate_snapshot_start,
)


def _sync_response(operation):
    try:
        body, status, etag = operation()
    except SnapshotAccessError as exc:
        return _error(exc.code, exc.status)
    except SnapshotConflict:
        return _error("idempotency_conflict", 409)
    except ContractValidationError:
        return _error("invalid_event", 400)
    except Exception as exc:
        from .sync import SyncOperationError
        if isinstance(exc, SyncOperationError):
            return _error(exc.code, exc.status)
        raise
    response = jsonify(body)
    response.status_code = status
    if etag:
        response.headers["ETag"] = etag
    return response


def _sync_json_body():
    if request.mimetype != "application/json" or request.content_length is None or request.content_length > 64 * 1024:
        raise ContractValidationError("Sync request must be bounded JSON")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ContractValidationError("Sync request body is invalid")
    return body


def _sync_uuid(value, name):
    from uuid import UUID
    try:
        parsed = UUID(str(value))
        if str(parsed) != str(value):
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise ContractValidationError(f"{name} must be a canonical UUID") from None


def _error(code: str, status: int, details: dict | None = None):
    body = {
        "contract_version": "1.0",
        "code": code,
        "request_id": str(uuid4()),
        "retryable": False,
    }
    if details:
        body["details"] = details
    return jsonify(body), status


def _json_body():
    if request.mimetype != "application/json":
        raise ConfigurationError(400, "invalid_event", details={"field": "Content-Type"})
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ConfigurationError(400, "invalid_event", details={"field": "body"})
    return payload


def _respond(operation):
    try:
        result = operation()
    except ConfigurationError as exc:
        return _error(exc.code, exc.status, exc.details)
    body, status, etag = result
    response = jsonify(body)
    response.status_code = status
    if etag:
        response.headers["ETag"] = etag
    return response


def _provider_error(code: str, status: int, field: str | None = None):
    details = {"field": field} if field else None
    return _error(code, status, details)


def _snapshot_respond(operation, *, default_status=200):
    try:
        body, status, etag = operation()
    except SnapshotAccessError as exc:
        return _error(exc.code, exc.status)
    except SnapshotConflict:
        return _error("idempotency_conflict", 409)
    except ContractValidationError as exc:
        message = str(exc)
        code = "stale_config" if "configuration revision" in message or "config is stale" in message or "configuration changed" in message else "invalid_event"
        status = 409 if code == "stale_config" else 400
        return _error(code, status)
    response = jsonify(body)
    response.status_code = status or default_status
    if etag:
        response.headers["ETag"] = etag
    return response


def _snapshot_resource_models():
    from ..models import KitsuSyncHierarchySnapshot
    return KitsuSyncHierarchySnapshot


def _snapshot_uuid(raw, field):
    from uuid import UUID
    try:
        parsed = UUID(str(raw))
        if str(parsed) != str(raw):
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise ContractValidationError(f"{field} must be a canonical UUID") from None


def _snapshot_body():
    if request.mimetype != "application/json" or request.content_length is None or request.content_length > 2 * 1024 * 1024:
        raise ContractValidationError("Snapshot request must be bounded JSON")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ContractValidationError("Snapshot request body is invalid")
    return payload


def _bound_snapshot(session, snapshot_id, identity):
    snapshot_uuid = _snapshot_uuid(snapshot_id, "snapshot_id")
    row = session.get(_snapshot_resource_models(), snapshot_uuid)
    if row is None:
        raise SnapshotAccessError(404, "invalid_event")
    if row.production_id != identity.production_id:
        raise SnapshotAccessError(403, "production_access_denied")
    return row


class SnapshotAccessError(Exception):
    def __init__(self, status, code):
        self.status = status
        self.code = code


class SyncSnapshotStartResource(MethodView):
    @service_scope("vfx.sync.snapshot.write")
    def post(self, production_id, service_identity=None):
        from datetime import datetime, timezone

        from zou.app import db

        from .snapshots import (
            SNAPSHOT_UPLOAD_TTL,
            run_snapshot_idempotent,
            snapshot_payload,
            start_snapshot,
        )

        try:
            body = validate_snapshot_start(_snapshot_body())
            production_uuid = _snapshot_uuid(production_id, "production_id")
            snapshot_uuid = _snapshot_uuid(body["snapshot_id"], "snapshot_id")

            def apply():
                row = start_snapshot(
                    db.session, production_id=production_uuid, snapshot_id=snapshot_uuid,
                    config_revision=body["config_revision"], source_etag=body["source_etag"],
                    page_count=body["page_count"], object_count=body["object_count"],
                    snapshot_sha256=body["snapshot_sha256"],
                    expires_at=datetime.now(timezone.utc) + SNAPSHOT_UPLOAD_TTL,
                )
                return snapshot_payload(db.session, row), 201, None

            return _snapshot_respond(lambda: run_snapshot_idempotent(
                db.session, production_id=production_uuid, actor_id=service_identity.actor_id,
                idempotency_key=request.headers.get("Idempotency-Key"),
                operation="snapshot.start", request_body=body, apply=apply,
            ), default_status=201)
        except ContractValidationError:
            return _error("invalid_event", 400)


class SyncSnapshotPageResource(MethodView):
    @service_scope("vfx.sync.snapshot.write", production_source="none")
    def put(self, snapshot_id, page_index, service_identity=None):
        from zou.app import db

        from .snapshots import (
            put_snapshot_page,
            run_snapshot_idempotent,
            snapshot_payload,
        )

        try:
            body = validate_snapshot_page_request(_snapshot_body(), path_page_index=page_index)
            snapshot_uuid = _snapshot_uuid(snapshot_id, "snapshot_id")
            def apply():
                _bound_snapshot(db.session, snapshot_uuid, service_identity)
                put_snapshot_page(db.session, snapshot_id=snapshot_uuid, page_index=page_index,
                                  objects=body["objects"], page_sha256=body["page_sha256"])
                current = db.session.get(_snapshot_resource_models(), snapshot_uuid)
                return snapshot_payload(db.session, current), 200, None

            return _snapshot_respond(lambda: run_snapshot_idempotent(
                db.session, production_id=service_identity.production_id, actor_id=service_identity.actor_id,
                idempotency_key=request.headers.get("Idempotency-Key"),
                operation=f"snapshot:{snapshot_uuid}:page:{page_index}", request_body=body, apply=apply,
            ))
        except ContractValidationError:
            return _error("invalid_event", 400)


class SyncSnapshotCompleteResource(MethodView):
    @service_scope("vfx.sync.snapshot.write", production_source="none")
    def post(self, snapshot_id, service_identity=None):
        from zou.app import db

        from .snapshots import (
            complete_snapshot,
            run_snapshot_idempotent,
            snapshot_payload,
        )

        try:
            body = validate_snapshot_complete_request(_snapshot_body())
            snapshot_uuid = _snapshot_uuid(snapshot_id, "snapshot_id")
            def apply():
                row = _bound_snapshot(db.session, snapshot_uuid, service_identity)
                current = complete_snapshot(
                    db.session, snapshot_id=snapshot_uuid, config_revision=row.config_revision,
                    page_count=body["page_count"], object_count=body["object_count"],
                    snapshot_sha256=body["snapshot_sha256"],
                )
                return snapshot_payload(db.session, current), 200, None

            return _snapshot_respond(lambda: run_snapshot_idempotent(
                db.session, production_id=service_identity.production_id, actor_id=service_identity.actor_id,
                idempotency_key=request.headers.get("Idempotency-Key"),
                operation=f"snapshot:{snapshot_uuid}:complete", request_body=body, apply=apply,
            ))
        except ContractValidationError:
            return _error("invalid_event", 400)


def _provider_connection(connection_id, production_id):
    from uuid import UUID

    from zou.app import db

    from ..models import StorageConnection

    try:
        connection_uuid = UUID(str(connection_id))
        production_uuid = UUID(str(production_id))
        if str(connection_uuid) != str(connection_id) or str(production_uuid) != str(production_id):
            raise ValueError("noncanonical UUID")
    except (ValueError, TypeError, AttributeError):
        raise ProviderContractError(400, "invalid_event", "connection_id") from None
    row = db.session.query(StorageConnection).filter_by(
        id=connection_uuid, production_id=production_uuid
    ).one_or_none()
    if row is None:
        raise ProviderContractError(404, "connection_not_found")
    if not row.enabled:
        raise ProviderContractError(409, "connection_not_ready")
    return row


class DriveConnectionsResource(MethodView):
    @service_scope("vfx.connections.read", production_source="query")
    def get(self, service_identity=None):
        from uuid import UUID

        from zou.app import db

        from ..models import StorageConnection

        production_id = UUID(request.args["production_id"])
        rows = db.session.query(StorageConnection).filter_by(
            production_id=production_id
        ).order_by(StorageConnection.id).all()
        try:
            connections = [connection_payload(row) for row in rows]
        except ProviderContractError as exc:
            return _provider_error(exc.code, exc.status, exc.field)
        return jsonify({"contract_version": "1.0", "connections": connections})


class DriveRootsResource(MethodView):
    @service_scope("vfx.roots.read", production_source="query")
    def get(self, connection_id, service_identity=None):
        production_id = request.args["production_id"]
        page_token = request.args.get("page_token")
        if page_token is not None and len(page_token) > 2048:
            return _provider_error("invalid_event", 400, "page_token")
        try:
            connection = _provider_connection(connection_id, production_id)
            from ..resources import _storage_adapter

            adapter = _storage_adapter(connection)
            listing = adapter.list_root_candidates(page_token=page_token)
            if not listing.complete or listing.next_page_token is not None or len(listing.items) > 500:
                raise ProviderContractError(503, "provider_unavailable")
            roots = []
            for root in listing.items:
                if root.trashed or root.mime_type not in ("folder", "application/vnd.google-apps.folder"):
                    continue
                if not isinstance(root.name, str) or not root.name:
                    raise ProviderContractError(503, "provider_unavailable")
                item = {"id": root.object_id, "name": root.name, "account_mode": connection.account_mode}
                if connection.account_mode == "shared_drive":
                    if root.drive_id != connection.drive_id:
                        raise ProviderContractError(503, "provider_unavailable")
                    item["drive_id"] = root.drive_id
                roots.append(item)
            return jsonify({"contract_version": "1.0", "roots": roots})
        except ProviderContractError as exc:
            return _provider_error(exc.code, exc.status, exc.field)
        except Exception as exc:
            from ..storage.base import PermissionFailure
            if isinstance(exc, PermissionFailure):
                return _provider_error("drive_permission_denied", 403)
            return _provider_error("provider_unavailable", 503)

    @service_scope("vfx.roots.verify", production_source="body")
    def post(self, connection_id, service_identity=None):
        payload = request.get_json(silent=True)
        if request.mimetype != "application/json" or not isinstance(payload, dict):
            return _provider_error("invalid_event", 400, "body")
        allowed = {"contract_version", "account_mode", "production_id", "root_id", "candidate_url", "drive_id"}
        if set(payload) - allowed or payload.get("contract_version") != "1.0":
            return _provider_error("invalid_event", 400, "body")
        if ("root_id" in payload) == ("candidate_url" in payload):
            return _provider_error("invalid_event", 400, "root_id")
        idempotency_key = request.headers.get("Idempotency-Key", "")
        if not 16 <= len(idempotency_key) <= 128:
            return _provider_error("invalid_event", 400, "Idempotency-Key")
        if request.args.get("production_id") not in (None, payload.get("production_id")):
            return _provider_error("production_access_denied", 403)
        try:
            root_id = payload.get("root_id")
            if "candidate_url" in payload:
                from ..storage.drive_url import parse_google_drive_folder_url
                root_id = parse_google_drive_folder_url(payload["candidate_url"])
            if not isinstance(root_id, str) or not root_id or len(root_id) > 255:
                raise ProviderContractError(400, "invalid_event", "root_id")
            connection = _provider_connection(connection_id, payload.get("production_id"))
            if payload.get("account_mode") != connection.account_mode:
                raise ProviderContractError(403, "root_unverified")
            requested_drive_id = payload.get("drive_id")
            if connection.account_mode == "shared_drive" and requested_drive_id != connection.drive_id:
                raise ProviderContractError(403, "root_unverified")
            if connection.account_mode == "my_drive" and requested_drive_id not in (None, ""):
                raise ProviderContractError(400, "invalid_event", "drive_id")
            from ..resources import _storage_adapter
            from ..storage.base import PermissionFailure

            if connection.account_mode == "my_drive" and current_app.config.get("VFX_FILES_STORAGE_MODE") == "fake":
                raise ProviderContractError(403, "root_unverified")
            adapter = _storage_adapter(connection)
            try:
                capabilities = adapter.verify_root_candidate(root_id)
            except PermissionFailure:
                raise ProviderContractError(403, "drive_permission_denied") from None
            if (
                not capabilities.root_visible or capabilities.root_id != root_id
                or (connection.account_mode == "shared_drive" and capabilities.drive_id != connection.drive_id)
            ):
                raise ProviderContractError(403, "drive_permission_denied")
            result = root_verification_payload(
                connection_id=connection.id,
                account_mode=connection.account_mode,
                root_id=root_id,
                drive_id=capabilities.drive_id,
                can_add_children=capabilities.can_add_children,
                owner_subject=connection.owner_subject if connection.account_mode == "my_drive" else None,
            )
            return jsonify(result)
        except ProviderContractError as exc:
            return _provider_error(exc.code, exc.status, exc.field)
        except ValueError:
            return _provider_error("invalid_event", 400, "candidate_url")
        except Exception:
            return _provider_error("provider_unavailable", 503)


class TemplatesResource(MethodView):
    @service_scope("vfx.templates.read", production_source="none")
    def get(self, service_identity=None):
        try:
            return jsonify(templates_payload(request.args.get("scope")))
        except ProviderContractError as exc:
            return _provider_error(exc.code, exc.status, exc.field)


class DesiredConfigurationResource(MethodView):
    @service_scope("vfx.config.read")
    def get(self, production_id, service_identity=None):
        from uuid import UUID

        from zou.app import db

        try:
            payload, etag = get_desired_config(db.session, UUID(production_id))
        except ConfigurationError as exc:
            return _error(exc.code, exc.status, exc.details)
        response = jsonify(payload)
        response.headers["ETag"] = etag
        return response

    @service_scope("vfx.config.write")
    def put(self, production_id, service_identity=None):
        from uuid import UUID

        from zou.app import db

        def apply():
            return put_desired_config(
                db.session,
                production_id=UUID(production_id),
                actor_id=service_identity.actor_id,
                payload=_json_body(),
                idempotency_key=request.headers.get("Idempotency-Key"),
                if_match=request.headers.get("If-Match"),
                if_none_match=request.headers.get("If-None-Match"),
            )

        return _respond(apply)


class ActivationResource(MethodView):
    @service_scope("vfx.activation.write")
    def post(self, production_id, service_identity=None):
        from uuid import UUID

        from zou.app import db

        def activate():
            body, status, etag = set_activation(
                db.session,
                production_id=UUID(production_id),
                actor_id=service_identity.actor_id,
                enabled=True,
                payload=_json_body(),
                idempotency_key=request.headers.get("Idempotency-Key"),
                if_match=request.headers.get("If-Match"),
            )
            return body, status, etag

        return _respond(activate)


class DeactivationResource(MethodView):
    @service_scope("vfx.activation.write")
    def post(self, production_id, service_identity=None):
        from uuid import UUID

        from zou.app import db

        def deactivate():
            body, status, etag = set_activation(
                db.session,
                production_id=UUID(production_id),
                actor_id=service_identity.actor_id,
                enabled=False,
                payload=_json_body(),
                idempotency_key=request.headers.get("Idempotency-Key"),
                if_match=request.headers.get("If-Match"),
            )
            return body, status, etag

        return _respond(deactivate)


class SyncPreviewResource(MethodView):
    @service_scope("vfx.sync.preview")
    def post(self, production_id, service_identity=None):
        from zou.app import db

        from .sync import create_sync_preview
        try:
            body = _sync_json_body()
            production_uuid = _sync_uuid(production_id, "production_id")
        except ContractValidationError:
            return _error("invalid_event", 400)
        return _sync_response(lambda: create_sync_preview(
            db.session, production_id=production_uuid, actor_id=service_identity.actor_id,
            payload=body, idempotency_key=request.headers.get("Idempotency-Key"),
        ))


class SyncApplyResource(MethodView):
    @service_scope("vfx.sync.apply")
    def post(self, production_id, service_identity=None):
        from zou.app import db

        from .sync import apply_sync_preview
        try:
            body = _sync_json_body()
            production_uuid = _sync_uuid(production_id, "production_id")
        except ContractValidationError:
            return _error("invalid_event", 400)
        return _sync_response(lambda: apply_sync_preview(
            db.session, production_id=production_uuid, actor_id=service_identity.actor_id,
            payload=body, idempotency_key=request.headers.get("Idempotency-Key"),
        ))


class SyncJobResource(MethodView):
    @service_scope("vfx.sync.read", production_source="none")
    def get(self, job_id, service_identity=None):
        from zou.app import db

        from .sync import get_sync_job
        try:
            parsed = _sync_uuid(job_id, "job_id")
        except ContractValidationError:
            return _error("invalid_event", 400)
        return _sync_response(lambda: (get_sync_job(
            db.session, job_id=parsed, production_id=service_identity.production_id,
            actor_id=service_identity.actor_id,
        ), 200, None))


class SyncJobRetryResource(MethodView):
    @service_scope("vfx.sync.retry", production_source="none")
    def post(self, job_id, service_identity=None):
        from zou.app import db

        from .sync import enqueue_sync_job_command, validate_operator_command
        try:
            parsed, body = _sync_uuid(job_id, "job_id"), _sync_json_body()
            validate_operator_command(body)
        except ContractValidationError:
            return _error("invalid_event", 400)
        return _sync_response(lambda: enqueue_sync_job_command(
            db.session, job_id=parsed, production_id=service_identity.production_id,
            actor_id=service_identity.actor_id, command=body,
            idempotency_key=request.headers.get("Idempotency-Key"), operation="retry",
        ))


class SyncJobReconcileResource(MethodView):
    @service_scope("vfx.sync.reconcile", production_source="none")
    def post(self, job_id, service_identity=None):
        from zou.app import db

        from .sync import enqueue_sync_job_command, validate_operator_command
        try:
            parsed, body = _sync_uuid(job_id, "job_id"), _sync_json_body()
            validate_operator_command(body)
        except ContractValidationError:
            return _error("invalid_event", 400)
        return _sync_response(lambda: enqueue_sync_job_command(
            db.session, job_id=parsed, production_id=service_identity.production_id,
            actor_id=service_identity.actor_id, command=body,
            idempotency_key=request.headers.get("Idempotency-Key"), operation="reconcile",
        ))
