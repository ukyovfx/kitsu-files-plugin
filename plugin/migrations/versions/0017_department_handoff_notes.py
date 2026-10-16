"""Persist the sender note on Department and Person Handoffs.

Revision ID: vxf_0017
Revises: vxf_0016
"""

from alembic import op
import sqlalchemy as sa


revision = "vxf_0017"
down_revision = "vxf_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plugin_vfx_files_handoff",
        sa.Column("sender_note", sa.Text(), nullable=True),
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 16 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_notes = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_handoff WHERE sender_note IS NOT NULL)"
    ))
    if has_notes:
        raise RuntimeError(
            "vxf_0017 contains Handoff notes; restore a consistent pre-migration snapshot instead of downgrading"
        )
    op.drop_column("plugin_vfx_files_handoff", "sender_note")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 15 WHERE id = 1")
