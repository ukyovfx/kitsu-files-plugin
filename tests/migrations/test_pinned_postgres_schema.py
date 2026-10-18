"""Runtime schema checks executed only against the disposable pinned host."""

from sqlalchemy import inspect, text


def test_vxf_0011_to_0019_and_runtime_compatibility_are_installed_on_pinned_postgres(zou_app):
    from plugin.authz import assert_compatible_host
    from plugin.compat import HOST_ZOU_VERSION, SCHEMA_EPOCH, WORKER_BUILD, assert_runtime_host
    from plugin.models import PluginState
    from zou.app import db

    with zou_app.app_context():
        schema = inspect(db.engine)
        installed_epoch = db.session.execute(
            text("SELECT schema_epoch FROM plugin_vfx_files_state WHERE id = 1")
        ).scalar_one()
        assert installed_epoch == 18
        assert installed_epoch == SCHEMA_EPOCH
        assert_runtime_host()
        assert_compatible_host(HOST_ZOU_VERSION, installed_epoch, WORKER_BUILD)
        assert db.session.query(PluginState).filter_by(id=1).one().schema_epoch == installed_epoch
        expected_tables = {
            "plugin_vfx_files_kitsusync_desired_config",
            "plugin_vfx_files_kitsusync_management_idempotency",
            "plugin_vfx_files_kitsusync_snapshot",
            "plugin_vfx_files_kitsusync_snapshot_page",
            "plugin_vfx_files_kitsusync_sync_preview",
            "plugin_vfx_files_drive_credential",
            "plugin_vfx_files_drive_authorization_intent",
            "plugin_vfx_files_production_drive_authority",
            "plugin_vfx_files_production_drive_authority_event",
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
