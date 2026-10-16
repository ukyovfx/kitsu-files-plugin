"""Native Kitsu root selection for OAuth-authorized My Drive folders."""

from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID

from sqlalchemy import select

from .configuration import ConfigurationError, _lock_production
from .storage_authority import production_drive_authority_status


def _request_digest(payload: dict, if_match: str) -> str:
    raw = json.dumps(
        {"operation": "native_root.select", "payload": payload, "if_match": if_match},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def list_native_root_candidates(connection, adapter):
    """List only app-visible My Drive folders; never infer Drive access from URLs."""
    if connection.account_mode != "my_drive":
        raise ConfigurationError(409, "shared_drive_selection_unavailable")
    if not connection.owner_subject:
        raise ConfigurationError(409, "identity_unverified")
    listing = adapter.list_root_candidates()
    if not listing.complete or listing.next_page_token is not None or len(listing.items) > 500:
        raise ConfigurationError(503, "provider_unavailable")
    candidates = []
    for item in listing.items:
        if item.trashed or item.mime_type not in ("folder", "application/vnd.google-apps.folder"):
            continue
        if not isinstance(item.name, str) or not item.name or not item.object_id:
            raise ConfigurationError(503, "provider_unavailable")
        candidates.append({"id": item.object_id, "name": item.name, "account_mode": "my_drive"})
    return {"contract_version": "1.0", "roots": candidates}


def native_root_receipt(session, *, production_id: UUID, actor_id: UUID, payload: object,
                        idempotency_key: str | None, if_match: str | None):
    """Return a matching prior result before another provider read is attempted."""
    from ..models import KitsuSyncManagementIdempotency

    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract_version", "root_id"}
        or payload.get("contract_version") != "1.0"
        or not isinstance(payload.get("root_id"), str)
        or not payload["root_id"] or len(payload["root_id"]) > 255
    ):
        raise ConfigurationError(400, "invalid_event")
    if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128:
        raise ConfigurationError(400, "invalid_event")
    if not isinstance(if_match, str) or not re.fullmatch(r'"(?:0|[1-9][0-9]*)"', if_match):
        raise ConfigurationError(400, "invalid_event")
    previous = session.scalar(select(KitsuSyncManagementIdempotency).where(
        KitsuSyncManagementIdempotency.production_id == production_id,
        KitsuSyncManagementIdempotency.idempotency_key == idempotency_key,
    ))
    if previous is None:
        return None
    if (
        previous.actor_id != actor_id or previous.operation != "native_root.select"
        or previous.request_sha256 != _request_digest(payload, if_match)
    ):
        raise ConfigurationError(409, "idempotency_conflict")
    return previous.response_body, previous.response_status, previous.response_etag


