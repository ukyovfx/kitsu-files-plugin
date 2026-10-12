"""Persist upload names and verified external transfer results.

Revision ID: vxf_0002
Revises: vxf_0001
"""

from alembic import op
import sqlalchemy as sa


revision = "vxf_0002"
down_revision = "vxf_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plugin_vfx_files_upload_session", sa.Column("file_name", sa.String(512), nullable=False, server_default="unnamed"))
    op.add_column("plugin_vfx_files_upload_session", sa.Column("provider_object_id", sa.String(255), nullable=True))
    op.add_column("plugin_vfx_files_upload_session", sa.Column("provider_size", sa.BigInteger(), nullable=True))
    op.add_column("plugin_vfx_files_upload_session", sa.Column("provider_sha256", sa.String(64), nullable=True))
    op.add_column("plugin_vfx_files_upload_session", sa.Column("transferred_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("plugin_vfx_files_upload_session", sa.Column("failure_code", sa.String(64), nullable=True))
    op.alter_column("plugin_vfx_files_upload_session", "file_name", server_default=None)


def downgrade():
    op.drop_column("plugin_vfx_files_upload_session", "failure_code")
    op.drop_column("plugin_vfx_files_upload_session", "transferred_at")
    op.drop_column("plugin_vfx_files_upload_session", "provider_sha256")
    op.drop_column("plugin_vfx_files_upload_session", "provider_size")
    op.drop_column("plugin_vfx_files_upload_session", "provider_object_id")
    op.drop_column("plugin_vfx_files_upload_session", "file_name")
