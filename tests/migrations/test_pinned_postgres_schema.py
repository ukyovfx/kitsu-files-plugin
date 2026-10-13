"""Runtime schema checks executed only against the disposable pinned host."""

from sqlalchemy import inspect, text


def test_vxf_0011_to_0013_are_installed_on_pinned_postgres(zou_app):
    from zou.app import db

    with zou_app.app_context():
        schema = inspect(db.engine)
        assert db.session.execute(
            text("SELECT schema_epoch FROM plugin_vfx_files_state WHERE id = 1")
        ).scalar_one() == 12
        expected_tables = {
            "plugin_vfx_files_kitsusync_desired_config",
            "plugin_vfx_files_kitsusync_management_idempotency",
            "plugin_vfx_files_kitsusync_snapshot",
            "plugin_vfx_files_kitsusync_snapshot_page",
            "plugin_vfx_files_kitsusync_sync_preview",
            "plugin_vfx_files_drive_credential",
            "plugin_vfx_files_drive_authorization_intent",
        }
        assert all(schema.has_table(table) for table in expected_tables)
