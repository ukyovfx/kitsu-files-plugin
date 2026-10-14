from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "plugin/migrations/versions/0001_plugin_foundation.py"
FOLDER_BINDING_MIGRATION = ROOT / "plugin/migrations/versions/0005_unique_folder_provider_binding.py"
PUBLISH_SOURCE_MIGRATION = ROOT / "plugin/migrations/versions/0006_publish_source_and_destination.py"
TASK_OUTPUT_MIGRATION = ROOT / "plugin/migrations/versions/0007_task_outputs_and_department_handoffs.py"
OUTPUT_PACKAGE_QC_MIGRATION = ROOT / "plugin/migrations/versions/0008_output_package_qc.py"
KITSUSYNC_DELIVERY_MIGRATION = ROOT / "plugin/migrations/versions/0009_kitsusync_contract_and_package_deliveries.py"
DRIVE_OBSERVATION_MIGRATION = ROOT / "plugin/migrations/versions/0010_drive_reconciliation_observations.py"
KITSUSYNC_IDEMPOTENCY_MIGRATION = ROOT / "plugin/migrations/versions/0011_kitsusync_management_idempotency.py"
KITSUSYNC_SYNC_MIGRATION = ROOT / "plugin/migrations/versions/0012_kitsusync_hierarchy_sync.py"
DRIVE_OAUTH_MIGRATION = ROOT / "plugin/migrations/versions/0013_encrypted_drive_oauth.py"
EVENT_OUTBOX_BINDING_MIGRATION = ROOT / "plugin/migrations/versions/0014_event_outbox_connection_binding.py"
PACKAGE_PUBLICATION_MIGRATION = ROOT / "plugin/migrations/versions/0015_package_publication_binding.py"


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


def test_task_outputs_migration_keeps_department_targets_and_latest_pointers_separate():
    source = TASK_OUTPUT_MIGRATION.read_text()
    assert 'revision = "vxf_0007"' in source
    assert 'down_revision = "vxf_0006"' in source
    assert "recipient_department_id" in source and "recipient_task_id" in source
    assert "recipient_person_id" in source
    assert "plugin_vfx_files_output_package_version_uc" in source
    for pointer in ("latest_output_id", "latest_published_id", "latest_approved_id", "latest_final_delivery_id"):
        assert pointer in source
    assert "provider_object_id" in source and "frame_number" in source and "sha256" in source
    assert "plugin_vfx_files_output_package_file_immutable" in source
    assert "plugin_vfx_files_output_package_immutable" in source
    assert "schema_epoch = 6" in source
    # Downgrade drops the new package data; supported recovery is snapshot restore.
    downgrade = source.split("def downgrade():", 1)[1]
    assert 'op.drop_table("plugin_vfx_files_output_package")' in downgrade
    assert "has_package_rows or has_team_handoff_rows" in downgrade
    assert "restore a consistent pre-migration snapshot" in downgrade
    assert "schema_epoch = 5" in downgrade


def test_output_package_qc_is_plugin_owned_and_downgrade_fails_closed_with_data():
    source = OUTPUT_PACKAGE_QC_MIGRATION.read_text()
    assert 'revision = "vxf_0008"' in source
    assert 'down_revision = "vxf_0007"' in source
    assert 'sa.Column("qc_state", sa.String(24), nullable=False, server_default="pending")' in source
    assert 'sa.Column("qc_report", postgresql.JSONB(), nullable=True)' in source
    assert source.count('sa.Column("provider_folder_id", sa.String(255), nullable=True)') == 1
    assert source.count('sa.Column("source_provider_object_id", sa.String(255), nullable=True)') == 1
    assert source.count('op.drop_column("plugin_vfx_files_output_package_file", "source_provider_object_id")') == 1
    upgrade = source.split("def downgrade():", 1)[0]
    assert upgrade.index("DROP TRIGGER plugin_vfx_files_output_package_file_immutable") < upgrade.index(
        "UPDATE plugin_vfx_files_output_package_file SET source_provider_object_id"
    )
    assert '"CREATE TRIGGER plugin_vfx_files_output_package_file_immutable "' in upgrade
    assert 'op.alter_column("plugin_vfx_files_output_package_file", "sha256", existing_type=sa.String(64), nullable=True)' in source
    assert "provider-readback digest" in source
    assert "CREATE OR REPLACE FUNCTION plugin_vfx_files_reject_output_file_mutation()" in source
    assert "schema_epoch = 7" in source
    downgrade = source.split("def downgrade():", 1)[1]
    assert "has_package_rows" in downgrade
    assert "restore a consistent pre-migration snapshot" in downgrade
    assert "schema_epoch = 6" in downgrade


