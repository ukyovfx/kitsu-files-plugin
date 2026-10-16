import sys
from types import SimpleNamespace
from uuid import uuid4


def _legacy_rows(*, connection_match=True, template_match=True, credential_state="active"):
    production_id, connection_id = uuid4(), uuid4()
    root_id = "authorized-root-01"
    legacy = SimpleNamespace(
        production_id=production_id,
        config_revision=7,
        notification_epoch=11,
        desired_config={
            "contract_version": "1.0",
            "connection_id": str(connection_id),
            "drive": {
                "account_mode": "my_drive",
                "owner_subject": "google-subject-01",
                "root_id": root_id,
                "root_state": "verified",
            },
            "template_selection": {
                "selections": [{
                    "scope": "production",
                    "template_id": "vfx-files.production.default",
                    "revision": 1,
                    "overrides": {},
                }],
            },
            "sync_policy": {
                "mode": "new_production",
                "scope": {"episodes": False, "sequences": False, "shots": False, "assets": False, "tasks": False},
                "notify_new_deliveries": False,
            },
            "activation": {"enabled": True, "notification_epoch": 11},
        },
    )
    connection = SimpleNamespace(
        id=connection_id if connection_match else uuid4(),
        production_id=production_id,
        enabled=True,
        provider="google_drive",
        allowed_root_id=root_id,
        account_mode="my_drive",
        owner_subject="google-subject-01",
        drive_id=None,
    )
    template = SimpleNamespace(
        storage_connection_id=connection_id,
        allowed_root_id=root_id if template_match else "different-root",
        version=3,
        enabled=True,
    )
    credential = credential_state

    class Session:
        writes = 0

        def scalar(self, statement):
            description = statement.column_descriptions[0]
            entity = description["entity"]
            name = getattr(entity, "__name__", "")
            if name == "ProductionDriveAuthority":
                return SimpleNamespace(owner="legacy_kitsusync", state="legacy_import_required")
            if name == "KitsuSyncDesiredConfiguration":
                return legacy
            if name == "StorageConnection":
                return connection
            if name == "SharedTemplateConfiguration":
                return template
            if description["name"] == "state":
                return credential
            return None

        def add(self, *_args, **_kwargs):
            self.writes += 1
            raise AssertionError("preview must not write")

        def flush(self, *_args, **_kwargs):
            self.writes += 1
            raise AssertionError("preview must not write")

    return production_id, Session()


def _preview_importer():
    # Model-only tests can run without booting Zou. Production uses Zou's
    # Flask-SQLAlchemy registry; the pinned-host suite exercises that registry.
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
    from plugin.integrations.legacy_import import preview_legacy_import
    return preview_legacy_import


def test_legacy_import_preview_is_non_mutating_and_requires_fresh_activation():
    preview_legacy_import = _preview_importer()
    production_id, session = _legacy_rows()
    result = preview_legacy_import(session, production_id)

    assert result["status"] == "ready_for_explicit_review", result
    assert result["source_activation_enabled"] is True
    assert result["source_notification_epoch"] == 11
    assert result["proposed_activation_enabled"] is False
    assert result["proposed_notification_epoch"] == 12
    assert result["requires_explicit_admin_confirmation"] is True
    assert result["root_verification"] == "stored_identity_match_only"
    assert result["credentials_decrypted"] is False
    assert result["writes_performed"] is False
    assert result["drive_operations_performed"] is False
    assert session.writes == 0
    assert "ciphertext" not in result
    assert "access_token" not in result


def test_legacy_import_preview_blocks_connection_or_root_mismatch():
    preview_legacy_import = _preview_importer()
    production_id, session = _legacy_rows(connection_match=False)
    result = preview_legacy_import(session, production_id)

    assert result["status"] == "blocked"
    assert result["reason"] == "connection_or_verified_root_mismatch"
    assert result["writes_performed"] is False


def test_legacy_import_preview_blocks_template_and_revoked_credential():
    preview_legacy_import = _preview_importer()
    production_id, session = _legacy_rows(template_match=False)
    assert preview_legacy_import(session, production_id)["reason"] == "execution_template_mismatch"

    production_id, session = _legacy_rows(credential_state="revoked")
    assert preview_legacy_import(session, production_id)["reason"] == "credential_not_active"


def test_legacy_import_preview_reports_no_source_without_writes():
    from plugin.integrations.legacy_import import preview_legacy_import

    class EmptySession:
        writes = 0

        def scalar(self, _statement):
            return None

    session = EmptySession()
    result = preview_legacy_import(session, uuid4())

    assert result["status"] == "no_legacy_configuration"
    assert result["writes_performed"] is False
    assert result["drive_operations_performed"] is False
    assert session.writes == 0
