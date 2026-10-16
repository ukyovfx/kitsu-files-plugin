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

    desired = dict(legacy.desired_config or {})
    activation = desired.get("activation") or {}
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


def _blocked(reason, **evidence):
    return {
        "status": "blocked",
        "reason": reason,
        **evidence,
        "writes_performed": False,
        "drive_operations_performed": False,
    }
