"""Add immutable per-Task Working revisions and independent latest pointer.

Revision ID: vxf_0003
Revises: vxf_0002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0003"
down_revision = "vxf_0002"
branch_labels = None
depends_on = None


def _uuid():
    return postgresql.UUID(as_uuid=False)


def upgrade():
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 2 WHERE id = 1")
    op.create_table(
        "plugin_vfx_files_working_revision",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("production_id", _uuid(), nullable=False),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("base_revision_id", _uuid(), sa.ForeignKey("plugin_vfx_files_working_revision.id")),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="receiving"),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("manifest_bytes", sa.LargeBinary()),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("task_id", "version", name="plugin_vfx_files_working_version_uc"),
    )
    op.create_index("plugin_vfx_files_working_production_ix", "plugin_vfx_files_working_revision", ["production_id"])
    op.create_index("plugin_vfx_files_working_task_ix", "plugin_vfx_files_working_revision", ["task_id"])
    op.create_table(
        "plugin_vfx_files_working_revision_file",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("revision_id", _uuid(), sa.ForeignKey("plugin_vfx_files_working_revision.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255)),
        sa.Column("category", sa.String(32), nullable=False, server_default="working"),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("expected_sha256", sa.String(64), nullable=False),
        sa.Column("provider_object_id", sa.String(255)),
        sa.Column("provider_size", sa.BigInteger()),
        sa.Column("provider_sha256", sa.String(64)),
        sa.Column("state", sa.String(32), nullable=False, server_default="receiving"),
        sa.UniqueConstraint("revision_id", "ordinal", name="plugin_vfx_files_working_file_ordinal_uc"),
    )
    op.create_index("plugin_vfx_files_working_file_revision_ix", "plugin_vfx_files_working_revision_file", ["revision_id"])
    op.create_table(
        "plugin_vfx_files_working_dependency",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("revision_id", _uuid(), sa.ForeignKey("plugin_vfx_files_working_revision.id"), nullable=False),
        sa.Column("dependency_type", sa.String(16), nullable=False),
        sa.Column("dependency_id", _uuid(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("revision_id", "dependency_type", "dependency_id", name="plugin_vfx_files_working_dependency_uc"),
    )
    op.create_index("plugin_vfx_files_working_dependency_revision_ix", "plugin_vfx_files_working_dependency", ["revision_id"])
    op.create_table(
        "plugin_vfx_files_latest_working",
        sa.Column("task_id", _uuid(), primary_key=True),
        sa.Column("revision_id", _uuid(), sa.ForeignKey("plugin_vfx_files_working_revision.id"), nullable=False),
    )
    op.add_column("plugin_vfx_files_upload_session", sa.Column("working_revision_file_id", _uuid()))
    op.create_foreign_key(
        "plugin_vfx_files_upload_working_file_fk",
        "plugin_vfx_files_upload_session",
        "plugin_vfx_files_working_revision_file",
        ["working_revision_file_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint("plugin_vfx_files_upload_working_file_fk", "plugin_vfx_files_upload_session", type_="foreignkey")
    op.drop_column("plugin_vfx_files_upload_session", "working_revision_file_id")
    op.drop_table("plugin_vfx_files_latest_working")
    op.drop_index("plugin_vfx_files_working_dependency_revision_ix", table_name="plugin_vfx_files_working_dependency")
    op.drop_table("plugin_vfx_files_working_dependency")
    op.drop_index("plugin_vfx_files_working_file_revision_ix", table_name="plugin_vfx_files_working_revision_file")
    op.drop_table("plugin_vfx_files_working_revision_file")
    op.drop_index("plugin_vfx_files_working_task_ix", table_name="plugin_vfx_files_working_revision")
    op.drop_index("plugin_vfx_files_working_production_ix", table_name="plugin_vfx_files_working_revision")
    op.drop_table("plugin_vfx_files_working_revision")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 1 WHERE id = 1")
