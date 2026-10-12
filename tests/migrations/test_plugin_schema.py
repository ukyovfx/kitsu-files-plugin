from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "plugin/migrations/versions/0001_plugin_foundation.py"
FOLDER_BINDING_MIGRATION = ROOT / "plugin/migrations/versions/0005_unique_folder_provider_binding.py"
PUBLISH_SOURCE_MIGRATION = ROOT / "plugin/migrations/versions/0006_publish_source_and_destination.py"


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


def test_provider_folder_identity_index_is_partial_and_reversible():
    source = FOLDER_BINDING_MIGRATION.read_text()
    assert 'revision = "vxf_0005"' in source
    assert 'down_revision = "vxf_0004"' in source
    assert '"plugin_vfx_files_folder_provider_id_uc"' in source
    assert 'postgresql_where=sa.text("provider_folder_id IS NOT NULL")' in source
    downgrade = source.split("def downgrade():", 1)[1]
    assert 'op.drop_index("plugin_vfx_files_folder_provider_id_uc"' in downgrade
    assert "schema_epoch = 3" in downgrade


def test_publish_source_and_destination_migration_is_plugin_owned_and_reversible():
    source = PUBLISH_SOURCE_MIGRATION.read_text()
    assert 'revision = "vxf_0006"' in source
    assert 'down_revision = "vxf_0005"' in source
    assert '"plugin_vfx_files_output_family"' in source
    assert '"publish_folder_id"' in source
    assert '"source_revision_file_id"' in source
    assert 'sa.ForeignKey("plugin_vfx_files_working_revision_file.id")' in source
    assert "schema_epoch = 5" in source
    downgrade = source.split("def downgrade():", 1)[1]
    assert 'op.drop_column("plugin_vfx_files_publish", "source_revision_file_id")' in downgrade
    assert 'op.drop_column("plugin_vfx_files_output_family", "publish_folder_id")' in downgrade
    assert "schema_epoch = 4" in downgrade
