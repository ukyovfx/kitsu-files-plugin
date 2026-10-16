"""Resolve a Production Drive connection through its explicit owner record."""

from uuid import UUID

from sqlalchemy import select


def selected_storage_connection(session, production_id):
    """Return the exact owner-selected root, preserving legacy rows for review."""
    from ..models import ProductionDriveAuthority, StorageConnection

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
            if connection.account_mode == "my_drive":
                return connection if connection.owner_subject else None
            if connection.account_mode == "shared_drive":
                return connection if connection.drive_id else None
            return None
        if authority.owner != "legacy_kitsusync" or authority.state != "legacy_import_required":
            return None

    return _legacy_selected_storage_connection(session, production_id)


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
