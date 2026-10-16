"""Authenticated Zou HTTP handlers for the KitsuSync contract v1 config slice."""

from uuid import UUID, uuid4

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


def _integration_session_factory():
    """Create independent sessions for transactional management operations.

    Zou may already have opened its request-scoped ``db.session`` while
    authenticating or serving an earlier request in the same app context.
    Snapshot/config idempotency helpers own their transaction boundary, so
    passing that scoped session can fail with "transaction already begun".
    """
    from flask import current_app
    from sqlalchemy.orm import sessionmaker

    database = current_app.extensions["sqlalchemy"]
    return sessionmaker(bind=database.engine, expire_on_commit=False)


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

        from .snapshots import (
            SNAPSHOT_UPLOAD_TTL,
            run_snapshot_idempotent,
            snapshot_payload,
            start_snapshot,
        )

        try:
            sessions = _integration_session_factory()
            body = validate_snapshot_start(_snapshot_body())
            production_uuid = _snapshot_uuid(production_id, "production_id")
            snapshot_uuid = _snapshot_uuid(body["snapshot_id"], "snapshot_id")

            with sessions() as session:
                def apply():
                    row = start_snapshot(
                        session, production_id=production_uuid, snapshot_id=snapshot_uuid,
                        config_revision=body["config_revision"], source_etag=body["source_etag"],
                        page_count=body["page_count"], object_count=body["object_count"],
                        snapshot_sha256=body["snapshot_sha256"],
                        expires_at=datetime.now(timezone.utc) + SNAPSHOT_UPLOAD_TTL,
                    )
                    return snapshot_payload(session, row), 201, None

                return _snapshot_respond(lambda: run_snapshot_idempotent(
                    session, production_id=production_uuid, actor_id=service_identity.actor_id,
                    idempotency_key=request.headers.get("Idempotency-Key"),
                    operation="snapshot.start", request_body=body, apply=apply,
                ), default_status=201)
        except ContractValidationError:
            return _error("invalid_event", 400)


class SyncSnapshotPageResource(MethodView):
    @service_scope("vfx.sync.snapshot.write", production_source="none")
    def put(self, snapshot_id, page_index, service_identity=None):
        from .snapshots import (
            put_snapshot_page,
            run_snapshot_idempotent,
            snapshot_payload,
        )

        try:
            sessions = _integration_session_factory()
            body = validate_snapshot_page_request(_snapshot_body(), path_page_index=page_index)
            snapshot_uuid = _snapshot_uuid(snapshot_id, "snapshot_id")
            with sessions() as session:
                def apply():
                    _bound_snapshot(session, snapshot_uuid, service_identity)
                    put_snapshot_page(session, snapshot_id=snapshot_uuid, page_index=page_index,
                                      objects=body["objects"], page_sha256=body["page_sha256"])
                    current = session.get(_snapshot_resource_models(), snapshot_uuid)
                    return snapshot_payload(session, current), 200, None

                return _snapshot_respond(lambda: run_snapshot_idempotent(
                    session, production_id=service_identity.production_id, actor_id=service_identity.actor_id,
                    idempotency_key=request.headers.get("Idempotency-Key"),
                    operation=f"snapshot:{snapshot_uuid}:page:{page_index}", request_body=body, apply=apply,
                ))
        except ContractValidationError:
            return _error("invalid_event", 400)


class SyncSnapshotCompleteResource(MethodView):
    @service_scope("vfx.sync.snapshot.write", production_source="none")
    def post(self, snapshot_id, service_identity=None):
        from .snapshots import (
            complete_snapshot,
            run_snapshot_idempotent,
            snapshot_payload,
        )

        try:
            sessions = _integration_session_factory()
            body = validate_snapshot_complete_request(_snapshot_body())
            snapshot_uuid = _snapshot_uuid(snapshot_id, "snapshot_id")
            with sessions() as session:
                def apply():
                    row = _bound_snapshot(session, snapshot_uuid, service_identity)
                    current = complete_snapshot(
                        session, snapshot_id=snapshot_uuid, config_revision=row.config_revision,
                        page_count=body["page_count"], object_count=body["object_count"],
                        snapshot_sha256=body["snapshot_sha256"],
                    )
                    return snapshot_payload(session, current), 200, None

                return _snapshot_respond(lambda: run_snapshot_idempotent(
                    session, production_id=service_identity.production_id, actor_id=service_identity.actor_id,
                    idempotency_key=request.headers.get("Idempotency-Key"),
                    operation=f"snapshot:{snapshot_uuid}:complete", request_body=body, apply=apply,
                ))
        except ContractValidationError:
            return _error("invalid_event", 400)


