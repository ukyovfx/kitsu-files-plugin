"""Persist scoped Drive reconciliation evidence.

Revision ID: vxf_0010
Revises: vxf_0009
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0010"
down_revision = "vxf_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_vfx_files_drive_observation",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("reconciliation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("production_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("storage_connection_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False),
        sa.Column("allowed_root_id", sa.String(255), nullable=False),
        sa.Column("provider_object_id", sa.String(255), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("metadata_fingerprint", sa.String(64)),
        sa.Column("provider_modified_at", sa.String(64)),
        sa.Column("byte_count", sa.BigInteger()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("reconciliation_id", "provider_object_id", name="plugin_vfx_files_drive_observation_identity_uc"),
        sa.CheckConstraint("state IN ('present','missing','modified','inaccessible','stale')", name="plugin_vfx_files_drive_observation_state_ck"),
    )
    op.create_index("plugin_vfx_files_drive_observation_production_id_ix", "plugin_vfx_files_drive_observation", ["production_id"])
    op.create_index("plugin_vfx_files_drive_observation_scope_ix", "plugin_vfx_files_drive_observation", ["storage_connection_id", "allowed_root_id", "observed_at"])
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 9 WHERE id = 1")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_drive_observation)")):
        raise RuntimeError("vxf_0010 contains Drive reconciliation evidence; restore a consistent pre-migration snapshot instead of downgrading")
    op.drop_index("plugin_vfx_files_drive_observation_scope_ix", table_name="plugin_vfx_files_drive_observation")
    op.drop_index("plugin_vfx_files_drive_observation_production_id_ix", table_name="plugin_vfx_files_drive_observation")
    op.drop_table("plugin_vfx_files_drive_observation")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 8 WHERE id = 1")
