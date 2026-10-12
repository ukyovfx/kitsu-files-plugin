"""Persist Output Package QC independently from native Zou OutputFile status.

Revision ID: vxf_0008
Revises: vxf_0007
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0008"
down_revision = "vxf_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plugin_vfx_files_output_package", sa.Column("qc_state", sa.String(24), nullable=False, server_default="pending"))
    op.add_column("plugin_vfx_files_output_package", sa.Column("qc_report", postgresql.JSONB(), nullable=True))
    op.add_column("plugin_vfx_files_output_package", sa.Column("provider_folder_id", sa.String(255), nullable=True))
    # Google Drive has no SHA-256 metadata field. Permit the Worker to bind the
    # provider-readback digest while the package is still uncompleted, then
    # keep the existing completed-package immutability trigger fail-closed.
    op.alter_column("plugin_vfx_files_output_package_file", "sha256", existing_type=sa.String(64), nullable=True)
    op.add_column("plugin_vfx_files_output_package_file", sa.Column("source_provider_object_id", sa.String(255), nullable=True))
    # vxf_0007 forbids every UPDATE. Temporarily remove that trigger to backfill
    # the new source ID column, then install the stricter completed-row trigger.
    op.execute("DROP TRIGGER plugin_vfx_files_output_package_file_immutable ON plugin_vfx_files_output_package_file")
    op.execute("UPDATE plugin_vfx_files_output_package_file SET source_provider_object_id = provider_object_id")
    op.alter_column("plugin_vfx_files_output_package_file", "provider_object_id", existing_type=sa.String(255), nullable=True)
    op.execute("""
        CREATE OR REPLACE FUNCTION plugin_vfx_files_reject_output_file_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'VFX Files Output Package file rows cannot be deleted';
            END IF;
            IF (SELECT state FROM plugin_vfx_files_output_package WHERE id = NEW.package_id) = 'completed' THEN
                RAISE EXCEPTION 'VFX Files completed Output Package files are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER plugin_vfx_files_output_package_file_immutable "
        "BEFORE INSERT OR UPDATE OR DELETE ON plugin_vfx_files_output_package_file "
        "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_output_file_mutation()"
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 7 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_package_rows = bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_output_package)"))
    if has_package_rows:
        raise RuntimeError(
            "vxf_0008 QC evidence is attached to Output Packages; restore a consistent pre-migration snapshot instead of downgrading"
        )
    op.execute("""
        CREATE OR REPLACE FUNCTION plugin_vfx_files_reject_output_file_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' AND (SELECT state FROM plugin_vfx_files_output_package WHERE id = NEW.package_id) = 'completed' THEN
                RAISE EXCEPTION 'VFX Files completed Output Packages are immutable';
            END IF;
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'VFX Files output package file entries are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.alter_column("plugin_vfx_files_output_package_file", "sha256", existing_type=sa.String(64), nullable=False)
    op.alter_column("plugin_vfx_files_output_package_file", "provider_object_id", existing_type=sa.String(255), nullable=False)
    op.drop_column("plugin_vfx_files_output_package_file", "source_provider_object_id")
    op.drop_column("plugin_vfx_files_output_package", "provider_folder_id")
    op.drop_column("plugin_vfx_files_output_package", "qc_report")
    op.drop_column("plugin_vfx_files_output_package", "qc_state")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 6 WHERE id = 1")