def _provider_connection(connection_id, production_id, *, session=None):
    from uuid import UUID

    from zou.app import db

    from ..models import DriveCredential, StorageConnection

    try:
        connection_uuid = UUID(str(connection_id))
        production_uuid = UUID(str(production_id))
        if str(connection_uuid) != str(connection_id) or str(production_uuid) != str(production_id):
            raise ValueError("noncanonical UUID")
    except (ValueError, TypeError, AttributeError):
        raise ProviderContractError(400, "invalid_event", "connection_id") from None
    session = session or db.session
    row = session.query(StorageConnection).filter_by(
        id=connection_uuid, production_id=production_uuid
    ).one_or_none()
    if row is None:
        raise ProviderContractError(404, "connection_not_found")
    credential = session.get(DriveCredential, connection_uuid)
    if not row.enabled and (credential is None or credential.state != "active"):
        raise ProviderContractError(409, "connection_not_ready")
    return row


class DriveConnectionsResource(MethodView):
    @service_scope("vfx.connections.read", production_source="query")
    def get(self, service_identity=None):
        from uuid import UUID

        from zou.app import db

        from ..models import DriveCredential, StorageConnection
        from .oauth import connection_status

        production_id = UUID(request.args["production_id"])
        rows = db.session.query(StorageConnection).filter_by(
            production_id=production_id
        ).order_by(StorageConnection.id).all()
        try:
            connections = [connection_status(row, db.session.get(DriveCredential, row.id)) for row in rows]
        except ProviderContractError as exc:
            return _provider_error(exc.code, exc.status, exc.field)
        return jsonify({"contract_version": "1.0", "connections": connections})


class DriveAuthorizationResource(MethodView):
    @service_scope("vfx.connections.authorize", production_source="body")
    def post(self, service_identity=None):
        from flask import current_app
        from zou.app import db

        from .oauth import OAuthOperationError, start_authorization

        try:
            body = _sync_json_body()
            response, status = start_authorization(
                db.session, payload=body, actor_id=service_identity.actor_id,
                idempotency_key=request.headers.get("Idempotency-Key"),
                settings=current_app.config, extensions=current_app.extensions,
            )
            return jsonify(response), status
        except OAuthOperationError as exc:
            return _error(exc.code, exc.status)


class DriveConnectionStatusResource(MethodView):
    @service_scope("vfx.connections.read", production_source="query")
    def get(self, connection_id, service_identity=None):
        from uuid import UUID
        from zou.app import db
        from ..models import DriveCredential, StorageConnection
        from .oauth import connection_status

        try:
            parsed = UUID(str(connection_id))
            if str(parsed) != str(connection_id):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            return _provider_error("invalid_event", 400, "connection_id")
        production_id = service_identity.production_id
        connection = db.session.query(StorageConnection).filter_by(
            id=parsed, production_id=production_id,
        ).one_or_none()
        if connection is None:
            return _provider_error("connection_not_found", 404)
        credential = db.session.get(DriveCredential, parsed)
        return jsonify(connection_status(connection, credential))


