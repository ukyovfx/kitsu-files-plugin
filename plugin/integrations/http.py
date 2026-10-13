"""Authenticated Zou HTTP handlers for the KitsuSync contract v1 config slice."""

from uuid import uuid4

from flask import current_app, jsonify, request
from flask.views import MethodView

from .configuration import ConfigurationError, get_desired_config, put_desired_config, set_activation
from .service_auth import service_scope
from .providers import ProviderContractError, connection_payload, root_verification_payload, templates_payload


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
        from zou.app import db
        from uuid import UUID

        try:
            payload, etag = get_desired_config(db.session, UUID(production_id))
        except ConfigurationError as exc:
            return _error(exc.code, exc.status, exc.details)
        response = jsonify(payload)
        response.headers["ETag"] = etag
        return response

    @service_scope("vfx.config.write")
    def put(self, production_id, service_identity=None):
        from zou.app import db
        from uuid import UUID

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
        from zou.app import db
        from uuid import UUID

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
        from zou.app import db
        from uuid import UUID

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
