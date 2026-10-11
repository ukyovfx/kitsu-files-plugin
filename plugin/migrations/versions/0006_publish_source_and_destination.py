"""Bind Publish records to an immutable plugin Working File and Drive folder.

Revision ID: vxf_0006
Revises: vxf_0005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0006"
down_revision = "vxf_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 5 WHERE id = 1")
    op.add_column(
        "plugin_vfx_files_output_family",
        sa.Column("publish_folder_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "plugin_vfx_files_publish",
        sa.Column(
            "source_revision_file_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("plugin_vfx_files_working_revision_file.id"),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("plugin_vfx_files_publish", "source_revision_file_id")
    op.drop_column("plugin_vfx_files_output_family", "publish_folder_id")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 4 WHERE id = 1")