class DriveConnectionRevokeResource(MethodView):
    @service_scope("vfx.connections.revoke", production_source="none")
    def post(self, connection_id, service_identity=None):
        from uuid import UUID, uuid4
        from sqlalchemy import select
        from sqlalchemy.orm import sessionmaker
        from flask import current_app
        from ..models import DriveCredential, Job, KitsuSyncDesiredConfiguration, StorageConnection
        from .oauth import OAuthOperationError, connection_status
        from .sync import validate_operator_command

        try:
            body = _sync_json_body()
            validate_operator_command(body)
            parsed = UUID(str(connection_id))
            if str(parsed) != str(connection_id):
                raise ValueError
            key = request.headers.get("Idempotency-Key", "")
            if not 16 <= len(key) <= 128:
                raise OAuthOperationError(400, "invalid_event")
        except (ValueError, TypeError, AttributeError, ContractValidationError):
            return _provider_error("invalid_event", 400)
        except OAuthOperationError as exc:
            return _provider_error(exc.code, exc.status)
        production_id = service_identity.production_id
        try:
            database = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=database.engine, expire_on_commit=False)
            with sessions.begin() as session:
                connection = session.scalar(select(StorageConnection).where(
                    StorageConnection.id == parsed,
                    StorageConnection.production_id == production_id,
                ).with_for_update())
                credential = session.scalar(select(DriveCredential).where(
                    DriveCredential.connection_id == parsed,
                    DriveCredential.production_id == production_id,
                ).with_for_update())
                if connection is None:
                    raise OAuthOperationError(404, "connection_not_found")
                if credential is None or credential.state == "revoked":
                    return jsonify(connection_status(connection, credential)), 202
                job_key = f"drive-revoke:{production_id}:{key}"
                existing = session.scalar(select(Job).where(Job.idempotency_key == job_key).with_for_update())
                payload = {
                    "job_type": "revoke_drive_credential", "production_id": str(production_id),
                    "actor_id": str(service_identity.actor_id), "connection_id": str(parsed),
                }
                if existing is not None:
                    if existing.payload != payload:
                        raise OAuthOperationError(409, "idempotency_conflict")
                    return jsonify(connection_status(connection, credential)), 202
                connection.enabled = False
                credential.state = "revoking"
                from .storage_authority import fence_plugin_authority_after_credential_failure
                fence_plugin_authority_after_credential_failure(session, production_id, parsed)
                desired = session.get(KitsuSyncDesiredConfiguration, production_id)
                if desired is not None:
                    desired_body = dict(desired.desired_config)
                    activation = dict(desired_body.get("activation", {}))
                    activation.update({"enabled": False, "activated_at": None,
                                       "notification_epoch": desired.notification_epoch + 1})
                    desired_body["activation"] = activation
                    desired.notification_epoch += 1
                    desired.config_revision += 1
                    desired.desired_config = desired_body
                session.add(Job(
                    id=uuid4(), job_type="revoke_drive_credential", idempotency_key=job_key,
                    payload=payload, state="queued",
                ))
            return jsonify(connection_status(connection, credential)), 202
        except OAuthOperationError as exc:
            return _provider_error(exc.code, exc.status)


