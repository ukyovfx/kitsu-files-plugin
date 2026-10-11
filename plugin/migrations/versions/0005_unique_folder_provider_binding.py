"""Prevent one provider folder ID from being bound to multiple Kitsu scopes.

Revision ID: vxf_0005
Revises: vxf_0004
"""

from alembic import op
import sqlalchemy as sa


revision = "vxf_0005"
down_revision = "vxf_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 4 WHERE id = 1")
    op.create_index(
        "plugin_vfx_files_folder_provider_id_uc",
        "plugin_vfx_files_folder_binding",
        ["storage_connection_id", "allowed_root_id", "provider_folder_id"],
        unique=True,
        postgresql_where=sa.text("provider_folder_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("plugin_vfx_files_folder_provider_id_uc", table_name="plugin_vfx_files_folder_binding")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 3 WHERE id = 1")
