"""Persist immutable Output Package delivery-create request identity.

Revision ID: vxf_0016
Revises: vxf_0015
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0016"
down_revision = "vxf_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plugin_vfx_files_output_package_delivery_event",
        sa.Column("request_operation", sa.String(32), nullable=True),
    )
    op.add_column(
        "plugin_vfx_files_output_package_delivery_event",
        sa.Column(
            "request_package_id", postgresql.UUID(as_uuid=False),
            sa.ForeignKey("plugin_vfx_files_output_package.id"), nullable=True,
        ),
    )
    op.add_column(
        "plugin_vfx_files_output_package_delivery_event",
        sa.Column("request_recipient_task_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "plugin_vfx_files_output_package_delivery_event",
        sa.Column("request_recipient_task_type_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "plugin_vfx_files_output_package_delivery_event",
        sa.Column("request_revision", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "plugin_vfx_files_delivery_request_revision_ck",
        "plugin_vfx_files_output_package_delivery_event",
        "request_revision IS NULL OR request_revision > 0",
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 15 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_request_identity = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_output_package_delivery_event "
        "WHERE request_operation IS NOT NULL OR request_package_id IS NOT NULL "
        "OR request_recipient_task_id IS NOT NULL "
        "OR request_recipient_task_type_id IS NOT NULL OR request_revision IS NOT NULL)"
    ))
    if has_request_identity:
        raise RuntimeError(
            "vxf_0016 contains immutable delivery request identities; restore a consistent snapshot instead of downgrading"
        )
    op.drop_constraint(
        "plugin_vfx_files_delivery_request_revision_ck",
        "plugin_vfx_files_output_package_delivery_event", type_="check",
    )
    op.drop_column("plugin_vfx_files_output_package_delivery_event", "request_revision")
    op.drop_column("plugin_vfx_files_output_package_delivery_event", "request_recipient_task_type_id")
    op.drop_column("plugin_vfx_files_output_package_delivery_event", "request_recipient_task_id")
    op.drop_column("plugin_vfx_files_output_package_delivery_event", "request_package_id")
    op.drop_column("plugin_vfx_files_output_package_delivery_event", "request_operation")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 14 WHERE id = 1")
