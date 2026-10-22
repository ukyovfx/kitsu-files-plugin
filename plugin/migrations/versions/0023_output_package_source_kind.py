"""Record whether an Output Package's sources come from the Production Drive or the registrant's personal Drive.

Revision ID: vxf_0023
Revises: vxf_0022
"""

from alembic import op
import sqlalchemy as sa

revision = "vxf_0023"
down_revision = "vxf_0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plugin_vfx_files_output_package",
                  sa.Column("source_kind", sa.String(16), nullable=False, server_default="production"))
    op.create_check_constraint("plugin_vfx_files_output_package_source_kind_ck", "plugin_vfx_files_output_package",
                               "source_kind IN ('production','personal')")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 22 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_output_package WHERE source_kind <> 'production')")):
        raise RuntimeError("Personal-Drive Output Packages exist; restore a consistent snapshot instead of downgrading")
    op.drop_constraint("plugin_vfx_files_output_package_source_kind_ck", "plugin_vfx_files_output_package", type_="check")
    op.drop_column("plugin_vfx_files_output_package", "source_kind")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 21 WHERE id = 1")
