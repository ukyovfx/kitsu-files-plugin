"""Add immutable original-reference share records and recipient targets.

Revision ID: vxf_0020
Revises: vxf_0019
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "vxf_0020"
down_revision = "vxf_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_vfx_files_link_share",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("production_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_task_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_entity_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_entity_type", sa.String(16), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("storage_connection_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False),
        sa.Column("object_id", sa.String(200), nullable=False),
        sa.Column("object_type", sa.String(16), nullable=False, server_default="file"),
        sa.Column("object_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("object_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("request_idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="verifying"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("production_id", "request_idempotency_key", name="plugin_vfx_files_link_share_idem_uc"),
        sa.CheckConstraint("source_entity_type IN ('shot','asset')", name="plugin_vfx_files_link_share_entity_type_ck"),
        sa.CheckConstraint("object_type IN ('file','folder')", name="plugin_vfx_files_link_share_object_type_ck"),
        sa.CheckConstraint("state IN ('verifying','created','failed')", name="plugin_vfx_files_link_share_state_ck"),
    )
    op.create_index("plugin_vfx_files_link_share_source_ix", "plugin_vfx_files_link_share", ["production_id", "source_task_id", "created_at"])

    op.create_table(
        "plugin_vfx_files_link_share_target",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("share_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_link_share.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_task_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("recipient_task_type_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("recipient_department_id", postgresql.UUID(as_uuid=False)),
        sa.Column("event_id", sa.String(36), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("event_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("event_payload_sha256", sa.String(64), nullable=True),
        sa.Column("notification_status", sa.String(32), nullable=False, server_default="blocked_receiver_version"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("share_id", "recipient_task_id", name="plugin_vfx_files_link_share_recipient_uc"),
        sa.CheckConstraint("notification_status IN ('blocked_receiver_version','blocked_disclosure','pending','inbox_accepted','quarantined','delivered')", name="plugin_vfx_files_link_share_notify_state_ck"),
    )
    op.create_index("plugin_vfx_files_link_share_target_task_ix", "plugin_vfx_files_link_share_target", ["recipient_task_id", "created_at"])

    op.execute("""
        CREATE OR REPLACE FUNCTION plugin_vfx_files_reject_link_share_identity_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'VFX Files Link Shares are retained as immutable history';
            END IF;
            IF OLD.production_id IS DISTINCT FROM NEW.production_id
                OR OLD.source_task_id IS DISTINCT FROM NEW.source_task_id
                OR OLD.source_entity_id IS DISTINCT FROM NEW.source_entity_id
                OR OLD.source_entity_type IS DISTINCT FROM NEW.source_entity_type
                OR OLD.actor_id IS DISTINCT FROM NEW.actor_id
                OR OLD.storage_connection_id IS DISTINCT FROM NEW.storage_connection_id
                OR OLD.object_id IS DISTINCT FROM NEW.object_id
                OR OLD.request_idempotency_key IS DISTINCT FROM NEW.request_idempotency_key
                OR OLD.request_sha256 IS DISTINCT FROM NEW.request_sha256
                OR OLD.state = 'created' AND (
                    NEW.state IS DISTINCT FROM OLD.state
                    OR OLD.object_type IS DISTINCT FROM NEW.object_type
                    OR OLD.object_url IS DISTINCT FROM NEW.object_url
                    OR OLD.object_snapshot IS DISTINCT FROM NEW.object_snapshot
                )
                OR OLD.state NOT IN ('verifying','failed') AND NEW.state NOT IN ('created','failed') THEN
                RAISE EXCEPTION 'VFX Files Link Share identity and snapshot are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER plugin_vfx_files_link_share_immutable
        BEFORE UPDATE OR DELETE ON plugin_vfx_files_link_share
        FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_link_share_identity_mutation()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION plugin_vfx_files_reject_link_share_target_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.id IS DISTINCT FROM NEW.id
                OR OLD.share_id IS DISTINCT FROM NEW.share_id
                OR OLD.recipient_task_id IS DISTINCT FROM NEW.recipient_task_id
                OR OLD.recipient_task_type_id IS DISTINCT FROM NEW.recipient_task_type_id
                OR OLD.recipient_department_id IS DISTINCT FROM NEW.recipient_department_id
                OR OLD.event_id IS DISTINCT FROM NEW.event_id
                OR OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key
                OR OLD.event_payload IS NOT NULL AND (
                    OLD.event_payload IS DISTINCT FROM NEW.event_payload
                    OR OLD.event_payload_sha256 IS DISTINCT FROM NEW.event_payload_sha256
                ) THEN
                RAISE EXCEPTION 'VFX Files Link Share target identity and event are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER plugin_vfx_files_link_share_target_immutable
        BEFORE UPDATE OR DELETE ON plugin_vfx_files_link_share_target
        FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_link_share_target_mutation()
    """)
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 19 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_records = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_link_share)"
    ))
    if has_records:
        raise RuntimeError("Link Share history exists; restore a consistent snapshot instead of downgrading")
    op.execute("DROP TRIGGER plugin_vfx_files_link_share_target_immutable ON plugin_vfx_files_link_share_target")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_link_share_target_mutation()")
    op.execute("DROP TRIGGER plugin_vfx_files_link_share_immutable ON plugin_vfx_files_link_share")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_link_share_identity_mutation()")
    op.drop_index("plugin_vfx_files_link_share_target_task_ix", table_name="plugin_vfx_files_link_share_target")
    op.drop_table("plugin_vfx_files_link_share_target")
    op.drop_index("plugin_vfx_files_link_share_source_ix", table_name="plugin_vfx_files_link_share")
    op.drop_table("plugin_vfx_files_link_share")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 18 WHERE id = 1")
