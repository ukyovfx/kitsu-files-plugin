"""Persist an explicitly selected, folder-only Shared Drive template.

Revision ID: vxf_0004
Revises: vxf_0003
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0004"
down_revision = "vxf_0003"
branch_labels = None
depends_on = None


def _uuid():
    return postgresql.UUID(as_uuid=False)


def upgrade():
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 3 WHERE id = 1")
    op.create_table(
        "plugin_vfx_files_shared_template",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("production_id", _uuid(), nullable=False),
        sa.Column("storage_connection_id", _uuid(), sa.ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False),
        sa.Column("allowed_root_id", sa.String(255), nullable=False),
        sa.Column("template_folder_id", sa.String(255), nullable=False),
        sa.Column("template_tree", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("role_mappings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", _uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("production_id", "storage_connection_id", name="plugin_vfx_files_shared_template_scope_uc"),
    )
    op.create_index("plugin_vfx_files_shared_template_production_ix", "plugin_vfx_files_shared_template", ["production_id"])


def downgrade():
    op.drop_index("plugin_vfx_files_shared_template_production_ix", table_name="plugin_vfx_files_shared_template")
    op.drop_table("plugin_vfx_files_shared_template")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 2 WHERE id = 1")
