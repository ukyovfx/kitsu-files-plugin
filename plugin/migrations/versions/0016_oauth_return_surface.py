"""Bind each OAuth completion to an approved application return surface.

Revision ID: vxf_0016
Revises: vxf_0015
"""

import sqlalchemy as sa
from alembic import op

revision = "vxf_0016"
down_revision = "vxf_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plugin_vfx_files_drive_authorization_intent",
        sa.Column("return_surface", sa.String(24), nullable=False, server_default="kitsusync"),
    )
    op.create_check_constraint(
        "plugin_vfx_files_drive_auth_return_surface_ck",
        "plugin_vfx_files_drive_authorization_intent",
        "return_surface IN ('kitsusync','kitsu_plugin')",
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 15 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    native_intents = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_drive_authorization_intent WHERE return_surface <> 'kitsusync')"
    ))
    if native_intents:
        raise RuntimeError(
            "Native OAuth return intents exist; restore a consistent pre-migration snapshot instead of downgrading"
        )
    op.drop_constraint(
        "plugin_vfx_files_drive_auth_return_surface_ck",
        "plugin_vfx_files_drive_authorization_intent",
        type_="check",
    )
    op.drop_column("plugin_vfx_files_drive_authorization_intent", "return_surface")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 14 WHERE id = 1")
