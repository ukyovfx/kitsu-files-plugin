from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "plugin/migrations/versions/0001_plugin_foundation.py"


def test_plugin_tables_are_namespaced_and_nullable_folder_identity_is_avoided():
    source = MIGRATION.read_text()
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))
    assert created
    assert all(name.startswith("plugin_vfx_files_") for name in created)
    assert "plugin_vfx_files_folder_identity_uc" in source
    assert 'sa.Column("storage_connection_id", _uuid(), sa.ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False)' in source
    assert 'sa.Column("allowed_root_id", sa.String(255), nullable=False)' in source
    assert 'sa.Column("canonical_scope_key", sa.String(512), nullable=False)' in source


def test_downgrade_drops_tables_in_reverse_dependency_order():
    source = MIGRATION.read_text().split("def downgrade():", 1)[1]
    dropped = re.findall(r'op\.drop_table\("([^"]+)"\)', source)
    assert dropped[0] == "plugin_vfx_files_reconciliation_finding"
    assert dropped[-1] == "plugin_vfx_files_state"
    assert len(dropped) == len(set(dropped))


def test_audit_history_and_publish_checkpoints_are_database_immutable():
    source = MIGRATION.read_text()
    assert "plugin_vfx_files_reject_immutable_mutation" in source
    for table in (
        "plugin_vfx_files_publish_checkpoint",
        "plugin_vfx_files_native_create_event",
        "plugin_vfx_files_approval_event",
        "plugin_vfx_files_pinned_dependency",
        "plugin_vfx_files_handoff_event",
    ):
        assert table in source
    assert 'f"CREATE TRIGGER {table_name}_immutable' in source
