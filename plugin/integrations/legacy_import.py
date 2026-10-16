"""Read-only preflight for explicitly importing legacy KitsuSync settings.

This module never changes configuration or calls a storage provider. A passing
preview means only that the stored identities are internally consistent and
may be presented for a separately authorized import confirmation.
"""

from uuid import UUID

from sqlalchemy import select


def preview_legacy_import(session, production_id):
    """Return a sanitized, non-mutating legacy-import preview.

    Credentials are not decrypted or returned. Existing activation is never
    carried into an imported Plugin configuration; a future import must fence
    old work and require fresh activation after explicit confirmation.
    """
    from ..models import (
        DriveCredential,
        KitsuSyncDesiredConfiguration,
        ProductionDriveAuthority,
        SharedTemplateConfiguration,
    )
    from .configuration import ConfigurationError, validate_desired_config
    from .storage_authority import selected_storage_connection

    try:
        production_id = UUID(str(production_id))
    except (TypeError, ValueError, AttributeError):
        return _blocked("invalid_production")

    legacy = session.scalar(select(KitsuSyncDesiredConfiguration).where(
        KitsuSyncDesiredConfiguration.production_id == production_id,
    ))
    if legacy is None:
        return {
            "status": "no_legacy_configuration",
            "production_id": str(production_id),
            "writes_performed": False,
            "drive_operations_performed": False,
        }

    authority = session.scalar(select(ProductionDriveAuthority).where(
        ProductionDriveAuthority.production_id == production_id,
    ))
    if (
        authority is None or authority.owner != "legacy_kitsusync"
        or authority.state != "legacy_import_required"
    ):
        return _blocked("legacy_authority_not_pending", production_id=str(production_id))

    if (
        isinstance(legacy.config_revision, bool)
        or not isinstance(legacy.config_revision, int)
        or legacy.config_revision < 1
        or not isinstance(legacy.desired_config, dict)
    ):
        return _blocked(
            "legacy_configuration_invalid",
            production_id=str(production_id),
        )

    desired = dict(legacy.desired_config)
    activation = desired.get("activation", {})
    if activation is None:
        return _blocked(
            "activation_state_invalid",
            production_id=str(production_id),
            source_revision=legacy.config_revision,
        )
    activation = activation or {}
    if not isinstance(activation, dict):
        return _blocked(
            "activation_state_invalid",
            production_id=str(production_id),
            source_revision=legacy.config_revision,
        )
    activation_enabled = activation.get("enabled", False)
    activation_epoch = activation.get("notification_epoch", legacy.notification_epoch)
    if (
        not isinstance(activation_enabled, bool)
        or isinstance(activation_epoch, bool)
        or not isinstance(activation_epoch, int)
        or activation_epoch != legacy.notification_epoch
    ):
        return _blocked(
            "activation_state_invalid",
            production_id=str(production_id),
            source_revision=legacy.config_revision,
        )
    try:
        # Activation is stored alongside, but outside, the immutable desired
        # configuration request contract.
        validate_desired_config({key: value for key, value in desired.items() if key != "activation"})
    except (ConfigurationError, TypeError, ValueError):
        return _blocked(
            "legacy_configuration_invalid",
            production_id=str(production_id),
            source_revision=legacy.config_revision,
        )

    connection = selected_storage_connection(session, production_id)
    if connection is None:
        return _blocked(
            "connection_or_verified_root_mismatch",
            production_id=str(production_id),
            source_revision=legacy.config_revision,
        )

    template = session.scalar(select(SharedTemplateConfiguration).where(
        SharedTemplateConfiguration.production_id == production_id,
        SharedTemplateConfiguration.storage_connection_id == connection.id,
    ))
    if (
        template is None
        or template.storage_connection_id != connection.id
        or template.allowed_root_id != connection.allowed_root_id
        or bool(template.enabled) != activation_enabled
    ):
        return _blocked(
            "execution_template_mismatch",
            production_id=str(production_id),
            source_revision=legacy.config_revision,
            connection_id=str(connection.id),
            root_id=connection.allowed_root_id,
        )

    credential_state = session.scalar(select(DriveCredential.state).where(
        DriveCredential.connection_id == connection.id,
        DriveCredential.production_id == production_id,
    ))
    if credential_state != "active":
        return _blocked(
            "credential_not_active",
            production_id=str(production_id),
            source_revision=legacy.config_revision,
            connection_id=str(connection.id),
            root_id=connection.allowed_root_id,
        )

    source_epoch = legacy.notification_epoch
    if isinstance(source_epoch, bool) or not isinstance(source_epoch, int) or source_epoch < 0:
        return _blocked(
            "activation_epoch_invalid",
            production_id=str(production_id),
            source_revision=legacy.config_revision,
            connection_id=str(connection.id),
            root_id=connection.allowed_root_id,
        )

    return {
        "status": "ready_for_explicit_review",
        "production_id": str(production_id),
        "source_revision": legacy.config_revision,
        "source_activation_enabled": activation_enabled,
        "source_notification_epoch": source_epoch,
        "proposed_activation_enabled": False,
        "proposed_notification_epoch": source_epoch + 1,
        "connection_id": str(connection.id),
        "root_id": connection.allowed_root_id,
        "root_verification": "stored_identity_match_only",
        "template_version": template.version,
        "requires_explicit_admin_confirmation": True,
        "outstanding_work_review_required": True,
        "credentials_decrypted": False,
        "writes_performed": False,
        "drive_operations_performed": False,
    }