class GoogleOAuthCallbackResource(MethodView):
    """Fixed public callback; only the single-use state authorizes completion."""
    def get(self):
        from flask import current_app, redirect, make_response
        from sqlalchemy import select
        from sqlalchemy.orm import sessionmaker
        from zou.app import db
        from ..models import DriveAuthorizationIntent
        from .oauth import OAuthOperationError, _oauth_settings, complete_authorization, validate_google_callback_query
        from ..storage.worker_factory import RequestsDriveTransport
        import hashlib

        state = request.args.get("state", "")
        code = None
        oauth_error = None
        authorization_id = None
        try:
            state, code, oauth_error = validate_google_callback_query(request.args)
            if isinstance(state, str) and 32 <= len(state) <= 256:
                row = db.session.scalar(select(DriveAuthorizationIntent).where(
                    DriveAuthorizationIntent.state_sha256 == hashlib.sha256(state.encode("utf-8")).hexdigest(),
                ))
                authorization_id = str(row.id) if row is not None else None
            if authorization_id is None:
                raise OAuthOperationError(400, "authorization_invalid")
            database = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=database.engine, expire_on_commit=False)
            transport = current_app.extensions.get("vfx_files_oauth_transport") or RequestsDriveTransport(timeout=(10, 30))
            location = complete_authorization(
                sessions, state=state, code=code,
                oauth_error=oauth_error, settings=current_app.config,
                extensions=current_app.extensions, transport=transport,
            )
        except OAuthOperationError as exc:
            try:
                _client, _secret, _callback, return_uri = _oauth_settings(current_app.config)
                from urllib.parse import urlencode
                location = return_uri + ("&" if "?" in return_uri else "?") + urlencode({
                    "authorization_id": authorization_id or "", "status": "failed", "error": exc.code,
                })
            except OAuthOperationError:
                return _error(exc.code, exc.status)
        response = make_response(redirect(location, code=303))
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


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

            adapter = _storage_adapter(connection, allow_unconfigured=True)
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
        from sqlalchemy.orm import sessionmaker
        from .snapshots import run_snapshot_idempotent
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
            # Zou may have already opened its request-scoped SQLAlchemy
            # transaction while resolving request identity. Keep the root
            # verification mutation and idempotency record in an independent
            # transaction so this endpoint remains atomic and composable.
            database = current_app.extensions["sqlalchemy"]
            sessions = sessionmaker(bind=database.engine, expire_on_commit=False)
            def verify_root():
                from ..models import DriveCredential, StorageConnection

                connection = _provider_connection(
                    connection_id, payload.get("production_id"), session=session,
                )
                if payload.get("account_mode") != connection.account_mode:
                    raise ProviderContractError(403, "root_unverified")
                requested_drive_id = payload.get("drive_id")
                if connection.account_mode == "shared_drive" and (
                    not isinstance(requested_drive_id, str) or not requested_drive_id
                    or (connection.drive_id and requested_drive_id != connection.drive_id)
                ):
                    raise ProviderContractError(403, "root_unverified")
                if connection.account_mode == "my_drive" and requested_drive_id not in (None, ""):
                    raise ProviderContractError(400, "invalid_event", "drive_id")
                from ..resources import _storage_adapter
                from ..storage.base import PermissionFailure

                if connection.account_mode == "my_drive" and current_app.config.get("VFX_FILES_STORAGE_MODE") == "fake":
                    raise ProviderContractError(403, "root_unverified")
                adapter_connection = connection
                if connection.account_mode == "shared_drive" and not connection.drive_id:
                    from types import SimpleNamespace
                    adapter_connection = SimpleNamespace(**{
                        column.name: getattr(connection, column.name)
                        for column in connection.__table__.columns
                    })
                    adapter_connection.drive_id = requested_drive_id
                adapter = _storage_adapter(adapter_connection, allow_unconfigured=True)
                try:
                    capabilities = adapter.verify_root_candidate(root_id)
                except PermissionFailure:
                    raise ProviderContractError(403, "drive_permission_denied") from None
                if (
                    not capabilities.root_visible or capabilities.root_id != root_id
                    or (connection.account_mode == "shared_drive" and capabilities.drive_id != requested_drive_id)
                ):
                    raise ProviderContractError(403, "drive_permission_denied")
                if connection.enabled and connection.allowed_root_id not in (root_id, f"__pending__:{connection.id}"):
                    raise ProviderContractError(409, "root_unverified")
                another_enabled = session.query(StorageConnection).filter(
                    StorageConnection.production_id == connection.production_id,
                    StorageConnection.enabled.is_(True),
                    StorageConnection.id != connection.id,
                ).first()
                if another_enabled is not None:
                    raise ProviderContractError(409, "connection_not_ready")
                credential = session.get(DriveCredential, connection.id)
                if current_app.config.get("VFX_FILES_STORAGE_MODE") == "google_drive" and (credential is None or credential.state != "active"):
                    raise ProviderContractError(403, "root_unverified")
                if connection.account_mode == "my_drive" and credential is not None and credential.account_subject != connection.owner_subject:
                    raise ProviderContractError(403, "root_unverified")
                if connection.account_mode == "shared_drive" and credential is not None and credential.drive_id not in (None, requested_drive_id):
                    raise ProviderContractError(403, "root_unverified")
                connection.allowed_root_id = root_id
                connection.drive_id = capabilities.drive_id
                connection.enabled = True
                if credential is not None and connection.account_mode == "shared_drive":
                    credential.drive_id = capabilities.drive_id
                session.flush()
                result = root_verification_payload(
                    connection_id=connection.id,
                    account_mode=connection.account_mode,
                    root_id=root_id,
                    drive_id=capabilities.drive_id,
                    can_add_children=capabilities.can_add_children,
                    owner_subject=connection.owner_subject if connection.account_mode == "my_drive" else None,
                )
                return result, 200, None

            production_id = UUID(payload["production_id"])
            connection_uuid = UUID(str(connection_id))
            with sessions() as session:
                response_body, status, _etag = run_snapshot_idempotent(
                    session, production_id=production_id, actor_id=service_identity.actor_id,
                    idempotency_key=idempotency_key, operation=f"root.verify:{connection_uuid}",
                    request_body={"connection_id": str(connection_uuid), **payload}, apply=verify_root,
                )
            return jsonify(response_body), status
        except SnapshotConflict:
            return _provider_error("idempotency_conflict", 409)
        except ProviderContractError as exc:
            return _provider_error(exc.code, exc.status, exc.field)
        except ContractValidationError:
            return _provider_error("invalid_event", 400)
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

        with _integration_session_factory()() as session:
            def apply():
                return put_desired_config(
                    session,
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

        with _integration_session_factory()() as session:
            def activate():
                return set_activation(
                    session,
                    production_id=UUID(production_id),
                    actor_id=service_identity.actor_id,
                    enabled=True,
                    payload=_json_body(),
                    idempotency_key=request.headers.get("Idempotency-Key"),
                    if_match=request.headers.get("If-Match"),
                )

            return _respond(activate)


class DeactivationResource(MethodView):
    @service_scope("vfx.activation.write")
    def post(self, production_id, service_identity=None):
        from uuid import UUID

        with _integration_session_factory()() as session:
            def deactivate():
                return set_activation(
                    session,
                    production_id=UUID(production_id),
                    actor_id=service_identity.actor_id,
                    enabled=False,
                    payload=_json_body(),
                    idempotency_key=request.headers.get("Idempotency-Key"),
                    if_match=request.headers.get("If-Match"),
                )

            return _respond(deactivate)


class ProductionDriveAuthorityStatusResource(MethodView):
    @service_scope("vfx.config.read")
    def get(self, production_id, service_identity=None):
        from .storage_authority import production_drive_authority_status

        try:
            parsed = UUID(str(production_id))
            if str(parsed) != str(production_id):
                return _error("invalid_event", 400)
        except (TypeError, ValueError, AttributeError):
            return _error("invalid_event", 400)
        with _integration_session_factory()() as session:
            return jsonify(production_drive_authority_status(session, parsed))


class SyncPreviewResource(MethodView):
    @service_scope("vfx.sync.preview")
    def post(self, production_id, service_identity=None):
        from .sync import create_sync_preview
        try:
            body = _sync_json_body()
            production_uuid = _sync_uuid(production_id, "production_id")
        except ContractValidationError:
            return _error("invalid_event", 400)
        with _integration_session_factory()() as session:
            return _sync_response(lambda: create_sync_preview(
                session, production_id=production_uuid, actor_id=service_identity.actor_id,
                payload=body, idempotency_key=request.headers.get("Idempotency-Key"),
            ))


class SyncApplyResource(MethodView):
    @service_scope("vfx.sync.apply")
    def post(self, production_id, service_identity=None):
        from .sync import apply_sync_preview
        try:
            body = _sync_json_body()
            production_uuid = _sync_uuid(production_id, "production_id")
        except ContractValidationError:
            return _error("invalid_event", 400)
        with _integration_session_factory()() as session:
            return _sync_response(lambda: apply_sync_preview(
                session, production_id=production_uuid, actor_id=service_identity.actor_id,
                payload=body, idempotency_key=request.headers.get("Idempotency-Key"),
            ))


class SyncJobResource(MethodView):
    @service_scope("vfx.sync.read", production_source="none")
    def get(self, job_id, service_identity=None):
        from .sync import get_sync_job
        try:
            parsed = _sync_uuid(job_id, "job_id")
        except ContractValidationError:
            return _error("invalid_event", 400)
        with _integration_session_factory()() as session:
            return _sync_response(lambda: (get_sync_job(
                session, job_id=parsed, production_id=service_identity.production_id,
                actor_id=service_identity.actor_id,
            ), 200, None))


class SyncJobRetryResource(MethodView):
    @service_scope("vfx.sync.retry", production_source="none")
    def post(self, job_id, service_identity=None):
        from .sync import enqueue_sync_job_command, validate_operator_command
        try:
            parsed, body = _sync_uuid(job_id, "job_id"), _sync_json_body()
            validate_operator_command(body)
        except ContractValidationError:
            return _error("invalid_event", 400)
        with _integration_session_factory()() as session:
            return _sync_response(lambda: enqueue_sync_job_command(
                session, job_id=parsed, production_id=service_identity.production_id,
                actor_id=service_identity.actor_id, command=body,
                idempotency_key=request.headers.get("Idempotency-Key"), operation="retry",
            ))


class SyncJobReconcileResource(MethodView):
    @service_scope("vfx.sync.reconcile", production_source="none")
    def post(self, job_id, service_identity=None):
        from .sync import enqueue_sync_job_command, validate_operator_command
        try:
            parsed, body = _sync_uuid(job_id, "job_id"), _sync_json_body()
            validate_operator_command(body)
        except ContractValidationError:
            return _error("invalid_event", 400)
        with _integration_session_factory()() as session:
            return _sync_response(lambda: enqueue_sync_job_command(
                session, job_id=parsed, production_id=service_identity.production_id,
                actor_id=service_identity.actor_id, command=body,
                idempotency_key=request.headers.get("Idempotency-Key"), operation="reconcile",
            ))
