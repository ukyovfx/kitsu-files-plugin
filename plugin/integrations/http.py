"""Authenticated Zou HTTP handlers for the KitsuSync contract v1 config slice."""

from uuid import uuid4

from flask import jsonify, request
from flask.views import MethodView

from .configuration import ConfigurationError, get_desired_config, put_desired_config, set_activation
from .service_auth import service_scope


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
