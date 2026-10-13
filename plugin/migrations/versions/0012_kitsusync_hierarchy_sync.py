"""Persist explicit hierarchy previews and allow identical immutable snapshots.

Revision ID: vxf_0012
Revises: vxf_0011
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "vxf_0012"
down_revision = "vxf_0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plugin_vfx_files_job", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE plugin_vfx_files_job SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column("plugin_vfx_files_job", "updated_at", nullable=False, server_default=sa.func.now())
    # The hierarchy digest is content evidence, not snapshot identity. Multiple
    # Preview requests can produce equal bytes with distinct immutable IDs.
    op.drop_constraint(
        "plugin_vfx_files_kitsusync_snapshot_revision_sha_uc",
        "plugin_vfx_files_kitsusync_snapshot",
        type_="unique",
    )
    op.create_table(
        "plugin_vfx_files_kitsusync_sync_preview",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("production_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_kitsusync_snapshot.id"), nullable=False),
        sa.Column("template_configuration_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_shared_template.id"), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("activation_epoch", sa.Integer(), nullable=False),
        sa.Column("storage_connection_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False),
        sa.Column("root_id", sa.String(255), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="preview_ready"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("config_revision > 0 AND template_version > 0 AND activation_epoch >= 0", name="plugin_vfx_files_ks_preview_revision_ck"),
        sa.CheckConstraint("state IN ('preview_ready', 'applied', 'expired', 'stale')", name="plugin_vfx_files_ks_preview_state_ck"),
    )
    op.create_index(
        "plugin_vfx_files_ks_preview_scope_ix",
        "plugin_vfx_files_kitsusync_sync_preview",
        ["production_id", "expires_at"],
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 11 WHERE id = 1")


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_job)"
    )):
        raise RuntimeError(
            "vxf_0012 adds durable Worker update timestamps; restore a consistent pre-migration snapshot instead of downgrading"
        )
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_kitsusync_sync_preview)"
    )):
        raise RuntimeError(
            "vxf_0012 contains hierarchy synchronization evidence; restore a consistent pre-migration snapshot instead of downgrading"
        )
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_kitsusync_snapshot GROUP BY production_id, config_revision, hierarchy_sha256 HAVING count(*) > 1)"
    )):
        raise RuntimeError(
            "vxf_0012 permits repeated immutable snapshot content; restore a consistent pre-migration snapshot instead of downgrading"
        )
    op.drop_index("plugin_vfx_files_ks_preview_scope_ix", table_name="plugin_vfx_files_kitsusync_sync_preview")
    op.drop_table("plugin_vfx_files_kitsusync_sync_preview")
    op.drop_column("plugin_vfx_files_job", "updated_at")
    op.create_unique_constraint(
        "plugin_vfx_files_kitsusync_snapshot_revision_sha_uc",
        "plugin_vfx_files_kitsusync_snapshot",
        ["production_id", "config_revision", "hierarchy_sha256"],
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 10 WHERE id = 1")
