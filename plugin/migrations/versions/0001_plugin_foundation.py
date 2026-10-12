"""Create the plugin-owned foundation tables.

Revision ID: vxf_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0001"
down_revision = None
branch_labels = None
depends_on = None


def _uuid():
    return postgresql.UUID(as_uuid=False)


def upgrade():
    op.create_table(
        "plugin_vfx_files_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("schema_epoch", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.bulk_insert(
        sa.table("plugin_vfx_files_state", sa.column("id", sa.Integer()), sa.column("schema_epoch", sa.Integer())),
        [{"id": 1, "schema_epoch": 1}],
    )
    op.create_table(
        "plugin_vfx_files_storage_connection",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("production_id", _uuid(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("account_mode", sa.String(24), nullable=False),
        sa.Column("owner_subject", sa.String(255)),
        sa.Column("drive_id", sa.String(255)),
        sa.Column("allowed_root_id", sa.String(255), nullable=False),
        sa.Column("credential_ref", sa.String(255), nullable=False),
        sa.Column("max_file_bytes", sa.BigInteger(), nullable=False, server_default=str(10 * 1024 * 1024 * 1024)),
        sa.Column("quota_bytes", sa.BigInteger(), nullable=False, server_default=str(100 * 1024 * 1024 * 1024)),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("plugin_vfx_files_storage_production_ix", "plugin_vfx_files_storage_connection", ["production_id"])
    op.create_table(
        "plugin_vfx_files_folder_binding",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("storage_connection_id", _uuid(), sa.ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False),
        sa.Column("allowed_root_id", sa.String(255), nullable=False),
        sa.Column("canonical_scope_key", sa.String(512), nullable=False),
        sa.Column("parent_provider_id", sa.String(255), nullable=False),
        sa.Column("provider_folder_id", sa.String(255)),
        sa.Column("state", sa.String(32), nullable=False, server_default="reserved"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("storage_connection_id", "allowed_root_id", "canonical_scope_key", name="plugin_vfx_files_folder_identity_uc"),
    )
    op.create_table(
        "plugin_vfx_files_upload_session",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("actor_id", _uuid(), nullable=False),
        sa.Column("storage_connection_id", _uuid(), sa.ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="receiving"),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("expected_sha256", sa.String(64), nullable=False),
        sa.Column("received_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("staging_key", sa.String(512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("plugin_vfx_files_upload_task_ix", "plugin_vfx_files_upload_session", ["task_id"])
    op.create_table(
        "plugin_vfx_files_upload_part",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("session_id", _uuid(), sa.ForeignKey("plugin_vfx_files_upload_session.id"), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("byte_start", sa.BigInteger(), nullable=False),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("staging_key", sa.String(512), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="reserved"),
        sa.UniqueConstraint("session_id", "part_index", name="plugin_vfx_files_upload_part_uc"),
    )
    op.create_table(
        "plugin_vfx_files_job",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_owner", _uuid()),
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("plugin_vfx_files_job_claim_ix", "plugin_vfx_files_job", ["state", "available_at", "lease_expires_at"])
    op.create_table(
        "plugin_vfx_files_output_family",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("production_id", _uuid(), nullable=False),
        sa.Column("family_key", sa.String(128), nullable=False),
        sa.Column("output_type_id", _uuid(), nullable=False),
        sa.Column("task_type_id", _uuid(), nullable=False),
        sa.Column("name", sa.String(250), nullable=False),
        sa.Column("representation", sa.String(20), nullable=False, server_default=""),
        sa.Column("extension", sa.String(10), nullable=False, server_default=""),
        sa.Column("file_status_id", _uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("production_id", "family_key", name="plugin_vfx_files_output_family_key_uc"),
    )
    op.create_index("plugin_vfx_files_output_family_production_ix", "plugin_vfx_files_output_family", ["production_id"])
    op.create_table(
        "plugin_vfx_files_publish",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("output_family_id", _uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), unique=True),
        sa.Column("source_working_file_id", _uuid()),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="reserved"),
        sa.Column("byte_count", sa.BigInteger()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("storage_connection_id", _uuid(), sa.ForeignKey("plugin_vfx_files_storage_connection.id")),
        sa.Column("provider_artifact_id", sa.String(255)),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("manifest_bytes", sa.LargeBinary()),
        sa.Column("native_output_file_id", _uuid()),
        sa.Column("native_revision", sa.Integer()),
        sa.Column("native_create_permit_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("native_retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qc_state", sa.String(24)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_id", "output_family_id", "version", name="plugin_vfx_files_publish_version_uc"),
    )
    op.create_index("plugin_vfx_files_publish_task_ix", "plugin_vfx_files_publish", ["task_id"])
    op.create_table(
        "plugin_vfx_files_publish_pointer",
        sa.Column("task_id", _uuid(), primary_key=True),
        sa.Column("output_family_id", _uuid(), primary_key=True),
        sa.Column("latest_published_id", _uuid(), sa.ForeignKey("plugin_vfx_files_publish.id")),
        sa.Column("latest_approved_id", _uuid(), sa.ForeignKey("plugin_vfx_files_publish.id")),
    )
    op.create_table(
        "plugin_vfx_files_publish_checkpoint",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("publish_id", _uuid(), sa.ForeignKey("plugin_vfx_files_publish.id"), nullable=False),
        sa.Column("checkpoint", sa.String(32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("publish_id", "checkpoint", name="plugin_vfx_files_publish_checkpoint_uc"),
    )
    op.create_index("plugin_vfx_files_publish_checkpoint_ix", "plugin_vfx_files_publish_checkpoint", ["publish_id"])
    op.create_table(
        "plugin_vfx_files_native_create_event",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("publish_id", _uuid(), sa.ForeignKey("plugin_vfx_files_publish.id"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("actor_id", _uuid()),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("plugin_vfx_files_native_create_event_ix", "plugin_vfx_files_native_create_event", ["publish_id"])
    op.create_table(
        "plugin_vfx_files_approval_event",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("publish_id", _uuid(), sa.ForeignKey("plugin_vfx_files_publish.id"), nullable=False),
        sa.Column("actor_id", _uuid(), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("reverses_event_id", _uuid(), sa.ForeignKey("plugin_vfx_files_approval_event.id")),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("policy_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("plugin_vfx_files_approval_publish_ix", "plugin_vfx_files_approval_event", ["publish_id"])
    op.create_table(
        "plugin_vfx_files_pinned_dependency",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("publish_id", _uuid(), sa.ForeignKey("plugin_vfx_files_publish.id"), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("plugin_vfx_files_pin_task_ix", "plugin_vfx_files_pinned_dependency", ["task_id"])
    op.create_table(
        "plugin_vfx_files_handoff",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("publish_id", _uuid(), sa.ForeignKey("plugin_vfx_files_publish.id"), nullable=False),
        sa.Column("sender_id", _uuid(), nullable=False),
        sa.Column("recipient_id", _uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("plugin_vfx_files_handoff_task_ix", "plugin_vfx_files_handoff", ["task_id"])
    op.create_table(
        "plugin_vfx_files_handoff_event",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("handoff_id", _uuid(), sa.ForeignKey("plugin_vfx_files_handoff.id"), nullable=False),
        sa.Column("actor_id", _uuid(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("feedback", sa.Text()),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("plugin_vfx_files_handoff_event_ix", "plugin_vfx_files_handoff_event", ["handoff_id"])
    op.create_table(
        "plugin_vfx_files_reconciliation_finding",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("kitsu_object_id", _uuid(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="orphaned"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("object_type", "kitsu_object_id", name="plugin_vfx_files_reconciliation_identity_uc"),
    )
    op.execute("""
        CREATE FUNCTION plugin_vfx_files_reject_immutable_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'VFX Files audit records are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table_name in (
        "plugin_vfx_files_publish_checkpoint",
        "plugin_vfx_files_native_create_event",
        "plugin_vfx_files_approval_event",
        "plugin_vfx_files_pinned_dependency",
        "plugin_vfx_files_handoff_event",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_immutable_mutation()"
        )


