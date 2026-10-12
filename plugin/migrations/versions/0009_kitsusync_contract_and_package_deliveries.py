"""Add durable KitsuSync contract state and multi-recipient package receipts.

Revision ID: vxf_0009
Revises: vxf_0008
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0009"
down_revision = "vxf_0008"
branch_labels = None
depends_on = None


def _uuid():
    return postgresql.UUID(as_uuid=False)


def upgrade():
    op.create_table(
        "plugin_vfx_files_kitsusync_desired_config",
        sa.Column("production_id", _uuid(), primary_key=True),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("notification_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("desired_config", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("config_revision > 0", name="plugin_vfx_files_ks_config_revision_ck"),
        sa.CheckConstraint("notification_epoch >= 0", name="plugin_vfx_files_ks_notification_epoch_ck"),
    )
    op.create_table(
        "plugin_vfx_files_kitsusync_snapshot",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("production_id", _uuid(), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("source_etag", sa.String(200), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("object_count", sa.Integer(), nullable=False),
        sa.Column("hierarchy_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="receiving"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("production_id", "config_revision", "hierarchy_sha256", name="plugin_vfx_files_kitsusync_snapshot_revision_sha_uc"),
        sa.CheckConstraint("config_revision > 0 AND page_count BETWEEN 1 AND 1000 AND object_count BETWEEN 1 AND 500000", name="plugin_vfx_files_ks_snapshot_bounds_ck"),
    )
    op.create_index("plugin_vfx_files_kitsusync_snapshot_production_id_ix", "plugin_vfx_files_kitsusync_snapshot", ["production_id"])
    op.create_table(
        "plugin_vfx_files_kitsusync_snapshot_page",
        sa.Column("snapshot_id", _uuid(), sa.ForeignKey("plugin_vfx_files_kitsusync_snapshot.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("page_index", sa.Integer(), primary_key=True),
        sa.Column("page_sha256", sa.String(64), nullable=False),
        sa.Column("objects", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("page_index >= 0", name="plugin_vfx_files_ks_snapshot_page_index_ck"),
    )
    op.create_table(
        "plugin_vfx_files_kitsusync_event_outbox",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("production_id", _uuid(), nullable=False),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_owner", _uuid()),
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_response_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", name="plugin_vfx_files_kitsusync_event_id_uc"),
        sa.UniqueConstraint("production_id", "event_id", name="plugin_vfx_files_kitsusync_event_production_uc"),
        sa.CheckConstraint("attempts >= 0 AND lease_generation >= 0", name="plugin_vfx_files_ks_outbox_counters_ck"),
    )
    op.create_index("plugin_vfx_files_kitsusync_event_production_id_ix", "plugin_vfx_files_kitsusync_event_outbox", ["production_id"])
    op.create_index("plugin_vfx_files_kitsusync_event_claim_ix", "plugin_vfx_files_kitsusync_event_outbox", ["state", "available_at", "lease_expires_at"])

    op.create_table(
        "plugin_vfx_files_output_package_delivery",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("package_id", _uuid(), sa.ForeignKey("plugin_vfx_files_output_package.id"), nullable=False),
        sa.Column("production_id", _uuid(), nullable=False),
        sa.Column("recipient_task_id", _uuid(), nullable=False),
        sa.Column("recipient_task_type_id", _uuid(), nullable=False),
        sa.Column("delivery_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(24), nullable=False, server_default="available"),
        sa.Column("feedback", sa.Text()),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="plugin_vfx_files_package_delivery_idempotency_uc"),
        sa.UniqueConstraint("package_id", "recipient_task_id", "delivery_revision", name="plugin_vfx_files_package_delivery_revision_uc"),
        sa.CheckConstraint("delivery_revision > 0", name="plugin_vfx_files_package_delivery_revision_ck"),
        sa.CheckConstraint("state IN ('available','delivered','in_review','accepted','needs_fix','revised')", name="plugin_vfx_files_package_delivery_state_ck"),
    )
    op.create_index("plugin_vfx_files_package_delivery_package_id_ix", "plugin_vfx_files_output_package_delivery", ["package_id"])
    op.create_index("plugin_vfx_files_package_delivery_production_id_ix", "plugin_vfx_files_output_package_delivery", ["production_id"])
    op.create_table(
        "plugin_vfx_files_output_package_delivery_event",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("delivery_id", _uuid(), sa.ForeignKey("plugin_vfx_files_output_package_delivery.id"), nullable=False),
        sa.Column("actor_id", _uuid(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("feedback", sa.Text()),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="plugin_vfx_files_package_delivery_event_idempotency_uc"),
        sa.CheckConstraint("event_type IN ('delivered','in_review','accepted','needs_fix','revised')", name="plugin_vfx_files_package_delivery_event_type_ck"),
    )
    op.create_index("plugin_vfx_files_package_delivery_event_delivery_id_ix", "plugin_vfx_files_output_package_delivery_event", ["delivery_id"])
    op.execute("""
        CREATE FUNCTION plugin_vfx_files_reject_ks_event_identity_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.production_id IS DISTINCT FROM NEW.production_id
                OR OLD.event_id IS DISTINCT FROM NEW.event_id
                OR OLD.event_type IS DISTINCT FROM NEW.event_type
                OR OLD.payload IS DISTINCT FROM NEW.payload
                OR OLD.payload_sha256 IS DISTINCT FROM NEW.payload_sha256 THEN
                RAISE EXCEPTION 'KitsuSync event identity and payload are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE FUNCTION plugin_vfx_files_reject_snapshot_page_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'KitsuSync hierarchy snapshot pages are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE FUNCTION plugin_vfx_files_reject_delivery_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Output Package delivery history is immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER plugin_vfx_files_kitsusync_outbox_identity_immutable "
        "BEFORE UPDATE OR DELETE ON plugin_vfx_files_kitsusync_event_outbox "
        "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_ks_event_identity_mutation()"
    )
    op.execute(
        "CREATE TRIGGER plugin_vfx_files_kitsusync_snapshot_page_immutable "
        "BEFORE UPDATE OR DELETE ON plugin_vfx_files_kitsusync_snapshot_page "
        "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_snapshot_page_mutation()"
    )
    op.execute(
        "CREATE TRIGGER plugin_vfx_files_package_delivery_event_immutable "
        "BEFORE UPDATE OR DELETE ON plugin_vfx_files_output_package_delivery_event "
        "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_delivery_event_mutation()"
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 8 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_state = bind.scalar(sa.text("""
        SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_kitsusync_desired_config)
            OR EXISTS (SELECT 1 FROM plugin_vfx_files_kitsusync_snapshot)
            OR EXISTS (SELECT 1 FROM plugin_vfx_files_kitsusync_event_outbox)
            OR EXISTS (SELECT 1 FROM plugin_vfx_files_output_package_delivery)
    """))
    if has_state:
        raise RuntimeError("vxf_0009 contains integration or delivery state; restore a consistent pre-migration snapshot instead of downgrading")
    op.execute("DROP TRIGGER plugin_vfx_files_package_delivery_event_immutable ON plugin_vfx_files_output_package_delivery_event")
    op.execute("DROP TRIGGER plugin_vfx_files_kitsusync_snapshot_page_immutable ON plugin_vfx_files_kitsusync_snapshot_page")
    op.execute("DROP TRIGGER plugin_vfx_files_kitsusync_outbox_identity_immutable ON plugin_vfx_files_kitsusync_event_outbox")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_delivery_event_mutation()")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_snapshot_page_mutation()")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_ks_event_identity_mutation()")
    op.drop_index("plugin_vfx_files_package_delivery_event_delivery_id_ix", table_name="plugin_vfx_files_output_package_delivery_event")
    op.drop_table("plugin_vfx_files_output_package_delivery_event")
    op.drop_index("plugin_vfx_files_package_delivery_production_id_ix", table_name="plugin_vfx_files_output_package_delivery")
    op.drop_index("plugin_vfx_files_package_delivery_package_id_ix", table_name="plugin_vfx_files_output_package_delivery")
    op.drop_table("plugin_vfx_files_output_package_delivery")
    op.drop_index("plugin_vfx_files_kitsusync_event_claim_ix", table_name="plugin_vfx_files_kitsusync_event_outbox")
    op.drop_index("plugin_vfx_files_kitsusync_event_production_id_ix", table_name="plugin_vfx_files_kitsusync_event_outbox")
    op.drop_table("plugin_vfx_files_kitsusync_event_outbox")
    op.drop_table("plugin_vfx_files_kitsusync_snapshot_page")
    op.drop_index("plugin_vfx_files_kitsusync_snapshot_production_id_ix", table_name="plugin_vfx_files_kitsusync_snapshot")
    op.drop_table("plugin_vfx_files_kitsusync_snapshot")
    op.drop_table("plugin_vfx_files_kitsusync_desired_config")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 7 WHERE id = 1")