def test_kitsusync_contract_and_multi_recipient_migration_is_additive_and_fail_closed():
    source = KITSUSYNC_DELIVERY_MIGRATION.read_text()
    assert 'revision = "vxf_0009"' in source
    assert 'down_revision = "vxf_0008"' in source
    for table in (
        "plugin_vfx_files_kitsusync_desired_config",
        "plugin_vfx_files_kitsusync_snapshot",
        "plugin_vfx_files_kitsusync_snapshot_page",
        "plugin_vfx_files_kitsusync_event_outbox",
        "plugin_vfx_files_output_package_delivery",
        "plugin_vfx_files_output_package_delivery_event",
    ):
        assert f'op.create_table(\n        "{table}"' in source
    assert "page_count BETWEEN 1 AND 1000" in source
    assert "object_count BETWEEN 1 AND 500000" in source
    assert "KitsuSync event identity and payload are immutable" in source
    assert "Output Package delivery history is immutable" in source
    assert "schema_epoch = 8" in source
    downgrade = source.split("def downgrade():", 1)[1]
    assert "restore a consistent pre-migration snapshot" in downgrade
    assert "schema_epoch = 7" in downgrade


def test_drive_observation_migration_is_additive_and_refuses_data_loss():
    source = DRIVE_OBSERVATION_MIGRATION.read_text()
    assert 'revision = "vxf_0010"' in source
    assert 'down_revision = "vxf_0009"' in source
    assert 'op.create_table(\n        "plugin_vfx_files_drive_observation"' in source
    for state in ("present", "missing", "modified", "inaccessible", "stale"):
        assert state in source
    assert "restore a consistent pre-migration snapshot instead of downgrading" in source
    assert "schema_epoch = 9" in source and "schema_epoch = 8" in source


def test_kitsusync_management_idempotency_migration_is_additive_and_refuses_data_loss():
    source = KITSUSYNC_IDEMPOTENCY_MIGRATION.read_text()
    assert 'revision = "vxf_0011"' in source
    assert 'down_revision = "vxf_0010"' in source
    assert 'op.create_table(\n        "plugin_vfx_files_kitsusync_management_idempotency"' in source
    assert 'UniqueConstraint("production_id", "idempotency_key"' in source
    assert "restore a consistent pre-migration snapshot instead of downgrading" in source
    assert "schema_epoch = 10" in source and "schema_epoch = 9" in source


def test_kitsusync_hierarchy_sync_migration_allows_repeated_content_and_preserves_evidence():
    source = KITSUSYNC_SYNC_MIGRATION.read_text()
    assert 'revision = "vxf_0012"' in source
    assert 'down_revision = "vxf_0011"' in source
    assert 'op.drop_constraint(' in source
    assert 'op.create_table(\n        "plugin_vfx_files_kitsusync_sync_preview"' in source
    assert 'sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)' in source
    assert 'SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_job)' in source
    assert "GROUP BY production_id, config_revision, hierarchy_sha256 HAVING count(*) > 1" in source
    assert "restore a consistent pre-migration snapshot instead of downgrading" in source
    assert "schema_epoch = 11" in source and "schema_epoch = 10" in source


def test_encrypted_drive_oauth_migration_is_additive_and_refuses_data_loss():
    source = DRIVE_OAUTH_MIGRATION.read_text()
    assert 'revision = "vxf_0013"' in source
    assert 'down_revision = "vxf_0012"' in source
    assert 'op.create_table(\n        "plugin_vfx_files_drive_credential"' in source
    assert 'op.create_table(\n        "plugin_vfx_files_drive_authorization_intent"' in source
    assert 'sa.Column("ciphertext", sa.LargeBinary(), nullable=True)' in source
    assert 'sa.Column("state_sha256", sa.String(64), nullable=False)' in source
    assert "owner_subject = account_subject" in source
    assert "state IN ('active','revoking','revoke_pending','revoked','reauth_required')" in source
    assert "restore a consistent snapshot instead of downgrading" in source
    assert "schema_epoch = 12" in source


def test_event_outbox_binding_migration_is_additive_and_preserves_bound_rows():
    source = EVENT_OUTBOX_BINDING_MIGRATION.read_text()
    assert 'revision = "vxf_0014"' in source
    assert 'down_revision = "vxf_0013"' in source
    assert 'op.add_column(' in source
    assert '"storage_connection_id"' in source
    assert 'ForeignKey("plugin_vfx_files_storage_connection.id")' in source
    assert 'nullable=True' in source
    assert "schema_epoch = 13" in source and "schema_epoch = 12" in source
    assert "WHERE storage_connection_id IS NOT NULL" in source
    assert "event_activation_epoch IS NOT NULL" in source
    assert '"plugin_vfx_files_output_package"' in source
    assert '"event_activation_epoch"' in source
    assert "restore a consistent snapshot instead of downgrading" in source
    assert "Output Package event eligibility is immutable" in source


def test_package_publication_binding_is_additive_and_immutable():
    source = PACKAGE_PUBLICATION_MIGRATION.read_text()
    assert 'revision = "vxf_0015"' in source
    assert 'down_revision = "vxf_0014"' in source
    assert '"plugin_vfx_files_output_package_publication"' in source
    assert 'sa.ForeignKey("plugin_vfx_files_output_package.id")' in source
    assert 'sa.ForeignKey("plugin_vfx_files_publish.id")' in source
    assert 'UniqueConstraint("package_id"' in source
    assert 'UniqueConstraint("publish_id"' in source
    assert "Output Package publication links are immutable" in source
    assert "restore a consistent snapshot instead of downgrading" in source
    assert "schema_epoch = 14" in source and "schema_epoch = 13" in source
