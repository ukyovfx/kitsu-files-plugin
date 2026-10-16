"""Add an explicit per-Production Drive configuration owner.

Revision ID: vxf_0015
Revises: vxf_0014
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "vxf_0015"
down_revision = "vxf_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_vfx_files_production_drive_authority",
        sa.Column("production_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activation_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("plugin_vfx_files_storage_connection.id"),
        ),
        sa.Column("root_id", sa.String(255)),
        sa.Column("activation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", postgresql.UUID(as_uuid=False)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("owner IN ('plugin','legacy_kitsusync')", name="plugin_vfx_files_drive_authority_owner_ck"),
        sa.CheckConstraint("state IN ('unconfigured','configured','legacy_import_required')", name="plugin_vfx_files_drive_authority_state_ck"),
        sa.CheckConstraint("config_revision >= 0 AND activation_epoch >= 0", name="plugin_vfx_files_drive_authority_revision_epoch_ck"),
        sa.CheckConstraint(
            "(state = 'configured' AND owner = 'plugin' AND connection_id IS NOT NULL AND root_id IS NOT NULL) "
            "OR (state = 'unconfigured' AND owner = 'plugin' AND connection_id IS NULL AND root_id IS NULL AND activation_enabled = false) "
            "OR (state = 'legacy_import_required' AND owner = 'legacy_kitsusync' AND connection_id IS NULL AND root_id IS NULL AND activation_enabled = false)",
            name="plugin_vfx_files_drive_authority_state_owner_ck",
        ),
    )
    # Copy ownership state only. Keep every legacy desired-config, credential,
    # root, binding, template, job, and event row in place for reviewed import.
    op.execute("""
        INSERT INTO plugin_vfx_files_production_drive_authority
            (production_id, owner, state, config_revision, activation_epoch,
             connection_id, root_id, activation_enabled)
        SELECT production_id, 'legacy_kitsusync', 'legacy_import_required',
               0, GREATEST(notification_epoch, 0), NULL, NULL, false
          FROM plugin_vfx_files_kitsusync_desired_config
    """)
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 14 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    plugin_owned = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_production_drive_authority WHERE owner = 'plugin')"
    ))
    if plugin_owned:
        raise RuntimeError(
            "Plugin-owned Drive authority exists; restore a consistent pre-migration snapshot instead of downgrading"
        )
    op.drop_table("plugin_vfx_files_production_drive_authority")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 13 WHERE id = 1")