def import_legacy_configuration(
    session, *, production_id, actor_id, payload, idempotency_key, if_match, capabilities,
):
    """Explicitly transfer one verified legacy configuration into Plugin ownership.

    The legacy configuration, OAuth credential, root, bindings, jobs and history
    stay in place. The existing execution template is only disabled and
    revision-fenced. No provider mutation is performed.
    """
    import re

    from ..models import (
        DriveCredential,
        KitsuSyncDesiredConfiguration,
        ProductionDriveAuthority,
        ProductionDriveAuthorityEvent,
        SharedTemplateConfiguration,
        StorageConnection,
    )
    from .configuration import (
        ConfigurationError,
        _lock_production,
        _remember,
        _request_hash,
        _response_receipt,
    )
    from .storage_authority import production_drive_authority_status

    try:
        production_id = UUID(str(production_id))
        actor_id = UUID(str(actor_id))
    except (TypeError, ValueError, AttributeError):
        raise ConfigurationError(400, "invalid_event") from None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract_version", "source_revision", "confirm"}
        or payload.get("contract_version") != "1.0"
        or not isinstance(payload.get("source_revision"), int)
        or isinstance(payload.get("source_revision"), bool)
        or payload["source_revision"] < 1
        or payload.get("confirm") is not True
    ):
        raise ConfigurationError(400, "invalid_event")
    if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128:
        raise ConfigurationError(400, "invalid_event", details={"field": "Idempotency-Key"})
    expected_revision = payload["source_revision"]
    if not isinstance(if_match, str) or not re.fullmatch(r'"legacy-[1-9][0-9]*"', if_match):
        raise ConfigurationError(400, "invalid_event", details={"field": "If-Match"})
    if int(if_match[8:-1]) != expected_revision:
        raise ConfigurationError(400, "invalid_event", details={"field": "If-Match"})
    operation = "plugin.legacy_import.confirm"
    request_hash = _request_hash(operation, payload, if_match, None)

    with session.begin():
        _lock_production(session, production_id)
        prior = _response_receipt(session, production_id, actor_id, idempotency_key, operation, request_hash)
        if prior is not None:
            return prior.response_body, prior.response_status, prior.response_etag

        authority = session.scalar(select(ProductionDriveAuthority).where(
            ProductionDriveAuthority.production_id == production_id,
        ).with_for_update())
        if (
            authority is None or authority.owner != "legacy_kitsusync"
            or authority.state != "legacy_import_required"
        ):
            raise ConfigurationError(409, "legacy_import_not_pending")

        legacy = session.scalar(select(KitsuSyncDesiredConfiguration).where(
            KitsuSyncDesiredConfiguration.production_id == production_id,
        ).with_for_update())
        if legacy is None or legacy.config_revision != expected_revision:
            raise ConfigurationError(409, "stale_legacy_revision")

        try:
            legacy_connection_id = UUID(str((legacy.desired_config or {}).get("connection_id", "")))
        except (TypeError, ValueError, AttributeError):
            raise ConfigurationError(409, "legacy_import_blocked") from None
        connection = session.scalar(select(StorageConnection).where(
            StorageConnection.production_id == production_id,
            StorageConnection.id == legacy_connection_id,
        ).with_for_update())
        if connection is None or connection.account_mode != "my_drive":
            raise ConfigurationError(409, "existing_root_not_supported")
        credential = session.scalar(select(DriveCredential).where(
            DriveCredential.production_id == production_id,
            DriveCredential.connection_id == connection.id,
        ).with_for_update())
        template = session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == production_id,
            SharedTemplateConfiguration.storage_connection_id == connection.id,
        ).with_for_update())

        if (
            capabilities is None or not capabilities.root_visible
            or capabilities.root_id != connection.allowed_root_id
            or not capabilities.can_add_children
        ):
            raise ConfigurationError(409, "root_not_writable")
        preview = preview_legacy_import(session, production_id)
        if preview.get("status") != "ready_for_explicit_review":
            raise ConfigurationError(409, "legacy_import_blocked", details={"reason": preview.get("reason")})
        if credential is None or credential.state != "active" or template is None:
            raise ConfigurationError(409, "legacy_import_blocked")

        desired = dict(legacy.desired_config)
        desired.pop("activation", None)
        new_revision = max(authority.config_revision, legacy.config_revision) + 1
        new_epoch = max(authority.activation_epoch, legacy.notification_epoch) + 1
        desired["activation"] = {
            "enabled": False,
            "activated_at": None,
            "notification_epoch": new_epoch,
        }
        authority.owner = "plugin"
        authority.state = "configured"
        authority.config_revision = new_revision
        authority.activation_epoch = new_epoch
        authority.connection_id = connection.id
        authority.root_id = connection.allowed_root_id
        authority.desired_config = desired
        authority.activation_enabled = False
        authority.updated_by = actor_id
        template.enabled = False
        template.version = new_revision
        template.updated_by = actor_id
        session.add(ProductionDriveAuthorityEvent(
            production_id=production_id,
            actor_id=actor_id,
            event_type="legacy_configuration_imported",
            source_revision=expected_revision,
            config_revision=new_revision,
            activation_epoch=new_epoch,
            idempotency_key=idempotency_key,
        ))
        session.flush()

        body = production_drive_authority_status(session, production_id)
        body.update({
            "contract_version": "1.0",
            "import_status": "imported_inactive",
            "source_revision": expected_revision,
            "legacy_source_preserved": True,
            "template_preserved": True,
            "credentials_decrypted": False,
            "drive_operations_performed": False,
        })
        etag = f'"plugin-{new_revision}"'
        _remember(session, production_id, actor_id, idempotency_key, operation, request_hash, 200, etag, body)
        session.flush()
    return body, 200, etag


def _blocked(reason, **evidence):
    return {
        "status": "blocked",
        "reason": reason,
        **evidence,
        "writes_performed": False,
        "drive_operations_performed": False,
    }
