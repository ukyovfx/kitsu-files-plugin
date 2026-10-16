"""Resolve a Production Drive connection through its explicit owner record."""

from uuid import UUID
from types import SimpleNamespace

from sqlalchemy import select


def selected_storage_connection(session, production_id):
    """Return the exact owner-selected root, preserving legacy rows for review."""
    from ..models import DriveCredential, ProductionDriveAuthority, StorageConnection

    authority = session.scalar(select(ProductionDriveAuthority).where(
        ProductionDriveAuthority.production_id == production_id,
    ))
    if authority is not None:
        if authority.owner == "plugin":
            if authority.state != "configured" or authority.config_revision < 1:
                return None
            try:
                connection_id = UUID(str(authority.connection_id))
            except (TypeError, ValueError, AttributeError):
                return None
            connection = session.scalar(select(StorageConnection).where(
                StorageConnection.id == connection_id,
                StorageConnection.production_id == production_id,
            ))
            if (
                connection is None or connection.id != connection_id
                or connection.production_id != production_id or not connection.enabled
                or connection.provider != "google_drive"
                or connection.allowed_root_id != authority.root_id
            ):
                return None
            credential = session.scalar(select(DriveCredential).where(
                DriveCredential.connection_id == connection_id,
                DriveCredential.production_id == production_id,
            ))
            if credential is None or credential.state != "active" or credential.account_mode != connection.account_mode:
                return None
            if connection.account_mode == "my_drive":
                if (
                    not connection.owner_subject
                    or credential.account_subject != connection.owner_subject
                    or credential.owner_subject != connection.owner_subject
                ):
                    return None
                return connection
            if connection.account_mode == "shared_drive":
                if (
                    not connection.drive_id
                    or credential.owner_subject is not None
                    or credential.drive_id != connection.drive_id
                ):
                    return None
                return connection
            return None
        if authority.owner != "legacy_kitsusync" or authority.state != "legacy_import_required":
            return None

    return _legacy_selected_storage_connection(session, production_id)


def fence_plugin_authority_after_credential_failure(session, production_id, connection_id):
    """Disable and advance a matching Plugin authority after grant loss.

    Call inside the same transaction that marks the encrypted credential
    unusable. The epoch bump prevents queued events from becoming current if
    the connection is later reauthorized.
    """
    from ..models import ProductionDriveAuthority

    try:
        connection_uuid = UUID(str(connection_id))
    except (TypeError, ValueError, AttributeError):
        return False
    authority = session.scalar(select(ProductionDriveAuthority).where(
        ProductionDriveAuthority.production_id == production_id,
    ).with_for_update())
    if (
        authority is None or authority.owner != "plugin" or authority.state != "configured"
        or authority.connection_id != connection_uuid
    ):
        return False
    authority.activation_enabled = False
    authority.activation_epoch += 1
    authority.config_revision += 1
    return True


