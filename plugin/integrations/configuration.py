"""Transactional KitsuSync desired-config mirror and activation lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select, text

from .v1 import canonical_json


class ConfigurationError(Exception):
    def __init__(self, status: int, code: str, *, details: dict | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.details = details


def _fail(status: int, code: str, **details):
    raise ConfigurationError(status, code, details=details or None)


def _uuid(value, field):
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        _fail(400, "invalid_event", field=field)
    if str(parsed) != value:
        _fail(400, "invalid_event", field=field)
    return parsed


def _is_int(value, minimum=1):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def validate_desired_config(payload: object) -> dict:
    """Validate the exact contract update shape without accepting extensions."""
    if not isinstance(payload, dict) or set(payload) != {
        "contract_version", "connection_id", "drive", "template_selection", "sync_policy"
    }:
        _fail(400, "invalid_event", field="desired_config")
    if payload["contract_version"] != "1.0":
        _fail(400, "unsupported_contract_version")
    _uuid(payload["connection_id"], "connection_id")

    drive = payload["drive"]
    if not isinstance(drive, dict) or set(drive) - {"account_mode", "drive_id", "owner_subject", "root_id", "root_state"}:
        _fail(400, "invalid_event", field="drive")
    if not {"account_mode", "root_id", "root_state"} <= set(drive):
        _fail(400, "invalid_event", field="drive")
    if drive["account_mode"] not in ("my_drive", "shared_drive"):
        _fail(400, "invalid_event", field="drive.account_mode")
    if not isinstance(drive["root_id"], str) or not drive["root_id"] or len(drive["root_id"]) > 255:
        _fail(400, "invalid_event", field="drive.root_id")
    if drive["root_state"] != "verified":
        _fail(409, "root_unverified")
    if drive["account_mode"] == "shared_drive":
        if not isinstance(drive.get("drive_id"), str) or not drive["drive_id"]:
            _fail(400, "invalid_event", field="drive.drive_id")
        if drive.get("owner_subject") is not None:
            _fail(400, "invalid_event", field="drive.owner_subject")
    else:
        if not isinstance(drive.get("owner_subject"), str) or not drive["owner_subject"] or len(drive["owner_subject"]) > 255:
            _fail(400, "invalid_event", field="drive.owner_subject")
        if drive.get("drive_id") is not None and not isinstance(drive["drive_id"], str):
            _fail(400, "invalid_event", field="drive.drive_id")

    templates = payload["template_selection"]
    selections = templates.get("selections") if isinstance(templates, dict) and set(templates) == {"selections"} else None
    if not isinstance(selections, list) or not selections:
        _fail(400, "invalid_event", field="template_selection.selections")
    for item in selections:
        if not isinstance(item, dict) or set(item) != {"scope", "template_id", "revision", "overrides"}:
            _fail(400, "invalid_event", field="template_selection.selections")
        if not isinstance(item["scope"], str) or item["scope"] not in {"production", "asset", "sequence", "shot", "task"}:
            _fail(400, "invalid_event", field="template_selection.scope")
        if not isinstance(item["template_id"], str) or not item["template_id"] or len(item["template_id"]) > 255:
            _fail(400, "invalid_event", field="template_selection.template_id")
        if not _is_int(item["revision"]):
            _fail(400, "invalid_event", field="template_selection.revision")
        overrides = item["overrides"]
        if not isinstance(overrides, dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 240 for k, v in overrides.items()):
            _fail(400, "invalid_event", field="template_selection.overrides")
    try:
        from .template_materialization import materialize_template_selection
        materialize_template_selection(selections)
    except (TypeError, ValueError):
        _fail(400, "invalid_event", field="template_selection")

    policy = payload["sync_policy"]
    if not isinstance(policy, dict) or set(policy) != {"mode", "scope", "notify_new_deliveries"}:
        _fail(400, "invalid_event", field="sync_policy")
    if not isinstance(policy["mode"], str) or policy["mode"] not in {"new_production", "adopt_existing", "future_only"}:
        _fail(400, "invalid_event", field="sync_policy.mode")
    if not isinstance(policy["notify_new_deliveries"], bool):
        _fail(400, "invalid_event", field="sync_policy.notify_new_deliveries")
    scope = policy["scope"]
    required_scope = {"episodes", "sequences", "shots", "assets", "tasks"}
    optional_scope = {"task_type_ids", "include_ids", "exclude_ids"}
    if not isinstance(scope, dict) or set(scope) - required_scope - optional_scope or not required_scope <= set(scope):
        _fail(400, "invalid_event", field="sync_policy.scope")
    if any(not isinstance(scope[name], bool) for name in required_scope):
        _fail(400, "invalid_event", field="sync_policy.scope")
    for name in optional_scope:
        if name in scope:
            values = scope[name]
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values) or len(values) != len(set(values)):
                _fail(400, "invalid_event", field=f"sync_policy.scope.{name}")
            for value in values:
                _uuid(value, f"sync_policy.scope.{name}")
    try:
        from .template_materialization import validate_sync_template_scope
        validate_sync_template_scope(selections, scope, allow_empty=True)
    except (TypeError, ValueError):
        _fail(400, "invalid_event", field="sync_policy.scope")
    return payload


def _configuration_payload(row) -> dict:
    result = dict(row.desired_config)
    result.update({
        "production_id": str(row.production_id),
        "config_revision": row.config_revision,
        "activation": {
            "enabled": bool(row.desired_config.get("activation", {}).get("enabled", False)),
            "activated_at": row.desired_config.get("activation", {}).get("activated_at"),
            "notification_epoch": row.notification_epoch,
        },
    })
    return result


def _request_hash(operation: str, payload: dict, if_match: str | None, if_none_match: str | None) -> str:
    raw = canonical_json({
        "operation": operation,
        "payload": payload,
        "if_match": if_match,
        "if_none_match": if_none_match,
    })
    return hashlib.sha256(raw).hexdigest()


def _response_receipt(session, production_id, actor_id, idempotency_key, operation, request_sha256):
    from ..models import KitsuSyncManagementIdempotency

    prior = session.scalar(select(KitsuSyncManagementIdempotency).where(
        KitsuSyncManagementIdempotency.production_id == production_id,
        KitsuSyncManagementIdempotency.idempotency_key == idempotency_key,
    ))
    if prior is None:
        return None
    if prior.actor_id != actor_id or prior.operation != operation or prior.request_sha256 != request_sha256:
        _fail(409, "idempotency_conflict")
    return prior


def get_operation_receipt(session, *, production_id, actor_id, idempotency_key, operation, payload, if_match):
    """Return a matching durable result before repeating external verification."""
    key = _valid_idempotency_key(idempotency_key)
    request_hash = _request_hash(operation, payload, if_match, None)
    return _response_receipt(session, production_id, actor_id, key, operation, request_hash)


def _lock_production(session, production_id):
    # The transaction-scoped lock also serializes first creation, where there
    # is not yet a desired-config row on which SELECT FOR UPDATE can lock.
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"), {
        "lock_key": f"vfx-files-config:{production_id}"
    })


def _assert_kitsusync_is_legacy_owner(session, production_id):
    """Fence Plugin-owned rows and persist explicit ownership for legacy writes."""
    from ..models import KitsuSyncDesiredConfiguration, ProductionDriveAuthority

    authority = session.get(ProductionDriveAuthority, production_id)
    if authority is not None:
        if authority.owner != "legacy_kitsusync" or authority.state != "legacy_import_required":
            _fail(409, "plugin_authority_active" if authority.owner == "plugin" else "drive_authority_unavailable")
        return
    legacy = session.get(KitsuSyncDesiredConfiguration, production_id)
    authority = ProductionDriveAuthority(
        production_id=production_id,
        owner="legacy_kitsusync",
        state="legacy_import_required",
        config_revision=0,
        activation_epoch=max(0, legacy.notification_epoch) if legacy is not None else 0,
        connection_id=None,
        root_id=None,
        activation_enabled=False,
    )
    session.add(authority)
    session.flush()


def _check_connection(session, production_id, connection_id, drive):
    from ..models import StorageConnection

    connection = session.scalar(select(StorageConnection).where(StorageConnection.id == connection_id))
    if connection is None or connection.production_id != production_id:
        _fail(404, "connection_not_found")
    if connection.provider != "google_drive" or not connection.enabled:
        _fail(409, "connection_not_ready")
    if connection.allowed_root_id != drive["root_id"] or connection.account_mode != drive["account_mode"]:
        _fail(403, "production_access_denied")
    if connection.account_mode == "my_drive":
        # owner_subject is populated only by the provider-side verified-root
        # path. Never accept a user-provided email or permissionId here.
        if not connection.owner_subject or connection.owner_subject != drive.get("owner_subject"):
            _fail(409, "root_unverified")
        if drive.get("drive_id") not in (None, connection.drive_id):
            _fail(403, "production_access_denied")
    else:
        if not connection.drive_id or connection.drive_id != drive.get("drive_id") or drive.get("owner_subject") is not None:
            _fail(403, "production_access_denied")


def _persist_execution_template(session, *, production_id, actor_id, desired, config_revision, enabled):
    """Persist the existing fenced execution snapshot in the config transaction."""
    from ..models import SharedTemplateConfiguration
    from .template_materialization import ROOT_BINDING, TREE_ROOT_ID, materialize_template_selection

    drive = desired["drive"]
    tree, role_mappings, root_binding = materialize_template_selection(
        desired["template_selection"]["selections"]
    )
    if root_binding != ROOT_BINDING:
        _fail(409, "root_unverified")
    row = session.scalar(select(SharedTemplateConfiguration).where(
        SharedTemplateConfiguration.production_id == production_id
    ).with_for_update())
    values = {
        "storage_connection_id": UUID(desired["connection_id"]),
        "allowed_root_id": drive["root_id"],
        "template_folder_id": TREE_ROOT_ID,
        "template_tree": tree,
        "role_mappings": role_mappings,
        "version": config_revision,
        "enabled": bool(enabled),
        "updated_by": actor_id,
    }
    if row is None:
        row = SharedTemplateConfiguration(
            production_id=production_id,
            created_at=datetime.now(timezone.utc),
            **values,
        )
        session.add(row)
    else:
        for name, value in values.items():
            setattr(row, name, value)
    session.flush()
    return row


def _valid_idempotency_key(value):
    if not isinstance(value, str) or not 16 <= len(value) <= 128:
        _fail(400, "invalid_event", field="Idempotency-Key")
    return value


def _remember(session, production_id, actor_id, key, operation, request_hash, status, etag, body):
    from ..models import KitsuSyncManagementIdempotency

    session.add(KitsuSyncManagementIdempotency(
        id=uuid4(), production_id=production_id, actor_id=actor_id, idempotency_key=key,
        operation=operation, request_sha256=request_hash,
        response_status=status, response_etag=etag, response_body=body,
    ))


def get_desired_config(session, production_id: UUID):
    from .storage_authority import current_configuration_record

    row = current_configuration_record(session, production_id)
    if row is None:
        _fail(404, "connection_not_found")
    return _configuration_payload(row), f'"{row.config_revision}"'


def _plugin_configuration_response(authority, body):
    from types import SimpleNamespace

    row = SimpleNamespace(
        production_id=authority.production_id,
        config_revision=authority.config_revision,
        notification_epoch=authority.activation_epoch,
        desired_config=body,
    )
    response = _configuration_payload(row)
    response.update({"configured": True, "owner": "plugin", "state": "configured"})
    response["activation"].update({
        "enabled": bool(authority.activation_enabled),
        "requested_enabled": bool(body.get("activation", {}).get("enabled", False)),
        "transition_pending": bool(getattr(authority, "activation_transition_pending", False)),
    })
    response.get("drive", {}).pop("owner_subject", None)
    return response


def put_plugin_desired_config(
    session, *, production_id: UUID, actor_id: UUID, payload: object,
    idempotency_key: str, if_match: str | None,
):
    """Save native Kitsu template/scope settings under Plugin ownership."""
    from ..models import ProductionDriveAuthority
    from .storage_authority import selected_storage_connection

    if not isinstance(payload, dict) or set(payload) != {"contract_version", "template_selection", "sync_policy"}:
        _fail(400, "invalid_event", field="desired_config")
    if payload.get("contract_version") != "1.0":
        _fail(400, "unsupported_contract_version")
    key = _valid_idempotency_key(idempotency_key)
    if not isinstance(if_match, str) or not re.fullmatch(r'"(?:0|[1-9][0-9]*)"', if_match):
        _fail(400, "invalid_event", field="If-Match")
    expected = int(if_match[1:-1])
    operation = "plugin_desired_config.put"
    request_hash = _request_hash(operation, payload, if_match, None)
    with session.begin():
        _lock_production(session, production_id)
        prior = _response_receipt(session, production_id, actor_id, key, operation, request_hash)
        if prior is not None:
            return prior.response_body, prior.response_status, prior.response_etag
        authority = session.scalar(select(ProductionDriveAuthority).where(
            ProductionDriveAuthority.production_id == production_id,
        ).with_for_update())
        if authority is None or authority.owner != "plugin" or authority.state != "configured":
            _fail(409, "drive_authority_unavailable")
        if authority.config_revision != expected:
            _fail(409, "stale_config", expected_revision=expected, actual_revision=authority.config_revision)
        if getattr(authority, "activation_transition_pending", False):
            _fail(409, "activation_barrier_pending")
        if authority.activation_enabled:
            _fail(409, "deactivate_before_config_change")
        connection = selected_storage_connection(session, production_id)
        if connection is None or connection.id != authority.connection_id or connection.allowed_root_id != authority.root_id:
            _fail(409, "connection_not_ready")
        desired = {
            "contract_version": "1.0",
            "connection_id": str(connection.id),
            "drive": {
                "account_mode": connection.account_mode,
                "drive_id": connection.drive_id,
                "owner_subject": connection.owner_subject,
                "root_id": connection.allowed_root_id,
                "root_state": "verified",
            },
            "template_selection": payload["template_selection"],
            "sync_policy": payload["sync_policy"],
        }
        desired = validate_desired_config(desired)
        authority.config_revision += 1
        authority.activation_epoch += 1
        desired["activation"] = {
            "enabled": False, "activated_at": None,
            "notification_epoch": authority.activation_epoch,
        }
        authority.desired_config = desired
        authority.updated_by = actor_id
        _persist_execution_template(
            session, production_id=production_id, actor_id=actor_id,
            desired=desired, config_revision=authority.config_revision, enabled=False,
        )
        body = _plugin_configuration_response(authority, desired)
        etag = f'"{authority.config_revision}"'
        _remember(session, production_id, actor_id, key, operation, request_hash, 200, etag, body)
        session.flush()
    return body, 200, etag


def set_plugin_activation(
    session, *, production_id: UUID, actor_id: UUID, enabled: bool,
    payload: object, idempotency_key: str, if_match: str | None, capabilities=None,
    deactivation_barrier_required: bool = False,
):
    """Activate or deactivate the Plugin-owned Production with fresh fencing."""
    from ..models import ProductionDriveAuthority, SharedTemplateConfiguration
    from .storage_authority import selected_storage_connection

    if not isinstance(enabled, bool):
        _fail(400, "invalid_event")
    if not isinstance(payload, dict) or set(payload) != {"contract_version"} or payload.get("contract_version") != "1.0":
        _fail(400, "unsupported_contract_version" if isinstance(payload, dict) and payload.get("contract_version") != "1.0" else "invalid_event")
    key = _valid_idempotency_key(idempotency_key)
    if not isinstance(if_match, str) or not re.fullmatch(r'"[1-9][0-9]*"', if_match):
        _fail(400, "invalid_event", field="If-Match")
    expected = int(if_match[1:-1])
    operation = "plugin.activation.enable" if enabled else "plugin.activation.disable"
    request_hash = _request_hash(operation, payload, if_match, None)
    with session.begin():
        _lock_production(session, production_id)
        prior = _response_receipt(session, production_id, actor_id, key, operation, request_hash)
        if prior is not None:
            return prior.response_body, prior.response_status, prior.response_etag
        authority = session.scalar(select(ProductionDriveAuthority).where(
            ProductionDriveAuthority.production_id == production_id,
        ).with_for_update())
        if authority is None or authority.owner != "plugin" or authority.state != "configured":
            _fail(409, "drive_authority_unavailable")
        if authority.config_revision != expected:
            _fail(409, "stale_config", expected_revision=expected, actual_revision=authority.config_revision)
        if getattr(authority, "deactivation_pending", False):
            _fail(409, "activation_barrier_pending")
        desired = authority.desired_config
        if not isinstance(desired, dict):
            _fail(409, "configuration_required")
        if enabled:
            connection = selected_storage_connection(session, production_id)
            if connection is None or connection.id != authority.connection_id or connection.allowed_root_id != authority.root_id:
                _fail(409, "connection_not_ready")
            if (
                capabilities is None or not capabilities.root_visible
                or capabilities.root_id != authority.root_id or not capabilities.can_add_children
            ):
                _fail(409, "root_not_writable")
            policy = desired.get("sync_policy", {})
            if not isinstance(policy, dict) or policy.get("mode") not in {"new_production", "adopt_existing", "future_only"}:
                _fail(409, "invalid_event", field="sync_policy.mode")
            try:
                from .template_materialization import validate_sync_template_scope
                validate_sync_template_scope(
                    desired.get("template_selection", {}).get("selections"),
                    policy.get("scope", {}), allow_empty=False,
                )
            except (TypeError, ValueError):
                _fail(409, "invalid_event", field="sync_policy.scope")
            template = session.scalar(select(SharedTemplateConfiguration).where(
                SharedTemplateConfiguration.production_id == production_id,
                SharedTemplateConfiguration.storage_connection_id == connection.id,
                SharedTemplateConfiguration.allowed_root_id == authority.root_id,
            ).with_for_update())
            if template is None:
                _fail(409, "configuration_required")
        else:
            template = session.scalar(select(SharedTemplateConfiguration).where(
                SharedTemplateConfiguration.production_id == production_id,
            ).with_for_update())

        previous_epoch = authority.activation_epoch
        transition_pending = bool(deactivation_barrier_required and authority.activation_enabled != enabled)
        authority.activation_epoch += 1
        authority.config_revision += 1
        authority.activation_enabled = False if transition_pending else enabled
        authority.activation_transition_pending = transition_pending
        authority.updated_by = actor_id
        desired = dict(desired)
        desired["activation"] = {
            "enabled": enabled,
            "activated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") if enabled else None,
            "notification_epoch": authority.activation_epoch,
        }
        authority.desired_config = desired
        if template is not None:
            # Keep every local worker paused until KitsuSync has durably
            # fenced the transition and acknowledged the drain barrier.
            template.enabled = enabled and not transition_pending
            template.version = authority.config_revision
            template.updated_by = actor_id
        if transition_pending:
            from ..models import KitsuSyncEventOutbox

            transition_type = "storage_authority.activation_requested" if enabled else "storage_authority.deactivation_requested"
            event_id = str(uuid5(
                UUID("c1edfb56-6b81-4cc2-94bc-a388c0a19db4"),
                f"{production_id}:{transition_type}:{previous_epoch}:{authority.activation_epoch}",
            ))
            event = {
                "contract_version": "1.0",
                "events": [{
                    "contract_version": "1.0", "producer_id": "vfx-files-plugin",
                    "event_id": event_id, "event_type": transition_type,
                    "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "production_id": str(production_id),
                    "activation_epoch": authority.activation_epoch if enabled else previous_epoch,
                    "config_revision": authority.config_revision,
                    "previous_activation_epoch": previous_epoch,
                    "new_activation_epoch": authority.activation_epoch,
                }],
            }
            from .v1 import canonical_json, sha256_hex, validate_event_batch
            validate_event_batch(event)
            payload = event["events"][0]
            session.add(KitsuSyncEventOutbox(
                production_id=production_id, storage_connection_id=None,
                event_id=event_id, event_type=transition_type, payload=payload,
                payload_sha256=sha256_hex(canonical_json(payload)), state="queued", attempts=0,
                lease_generation=0,
            ))
        session.flush()
        body = _plugin_configuration_response(authority, desired)
        etag = f'"{authority.config_revision}"'
        response_status = 202 if transition_pending else 200
        _remember(session, production_id, actor_id, key, operation, request_hash, response_status, etag, body)
        session.flush()
    return body, response_status, etag


def put_desired_config(session, *, production_id: UUID, actor_id: UUID, payload: object, idempotency_key: str,
                       if_match: str | None, if_none_match: str | None):
    from ..models import KitsuSyncDesiredConfiguration

    payload = validate_desired_config(payload)
    key = _valid_idempotency_key(idempotency_key)
    if (if_match is None) == (if_none_match is None) or (if_none_match is not None and if_none_match != "*"):
        _fail(400, "invalid_event", field="If-Match")
    if if_match is not None and not re.fullmatch(r'"[1-9][0-9]*"', if_match):
        _fail(400, "invalid_event", field="If-Match")
    operation = "desired_config.put"
    request_hash = _request_hash(operation, payload, if_match, if_none_match)
    with session.begin():
        _lock_production(session, production_id)
        _assert_kitsusync_is_legacy_owner(session, production_id)
        prior = _response_receipt(session, production_id, actor_id, key, operation, request_hash)
        if prior is not None:
            return prior.response_body, prior.response_status, prior.response_etag
        row = session.scalar(select(KitsuSyncDesiredConfiguration).where(
            KitsuSyncDesiredConfiguration.production_id == production_id
        ).with_for_update())
        if row is None:
            if if_none_match != "*":
                _fail(409, "stale_config", expected_revision=1, actual_revision=0)
            connection_id = UUID(payload["connection_id"])
            _check_connection(session, production_id, connection_id, payload["drive"])
            body = dict(payload)
            body["activation"] = {"enabled": False, "activated_at": None, "notification_epoch": 0}
            row = KitsuSyncDesiredConfiguration(
                production_id=production_id, config_revision=1,
                notification_epoch=0, desired_config=body,
            )
            status = 201
        else:
            if if_none_match == "*":
                _fail(412, "stale_config", expected_revision=0, actual_revision=row.config_revision)
            expected = int(if_match[1:-1])
            if expected != row.config_revision:
                _fail(409, "stale_config", expected_revision=expected, actual_revision=row.config_revision)
            connection_id = UUID(payload["connection_id"])
            _check_connection(session, production_id, connection_id, payload["drive"])
            activation = dict(row.desired_config.get("activation", {}))
            row.config_revision += 1
            body = dict(payload)
            body["activation"] = activation
            row.desired_config = body
            row.updated_at = datetime.now(timezone.utc)
            status = 200
        session.add(row)
        session.flush()
        _persist_execution_template(
            session,
            production_id=production_id,
            actor_id=actor_id,
            desired=body,
            config_revision=row.config_revision,
            enabled=bool(body.get("activation", {}).get("enabled", False)),
        )
        response_body = _configuration_payload(row)
        etag = f'"{row.config_revision}"'
        _remember(session, production_id, actor_id, key, operation, request_hash, status, etag, response_body)
        session.flush()
    return response_body, status, etag


def set_activation(session, *, production_id: UUID, actor_id: UUID, enabled: bool,
                   payload: object, idempotency_key: str, if_match: str | None):
    from ..models import KitsuSyncDesiredConfiguration

    if not isinstance(payload, dict) or set(payload) - {"contract_version", "reason_code"} or payload.get("contract_version") != "1.0":
        _fail(400, "unsupported_contract_version" if isinstance(payload, dict) and payload.get("contract_version") != "1.0" else "invalid_event")
    reason = payload.get("reason_code")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 80):
        _fail(400, "invalid_event", field="reason_code")
    key = _valid_idempotency_key(idempotency_key)
    if not isinstance(if_match, str) or not re.fullmatch(r'"[1-9][0-9]*"', if_match):
        _fail(400, "invalid_event", field="If-Match")
    operation = "activation.enable" if enabled else "activation.disable"
    request_hash = _request_hash(operation, payload, if_match, None)
    with session.begin():
        _lock_production(session, production_id)
        _assert_kitsusync_is_legacy_owner(session, production_id)
        prior = _response_receipt(session, production_id, actor_id, key, operation, request_hash)
        if prior is not None:
            return prior.response_body, prior.response_status, prior.response_etag
        row = session.scalar(select(KitsuSyncDesiredConfiguration).where(
            KitsuSyncDesiredConfiguration.production_id == production_id
        ).with_for_update())
        if row is None:
            _fail(409, "stale_config", expected_revision=int(if_match[1:-1]), actual_revision=0)
        expected = int(if_match[1:-1])
        if expected != row.config_revision:
            _fail(409, "stale_config", expected_revision=expected, actual_revision=row.config_revision)
        policy = row.desired_config.get("sync_policy", {})
        if enabled and (not isinstance(policy, dict) or policy.get("mode") != "new_production"):
            # Do not activate notifications or sync for a folder policy whose
            # materialization semantics are not implemented. Deactivation stays
            # available so old configurations can be made safe.
            _fail(409, "invalid_event", field="sync_policy.mode")
        if enabled:
            try:
                from .template_materialization import validate_sync_template_scope
                validate_sync_template_scope(
                    row.desired_config.get("template_selection", {}).get("selections"),
                    policy.get("scope", {}),
                    allow_empty=False,
                )
            except (TypeError, ValueError):
                _fail(409, "invalid_event", field="sync_policy.scope")
        body = dict(row.desired_config)
        epoch = row.notification_epoch + 1
        body["activation"] = {
            "enabled": enabled,
            "activated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") if enabled else None,
            "notification_epoch": epoch,
        }
        row.config_revision += 1
        row.notification_epoch = epoch
        row.desired_config = body
        row.updated_at = datetime.now(timezone.utc)
        _persist_execution_template(
            session,
            production_id=production_id,
            actor_id=actor_id,
            desired=body,
            config_revision=row.config_revision,
            enabled=enabled,
        )
        response_body = _configuration_payload(row)
        etag = f'"{row.config_revision}"'
        _remember(session, production_id, actor_id, key, operation, request_hash, 200, etag, response_body)
        session.flush()
    return response_body, 200, etag
