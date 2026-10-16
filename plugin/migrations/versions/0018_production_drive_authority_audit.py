"""Add immutable audit events for Drive authority transitions.

Revision ID: vxf_0018
Revises: vxf_0017
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "vxf_0018"
down_revision = "vxf_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_vfx_files_production_drive_authority_event",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("production_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("source_revision", sa.BigInteger(), nullable=False),
        sa.Column("config_revision", sa.BigInteger(), nullable=False),
        sa.Column("activation_epoch", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("event_type IN ('legacy_configuration_imported')", name="plugin_vfx_files_drive_authority_event_type_ck"),
        sa.UniqueConstraint("production_id", "idempotency_key", name="plugin_vfx_files_drive_authority_event_idem_uc"),
    )
    op.create_index(
        "plugin_vfx_files_drive_authority_event_production_ix",
        "plugin_vfx_files_production_drive_authority_event",
        ["production_id", "created_at"],
    )
    op.execute(
        "CREATE TRIGGER plugin_vfx_files_production_drive_authority_event_immutable "
        "BEFORE UPDATE OR DELETE ON plugin_vfx_files_production_drive_authority_event "
        "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_immutable_mutation()"
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 17 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_events = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_production_drive_authority_event)"
    ))
    if has_events:
        raise RuntimeError(
            "Drive authority audit events exist; restore a consistent pre-migration snapshot instead of downgrading"
        )
    op.execute("DROP TRIGGER plugin_vfx_files_production_drive_authority_event_immutable ON plugin_vfx_files_production_drive_authority_event")
    op.drop_index(
        "plugin_vfx_files_drive_authority_event_production_ix",
        table_name="plugin_vfx_files_production_drive_authority_event",
    )
    op.drop_table("plugin_vfx_files_production_drive_authority_event")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 16 WHERE id = 1")