def effective_drive_authority(session, production_id, *, lock_legacy=False):
    """Return the effective Drive owner, root and activation fence.

    Legacy rows retain their existing interpretation until explicit import.
    Plugin-owned rows become authoritative only after a fully configured
    connection is selected and its encrypted credential remains active.
    """
    from ..models import KitsuSyncDesiredConfiguration, ProductionDriveAuthority

    authority_query = select(ProductionDriveAuthority).where(
        ProductionDriveAuthority.production_id == production_id,
    )
    if lock_legacy:
        authority_query = authority_query.with_for_update()
    authority = session.scalar(authority_query)
    if authority is not None and authority.owner == "plugin":
        try:
            epoch = authority.activation_epoch
            revision = authority.config_revision
            connection_id = UUID(str(authority.connection_id))
        except (TypeError, ValueError, AttributeError):
            return None
        connection = selected_storage_connection(session, production_id)
        configured = (
            authority.state == "configured" and revision >= 1
            and connection is not None and connection.id == connection_id
            and connection.allowed_root_id == authority.root_id
        )
        enabled = bool(
            configured and authority.activation_enabled
            and isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= 1
        )
        return {
            "owner": "plugin", "state": "configured" if configured else authority.state,
            "config_revision": revision, "activation_epoch": epoch,
            "activation_enabled": enabled,
            "connection_id": str(connection.id) if configured else None,
            "root_id": connection.allowed_root_id if configured else None,
            "account_mode": connection.account_mode if configured else None,
            "root_state": "verified" if configured else None,
        }
    if authority is not None and not (
        authority.owner == "legacy_kitsusync" and authority.state == "legacy_import_required"
    ):
        return None

    query = select(KitsuSyncDesiredConfiguration).where(
        KitsuSyncDesiredConfiguration.production_id == production_id,
    )
    if lock_legacy:
        query = query.with_for_update()
    desired = session.scalar(query)
    if desired is None:
        return {
            "owner": "legacy_kitsusync" if authority is not None else "plugin",
            "state": "legacy_import_required" if authority is not None else "unconfigured",
            "config_revision": 0, "activation_epoch": 0, "activation_enabled": False,
            "connection_id": None, "root_id": None, "account_mode": None, "root_state": None,
        }
    config = dict(desired.desired_config or {})
    activation = config.get("activation") or {}
    drive = config.get("drive") or {}
    epoch = desired.notification_epoch
    try:
        connection_id = UUID(str(config["connection_id"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        connection_id = None
    connection = selected_storage_connection(session, production_id)
    configured = bool(
        connection_id is not None and connection is not None
        and connection.id == connection_id
        and drive.get("root_state") == "verified"
        and connection.allowed_root_id == drive.get("root_id")
        and connection.account_mode == drive.get("account_mode")
    )
    enabled = bool(
        configured and activation.get("enabled") is True
        and isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= 1
        and activation.get("notification_epoch") == epoch
    )
    return {
        "owner": "legacy_kitsusync", "state": "legacy_import_required",
        "config_revision": desired.config_revision, "activation_epoch": epoch,
        "activation_enabled": enabled,
        "connection_id": str(connection.id) if configured else None,
        "root_id": connection.allowed_root_id if configured else None,
        "account_mode": connection.account_mode if configured else None,
        "root_state": "verified" if configured else None,
    }


def current_configuration_record(session, production_id, *, lock=False):
    """Return the configuration owned by the current per-Production authority.

    Plugin-owned Productions read their durable configuration only from the
    authority row. Legacy rows remain readable only while legacy ownership is
    explicitly in force. The returned view is transient and never persisted.
    """
    from ..models import KitsuSyncDesiredConfiguration, ProductionDriveAuthority

    authority_query = select(ProductionDriveAuthority).where(
        ProductionDriveAuthority.production_id == production_id,
    )
    if lock:
        authority_query = authority_query.with_for_update()
    authority = session.scalar(authority_query)
    if authority is not None and authority.owner == "plugin":
        if not isinstance(authority.desired_config, dict):
            return None
        config = dict(authority.desired_config)
        previous_activation = config.get("activation")
        activated_at = previous_activation.get("activated_at") if isinstance(previous_activation, dict) else None
        config["activation"] = {
            "enabled": bool(authority.activation_enabled),
            "activated_at": activated_at if authority.activation_enabled else None,
            "notification_epoch": authority.activation_epoch,
        }
        return SimpleNamespace(
            production_id=authority.production_id,
            config_revision=authority.config_revision,
            notification_epoch=authority.activation_epoch,
            desired_config=config,
            authority=authority,
        )
    if authority is not None and authority.owner != "legacy_kitsusync":
        return None
    if authority is not None and authority.state != "legacy_import_required":
        return None
    query = select(KitsuSyncDesiredConfiguration).where(
        KitsuSyncDesiredConfiguration.production_id == production_id,
    )
    if lock:
        query = query.with_for_update()
    return session.scalar(query)


def _legacy_selected_storage_connection(session, production_id):
    """Keep current KitsuSync storage live only while import remains pending."""
    from ..models import KitsuSyncDesiredConfiguration, StorageConnection

    desired = session.scalar(select(KitsuSyncDesiredConfiguration).where(
        KitsuSyncDesiredConfiguration.production_id == production_id,
    ))
    if desired is None:
        return None
    config = dict(desired.desired_config)
    drive = config.get("drive") or {}
    try:
        connection_id = UUID(str(config["connection_id"]))
    except (KeyError, TypeError, ValueError):
        return None
    connection = session.scalar(select(StorageConnection).where(
        StorageConnection.id == connection_id,
        StorageConnection.production_id == production_id,
    ))
    if (
        connection is None or connection.id != connection_id
        or connection.production_id != production_id or not connection.enabled
        or connection.provider != "google_drive" or drive.get("root_state") != "verified"
        or connection.allowed_root_id != drive.get("root_id")
        or connection.account_mode != drive.get("account_mode")
    ):
        return None
    if connection.account_mode == "my_drive":
        if not connection.owner_subject or connection.owner_subject != drive.get("owner_subject"):
            return None
    elif connection.account_mode == "shared_drive":
        if not connection.drive_id or connection.drive_id != drive.get("drive_id") or drive.get("owner_subject") is not None:
            return None
    else:
        return None
    return connection


def is_selected_storage_connection(session_factory, production_id, connection_id):
    """Recheck the KitsuSync selection immediately before provider mutations."""
    with session_factory() as session:
        connection = selected_storage_connection(session, production_id)
        return connection is not None and connection.id == connection_id


def production_drive_authority_status(session, production_id):
    """Build a credential-free status response for the KitsuSync consumer."""
    from ..models import (
        DriveCredential,
        KitsuSyncDesiredConfiguration,
        ProductionDriveAuthority,
        StorageConnection,
    )

    authority = session.scalar(select(ProductionDriveAuthority).where(
        ProductionDriveAuthority.production_id == production_id,
    ))
    if authority is None:
        legacy = session.scalar(select(KitsuSyncDesiredConfiguration).where(
            KitsuSyncDesiredConfiguration.production_id == production_id,
        ))
        return {
            "contract_version": "1.0",
            "production_id": str(production_id),
            "owner": "legacy_kitsusync" if legacy is not None else "plugin",
            "state": "legacy_import_required" if legacy is not None else "unconfigured",
            "config_revision": 0,
            "activation_epoch": 0,
            "connection_id": None,
            "root_id": None,
            "account_mode": None,
            "credential_state": "unknown",
            "activation_enabled": False,
        }

    status = {
        "contract_version": "1.0",
        "production_id": str(production_id),
        "owner": authority.owner,
        "state": authority.state,
        "config_revision": authority.config_revision,
        "activation_epoch": authority.activation_epoch,
        "connection_id": None,
        "root_id": None,
        "account_mode": None,
        "credential_state": "unknown",
        "activation_enabled": bool(authority.activation_enabled),
    }
    if authority.owner != "plugin" or authority.state != "configured":
        status["activation_enabled"] = False
        return status

    try:
        connection_id = UUID(str(authority.connection_id))
    except (TypeError, ValueError, AttributeError):
        status["state"] = "blocked"
        status["activation_enabled"] = False
        return status
    connection = session.scalar(select(StorageConnection).where(
        StorageConnection.id == connection_id,
        StorageConnection.production_id == production_id,
    ))
    if (
        connection is None or not connection.enabled
        or connection.allowed_root_id != authority.root_id
        or connection.provider != "google_drive"
    ):
        status["state"] = "blocked"
        status["activation_enabled"] = False
        return status
    credential = session.scalar(select(DriveCredential).where(
        DriveCredential.connection_id == connection_id,
        DriveCredential.production_id == production_id,
    ))
    status.update({
        "connection_id": str(connection.id),
        "root_id": connection.allowed_root_id,
        "account_mode": connection.account_mode,
        "credential_state": credential.state if credential is not None else "missing",
    })
    identity_matches = bool(
        credential is not None
        and (
            (
                connection.account_mode == "my_drive"
                and connection.owner_subject
                and credential.account_subject == connection.owner_subject
                and credential.owner_subject == connection.owner_subject
            )
            or (
                connection.account_mode == "shared_drive"
                and connection.drive_id
                and credential.owner_subject is None
                and credential.drive_id == connection.drive_id
            )
        )
    )
    if not identity_matches:
        status["state"] = "blocked"
        status["activation_enabled"] = False
        return status
    if (
        credential is None or credential.state != "active"
        or isinstance(authority.activation_epoch, bool)
        or not isinstance(authority.activation_epoch, int)
        or authority.activation_epoch < 1
    ):
        status["activation_enabled"] = False
    return status
