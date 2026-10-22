"""Runtime schema checks executed only against the disposable pinned host."""

from sqlalchemy import inspect, text


def test_vxf_0011_to_0021_and_runtime_compatibility_are_installed_on_pinned_postgres(zou_app):
    from plugin.authz import assert_compatible_host
    from plugin.compat import HOST_ZOU_VERSION, SCHEMA_EPOCH, WORKER_BUILD, assert_runtime_host
    from plugin.models import PluginState
    from zou.app import db

    with zou_app.app_context():
        schema = inspect(db.engine)
        installed_epoch = db.session.execute(
            text("SELECT schema_epoch FROM plugin_vfx_files_state WHERE id = 1")
        ).scalar_one()
        assert installed_epoch == 22
        assert installed_epoch == SCHEMA_EPOCH
        assert_runtime_host()
        assert_compatible_host(HOST_ZOU_VERSION, installed_epoch, WORKER_BUILD)
        assert db.session.query(PluginState).filter_by(id=1).one().schema_epoch == installed_epoch
        expected_tables = {
            "plugin_vfx_files_google_user_credential",
            "plugin_vfx_files_google_user_authorization_intent",
            "plugin_vfx_files_output_package_deletion",
            "plugin_vfx_files_production_output_setting",
            "plugin_vfx_files_kitsusync_desired_config",
            "plugin_vfx_files_kitsusync_management_idempotency",
            "plugin_vfx_files_kitsusync_snapshot",
            "plugin_vfx_files_kitsusync_snapshot_page",
            "plugin_vfx_files_kitsusync_sync_preview",
            "plugin_vfx_files_drive_credential",
            "plugin_vfx_files_drive_authorization_intent",
            "plugin_vfx_files_production_drive_authority",
            "plugin_vfx_files_production_drive_authority_event",
            "plugin_vfx_files_link_share",
            "plugin_vfx_files_link_share_target",
        }
        assert all(schema.has_table(table) for table in expected_tables)
        assert "storage_connection_id" in {
            column["name"] for column in schema.get_columns("plugin_vfx_files_kitsusync_event_outbox")
        }
        assert "event_activation_epoch" in {
            column["name"] for column in schema.get_columns("plugin_vfx_files_output_package")
        }
        assert "return_surface" in {
            column["name"] for column in schema.get_columns("plugin_vfx_files_drive_authorization_intent")
        }
        assert "activation_transition_pending" in {
            column["name"] for column in schema.get_columns("plugin_vfx_files_production_drive_authority")
        }


def test_m4_source_kind_upgrade_and_guarded_downgrade_on_temporary_tables(zou_app):
    """Exercise DDL on connection-local shadows; never alter installed plugin data."""
    import importlib.util
    from pathlib import Path
    import pytest
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import IntegrityError
    from zou.app import db

    path = Path(__file__).resolve().parents[2] / "plugin/migrations/versions/0023_output_package_source_kind.py"
    spec = importlib.util.spec_from_file_location("m4_source_kind_rehearsal", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with zou_app.app_context(), db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE TEMP TABLE plugin_vfx_files_output_package (id integer PRIMARY KEY) ON COMMIT DROP"))
            connection.execute(text("CREATE TEMP TABLE plugin_vfx_files_state (id integer PRIMARY KEY, schema_epoch integer) ON COMMIT DROP"))
            for table in ("plugin_vfx_files_output_package", "plugin_vfx_files_state"):
                assert connection.scalar(text("SELECT relnamespace = pg_my_temp_schema() FROM pg_class WHERE oid = to_regclass(:name)"), {"name": table})
            connection.execute(text("INSERT INTO pg_temp.plugin_vfx_files_output_package VALUES (1)"))
            connection.execute(text("INSERT INTO pg_temp.plugin_vfx_files_state VALUES (1, 21)"))
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            assert connection.scalar(text("SELECT source_kind FROM pg_temp.plugin_vfx_files_output_package WHERE id=1")) == "production"
            assert connection.scalar(text("SELECT schema_epoch FROM pg_temp.plugin_vfx_files_state WHERE id=1")) == 22
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text("INSERT INTO pg_temp.plugin_vfx_files_output_package VALUES (2, 'invalid')"))
            connection.execute(text("INSERT INTO pg_temp.plugin_vfx_files_output_package VALUES (2, 'personal')"))
            with pytest.raises(RuntimeError, match="consistent snapshot"):
                migration.downgrade()
            assert connection.scalar(text("SELECT source_kind FROM pg_temp.plugin_vfx_files_output_package WHERE id=2")) == "personal"
            connection.execute(text("DELETE FROM pg_temp.plugin_vfx_files_output_package WHERE id=2"))
            migration.downgrade()
            assert connection.scalar(text("SELECT schema_epoch FROM pg_temp.plugin_vfx_files_state WHERE id=1")) == 21
            migration.upgrade()
            assert connection.scalar(text("SELECT source_kind FROM pg_temp.plugin_vfx_files_output_package WHERE id=1")) == "production"
        finally:
            transaction.rollback()
