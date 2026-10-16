"""Track integrated deactivation barriers until receiver drain acknowledgement.

Revision ID: vxf_0019
Revises: vxf_0018
"""

import sqlalchemy as sa
from alembic import op

revision = "vxf_0019"
down_revision = "vxf_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plugin_vfx_files_production_drive_authority",
        sa.Column("activation_transition_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 18 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    pending = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_production_drive_authority "
        "WHERE activation_transition_pending = true)"
    ))
    if pending:
        raise RuntimeError(
            "Drive deactivation barriers are pending; complete or reconcile them before downgrade"
        )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 17 WHERE id = 1")
    op.drop_column("plugin_vfx_files_production_drive_authority", "activation_transition_pending")
