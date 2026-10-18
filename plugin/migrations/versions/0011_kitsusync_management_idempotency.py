"""Add durable idempotency receipts for KitsuSync management mutations.

Revision ID: vxf_0011
Revises: vxf_0010
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0011"
down_revision = "vxf_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_vfx_files_kitsusync_management_idempotency",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("production_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_etag", sa.String(32)),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("production_id", "idempotency_key", name="plugin_vfx_files_ks_mgmt_idem_scope_uc"),
        sa.CheckConstraint("response_status BETWEEN 200 AND 299", name="plugin_vfx_files_ks_mgmt_idem_status_ck"),
    )
    op.create_index(
        "plugin_vfx_files_ks_mgmt_idem_created_ix",
        "plugin_vfx_files_kitsusync_management_idempotency",
        ["created_at"],
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 10 WHERE id = 1")


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_kitsusync_management_idempotency)"
    )):
        raise RuntimeError(
            "vxf_0011 contains management replay evidence; restore a consistent pre-migration snapshot instead of downgrading"
        )
    op.drop_index(
        "plugin_vfx_files_ks_mgmt_idem_created_ix",
        table_name="plugin_vfx_files_kitsusync_management_idempotency",
    )
    op.drop_table("plugin_vfx_files_kitsusync_management_idempotency")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 9 WHERE id = 1")