def select_native_root(session, *, production_id: UUID, actor_id: UUID, connection_id: UUID,
                       payload: object, idempotency_key: str | None, if_match: str | None,
                       capabilities):
    """Persist a verified root under the Plugin owner with CAS and replay safety."""
    from ..models import (
        DriveCredential,
        KitsuSyncDesiredConfiguration,
        KitsuSyncManagementIdempotency,
        ProductionDriveAuthority,
        SharedTemplateConfiguration,
        StorageConnection,
    )

    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract_version", "root_id"}
        or payload.get("contract_version") != "1.0"
        or not isinstance(payload.get("root_id"), str)
        or not payload["root_id"] or len(payload["root_id"]) > 255
    ):
        raise ConfigurationError(400, "invalid_event")
    if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128:
        raise ConfigurationError(400, "invalid_event")
    if not isinstance(if_match, str) or not re.fullmatch(r'"(?:0|[1-9][0-9]*)"', if_match):
        raise ConfigurationError(400, "invalid_event")
    if (
        not capabilities.root_visible
        or capabilities.root_id != payload["root_id"]
    ):
        raise ConfigurationError(403, "root_unverified")

    expected_revision = int(if_match[1:-1])
    request_sha256 = _request_digest(payload, if_match)
    with session.begin():
        if session.get_bind().dialect.name == "postgresql":
            _lock_production(session, production_id)
        previous = native_root_receipt(
            session, production_id=production_id, actor_id=actor_id, payload=payload,
            idempotency_key=idempotency_key, if_match=if_match,
        )
        if previous is not None:
            return previous

        authority = session.scalar(select(ProductionDriveAuthority).where(
            ProductionDriveAuthority.production_id == production_id,
        ).with_for_update())
        if authority is not None and authority.owner == "legacy_kitsusync":
            raise ConfigurationError(409, "legacy_import_required")
        if authority is None:
            if session.get(KitsuSyncDesiredConfiguration, production_id) is not None:
                raise ConfigurationError(409, "legacy_import_required")
            authority = ProductionDriveAuthority(
                production_id=production_id, owner="plugin", state="unconfigured",
                config_revision=0, activation_epoch=0, connection_id=None, root_id=None,
                activation_enabled=False,
            )
            session.add(authority)
            session.flush()
        if authority.owner != "plugin" or authority.state not in {"unconfigured", "configured"}:
            raise ConfigurationError(409, "drive_authority_unavailable")
        if getattr(authority, "activation_transition_pending", False):
            raise ConfigurationError(409, "activation_barrier_pending")
        if authority.config_revision != expected_revision:
            raise ConfigurationError(409, "stale_config")
        if authority.activation_enabled:
            raise ConfigurationError(409, "deactivate_before_root_change")

        connection = session.scalar(select(StorageConnection).where(
            StorageConnection.id == connection_id,
            StorageConnection.production_id == production_id,
        ).with_for_update())
        credential = session.scalar(select(DriveCredential).where(
            DriveCredential.connection_id == connection_id,
            DriveCredential.production_id == production_id,
        ).with_for_update())
        if connection is None:
            raise ConfigurationError(404, "connection_not_found")
        if (
            connection.provider != "google_drive" or connection.account_mode != "my_drive"
            or credential is None or credential.state != "active"
            or not connection.owner_subject
            or credential.account_subject != connection.owner_subject
            or credential.owner_subject != connection.owner_subject
        ):
            raise ConfigurationError(409, "connection_not_ready")
        if not capabilities.can_add_children:
            raise ConfigurationError(409, "root_not_writable")

        root_or_connection_changed = (
            authority.connection_id != connection_id or authority.root_id != payload["root_id"]
        )
        connection.allowed_root_id = payload["root_id"]
        connection.enabled = True
        authority.owner = "plugin"
        authority.state = "configured"
        authority.connection_id = connection_id
        authority.root_id = payload["root_id"]
        authority.config_revision += 1
        if root_or_connection_changed:
            authority.activation_epoch += 1
            if isinstance(authority.desired_config, dict):
                desired = dict(authority.desired_config)
                desired["connection_id"] = str(connection.id)
                desired["drive"] = {
                    "account_mode": connection.account_mode,
                    "drive_id": connection.drive_id,
                    "owner_subject": connection.owner_subject,
                    "root_id": connection.allowed_root_id,
                    "root_state": "verified",
                }
                desired["activation"] = {
                    "enabled": False, "activated_at": None,
                    "notification_epoch": authority.activation_epoch,
                }
                authority.desired_config = desired
            templates = session.scalars(select(SharedTemplateConfiguration).where(
                SharedTemplateConfiguration.production_id == production_id,
            ).with_for_update()).all()
            for template in templates:
                template.enabled = False
                template.storage_connection_id = connection_id
                template.allowed_root_id = payload["root_id"]
                template.version = authority.config_revision
                template.updated_by = actor_id
        authority.activation_enabled = False
        authority.updated_by = actor_id
        session.flush()
        status = production_drive_authority_status(session, production_id)
        etag = f'"{authority.config_revision}"'
        session.add(KitsuSyncManagementIdempotency(
            production_id=production_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            operation="native_root.select",
            request_sha256=request_sha256,
            response_status=200,
            response_etag=etag,
            response_body=status,
        ))
        session.flush()
    return status, 200, etag