def downgrade():
    for table_name in (
        "plugin_vfx_files_handoff_event",
        "plugin_vfx_files_pinned_dependency",
        "plugin_vfx_files_approval_event",
        "plugin_vfx_files_native_create_event",
        "plugin_vfx_files_publish_checkpoint",
    ):
        op.execute(f"DROP TRIGGER {table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_immutable_mutation()")
    op.drop_table("plugin_vfx_files_reconciliation_finding")
    op.drop_index("plugin_vfx_files_handoff_event_ix", table_name="plugin_vfx_files_handoff_event")
    op.drop_table("plugin_vfx_files_handoff_event")
    op.drop_index("plugin_vfx_files_handoff_task_ix", table_name="plugin_vfx_files_handoff")
    op.drop_table("plugin_vfx_files_handoff")
    op.drop_index("plugin_vfx_files_pin_task_ix", table_name="plugin_vfx_files_pinned_dependency")
    op.drop_table("plugin_vfx_files_pinned_dependency")
    op.drop_index("plugin_vfx_files_approval_publish_ix", table_name="plugin_vfx_files_approval_event")
    op.drop_table("plugin_vfx_files_approval_event")
    op.drop_index("plugin_vfx_files_native_create_event_ix", table_name="plugin_vfx_files_native_create_event")
    op.drop_table("plugin_vfx_files_native_create_event")
    op.drop_index("plugin_vfx_files_publish_checkpoint_ix", table_name="plugin_vfx_files_publish_checkpoint")
    op.drop_table("plugin_vfx_files_publish_checkpoint")
    op.drop_table("plugin_vfx_files_publish_pointer")
    op.drop_index("plugin_vfx_files_publish_task_ix", table_name="plugin_vfx_files_publish")
    op.drop_table("plugin_vfx_files_publish")
    op.drop_index("plugin_vfx_files_job_claim_ix", table_name="plugin_vfx_files_job")
    op.drop_table("plugin_vfx_files_job")
    op.drop_index("plugin_vfx_files_output_family_production_ix", table_name="plugin_vfx_files_output_family")
    op.drop_table("plugin_vfx_files_output_family")
    op.drop_table("plugin_vfx_files_upload_part")
    op.drop_index("plugin_vfx_files_upload_task_ix", table_name="plugin_vfx_files_upload_session")
    op.drop_table("plugin_vfx_files_upload_session")
    op.drop_table("plugin_vfx_files_folder_binding")
    op.drop_index("plugin_vfx_files_storage_production_ix", table_name="plugin_vfx_files_storage_connection")
    op.drop_table("plugin_vfx_files_storage_connection")
    op.drop_table("plugin_vfx_files_state")
