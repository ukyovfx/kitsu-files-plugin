import sys
from types import SimpleNamespace
from uuid import uuid4


def _authority_resolver():
    if "zou.app" not in sys.modules:
        try:
            __import__("zou.app")
        except ModuleNotFoundError:
            from flask_sqlalchemy import SQLAlchemy

            zou_module = sys.modules.setdefault("zou", __import__("types").ModuleType("zou"))
            zou_module.__path__ = []
            app_module = __import__("types").ModuleType("zou.app")
            app_module.db = SQLAlchemy()
            sys.modules["zou.app"] = app_module
    from plugin.integrations.storage_authority import selected_storage_connection
    return selected_storage_connection


def test_plugin_authority_selects_its_verified_root_without_kitsusync_config():
    selected_storage_connection = _authority_resolver()

    production_id, connection_id = uuid4(), uuid4()
    authority = SimpleNamespace(
        production_id=production_id,
        owner="plugin",
        state="configured",
        config_revision=1,
        connection_id=connection_id,
        root_id="plugin-root",
    )
    credential = SimpleNamespace(
        state="active",
        account_mode="my_drive",
        account_subject="verified-owner",
        owner_subject="verified-owner",
        drive_id=None,
    )
    connection = SimpleNamespace(
        id=connection_id,
        production_id=production_id,
        enabled=True,
        provider="google_drive",
        allowed_root_id="plugin-root",
        account_mode="my_drive",
        owner_subject="verified-owner",
        drive_id=None,
    )

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if model.__name__ == "ProductionDriveAuthority":
                return authority
            if model.__name__ == "DriveCredential":
                return credential
            return connection

    assert selected_storage_connection(Session(), production_id) is connection


def test_plugin_authority_rejects_revoked_or_wrong_owner_credentials():
    selected_storage_connection = _authority_resolver()
    production_id, connection_id = uuid4(), uuid4()
    authority = SimpleNamespace(
        production_id=production_id,
        owner="plugin",
        state="configured",
        config_revision=1,
        connection_id=connection_id,
        root_id="plugin-root",
    )
    connection = SimpleNamespace(
        id=connection_id,
        production_id=production_id,
        enabled=True,
        provider="google_drive",
        allowed_root_id="plugin-root",
        account_mode="my_drive",
        owner_subject="verified-owner",
        drive_id=None,
    )
    credential = SimpleNamespace(
        state="revoked",
        account_mode="my_drive",
        account_subject="verified-owner",
        owner_subject="verified-owner",
        drive_id=None,
    )

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if model.__name__ == "ProductionDriveAuthority":
                return authority
            if model.__name__ == "DriveCredential":
                return credential
            return connection

    session = Session()
    assert selected_storage_connection(session, production_id) is None
    credential.state = "active"
    credential.owner_subject = "different-owner"
    assert selected_storage_connection(session, production_id) is None


def test_legacy_authority_is_not_silently_promoted_to_plugin_owner():
    selected_storage_connection = _authority_resolver()

    production_id = uuid4()
    authority = SimpleNamespace(
        production_id=production_id,
        owner="legacy_kitsusync",
        state="legacy_import_required",
        connection_id=None,
        root_id=None,
    )
    legacy = SimpleNamespace(
        desired_config={
            "connection_id": str(uuid4()),
            "drive": {
                "account_mode": "my_drive",
                "owner_subject": "verified-owner",
                "root_id": "legacy-root",
                "root_state": "verified",
            },
        },
    )
    connection = SimpleNamespace(
        id=uuid4(),
        production_id=production_id,
        enabled=True,
        provider="google_drive",
        allowed_root_id="legacy-root",
        account_mode="my_drive",
        owner_subject="verified-owner",
        drive_id=None,
    )
    legacy.desired_config["connection_id"] = str(connection.id)

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if model.__name__ == "ProductionDriveAuthority":
                return authority
            if model.__name__ == "KitsuSyncDesiredConfiguration":
                return legacy
            return connection

    assert selected_storage_connection(Session(), production_id) is connection
    assert authority.owner == "legacy_kitsusync"
    assert authority.state == "legacy_import_required"


def test_plugin_authority_status_is_sanitized_and_reports_legacy_gate():
    from plugin.integrations.storage_authority import production_drive_authority_status

    production_id, connection_id = uuid4(), uuid4()
    authority = SimpleNamespace(
        production_id=production_id,
        owner="plugin",
        state="configured",
        config_revision=4,
        activation_epoch=9,
        connection_id=connection_id,
        root_id="plugin-root",
        activation_enabled=False,
    )
    connection = SimpleNamespace(
        id=connection_id,
        production_id=production_id,
        enabled=True,
        provider="google_drive",
        allowed_root_id="plugin-root",
        account_mode="my_drive",
        owner_subject="must-not-be-returned",
        drive_id=None,
    )
    credential = SimpleNamespace(state="active")

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if model.__name__ == "ProductionDriveAuthority":
                return authority
            if model.__name__ == "DriveCredential":
                return credential
            return connection

    result = production_drive_authority_status(Session(), production_id)
    assert result == {
        "contract_version": "1.0",
        "production_id": str(production_id),
        "owner": "plugin",
        "state": "configured",
        "config_revision": 4,
        "activation_epoch": 9,
        "connection_id": str(connection_id),
        "root_id": "plugin-root",
        "account_mode": "my_drive",
        "credential_state": "active",
        "activation_enabled": False,
    }
    assert "owner_subject" not in result
    assert "ciphertext" not in result
    assert "access_token" not in result
