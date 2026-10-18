"""Store Drive templates and sync policy under the Plugin authority.

Revision ID: vxf_0017
Revises: vxf_0016
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "vxf_0017"
down_revision = "vxf_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plugin_vfx_files_production_drive_authority",
        sa.Column("desired_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Existing KitsuSync rows remain legacy-owned. No configuration, activation,
    # credential, root, template, job, or history row is copied or modified.
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 16 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    plugin_configured = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_production_drive_authority "
        "WHERE owner = 'plugin' AND desired_config IS NOT NULL)"
    ))
    if plugin_configured:
        raise RuntimeError(
            "Plugin-owned Drive configuration exists; restore a consistent pre-migration snapshot instead of downgrading"
        )
    op.drop_column("plugin_vfx_files_production_drive_authority", "desired_config")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 15 WHERE id = 1")
