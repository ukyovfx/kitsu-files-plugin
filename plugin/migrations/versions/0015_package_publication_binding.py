"""Add explicit Output Package to formally approved Publish provenance links.

Revision ID: vxf_0015
Revises: vxf_0014
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0015"
down_revision = "vxf_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_vfx_files_output_package_publication",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("package_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_output_package.id"), nullable=False),
        sa.Column("publish_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_publish.id"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("package_id", name="plugin_vfx_files_package_publication_package_uc"),
        sa.UniqueConstraint("publish_id", name="plugin_vfx_files_package_publication_publish_uc"),
        sa.UniqueConstraint("idempotency_key", name="plugin_vfx_files_package_publication_idempotency_uc"),
    )
    op.execute("""
        CREATE FUNCTION plugin_vfx_files_reject_package_publication_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Output Package publication links are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER plugin_vfx_files_package_publication_immutable "
        "BEFORE UPDATE OR DELETE ON plugin_vfx_files_output_package_publication "
        "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_package_publication_mutation()"
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 14 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_links = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_output_package_publication)"
    ))
    if has_links:
        raise RuntimeError(
            "vxf_0015 contains immutable Package-to-Publish history; restore a consistent snapshot instead of downgrading"
        )
    op.execute("DROP TRIGGER plugin_vfx_files_package_publication_immutable ON plugin_vfx_files_output_package_publication")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_package_publication_mutation()")
    op.drop_table("plugin_vfx_files_output_package_publication")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 13 WHERE id = 1")
