"""Add immutable output-package metadata and Department/Task handoff targets.

Revision ID: vxf_0007
Revises: vxf_0006
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0007"
down_revision = "vxf_0006"
branch_labels = None
depends_on = None


def _uuid():
    return postgresql.UUID(as_uuid=False)


def upgrade():
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 6 WHERE id = 1")
    op.add_column("plugin_vfx_files_handoff", sa.Column("recipient_department_id", _uuid(), nullable=True))
    op.add_column("plugin_vfx_files_handoff", sa.Column("recipient_task_id", _uuid(), nullable=True))
    op.add_column("plugin_vfx_files_handoff", sa.Column("recipient_person_id", _uuid(), nullable=True))
    op.alter_column("plugin_vfx_files_handoff", "recipient_id", existing_type=_uuid(), nullable=True)
    op.create_check_constraint(
        "plugin_vfx_files_handoff_target_ck",
        "plugin_vfx_files_handoff",
        "(recipient_id IS NOT NULL AND recipient_department_id IS NULL AND recipient_task_id IS NULL AND recipient_person_id IS NULL) "
        "OR (recipient_id IS NULL AND recipient_department_id IS NOT NULL AND recipient_task_id IS NOT NULL)",
    )
    op.create_index(
        "plugin_vfx_files_handoff_pending_person_uc", "plugin_vfx_files_handoff",
        ["publish_id", "recipient_id"], unique=True,
        postgresql_where=sa.text("recipient_id IS NOT NULL AND state = 'pending'"),
    )
    op.create_index(
        "plugin_vfx_files_handoff_pending_department_task_uc", "plugin_vfx_files_handoff",
        ["publish_id", "recipient_department_id", "recipient_task_id"], unique=True,
        postgresql_where=sa.text("recipient_department_id IS NOT NULL AND state = 'pending'"),
    )
    op.create_table(
        "plugin_vfx_files_output_package",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("production_id", _uuid(), nullable=False),
        sa.Column("entity_id", _uuid(), nullable=False),
        sa.Column("task_id", _uuid(), nullable=False),
        sa.Column("output_type_id", _uuid(), nullable=False),
        sa.Column("package_type", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="receiving"),
        sa.Column("manifest_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("source_working_revision_id", _uuid(), sa.ForeignKey("plugin_vfx_files_working_revision.id")),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("task_id", "output_type_id", "package_type", "version", name="plugin_vfx_files_output_package_version_uc"),
    )
    for column in ("production_id", "entity_id", "task_id"):
        op.create_index(f"plugin_vfx_files_output_package_{column}_ix", "plugin_vfx_files_output_package", [column])
    op.create_table(
        "plugin_vfx_files_output_package_file",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("package_id", _uuid(), sa.ForeignKey("plugin_vfx_files_output_package.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(1024), nullable=False),
        sa.Column("provider_object_id", sa.String(255), nullable=False),
        sa.Column("frame_number", sa.Integer()),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_format", sa.String(32)),
        sa.Column("color_space", sa.String(128)),
        sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("package_id", "ordinal", name="plugin_vfx_files_output_package_file_ordinal_uc"),
        sa.UniqueConstraint("package_id", "provider_object_id", name="plugin_vfx_files_output_package_provider_object_uc"),
    )
    op.create_index("plugin_vfx_files_output_package_file_package_ix", "plugin_vfx_files_output_package_file", ["package_id"])
    op.create_table(
        "plugin_vfx_files_output_package_pointer",
        sa.Column("task_id", _uuid(), primary_key=True),
        sa.Column("output_type_id", _uuid(), primary_key=True),
        sa.Column("package_type", sa.String(64), primary_key=True),
        sa.Column("latest_output_id", _uuid(), sa.ForeignKey("plugin_vfx_files_output_package.id")),
        sa.Column("latest_published_id", _uuid(), sa.ForeignKey("plugin_vfx_files_output_package.id")),
        sa.Column("latest_approved_id", _uuid(), sa.ForeignKey("plugin_vfx_files_output_package.id")),
        sa.Column("latest_final_delivery_id", _uuid(), sa.ForeignKey("plugin_vfx_files_output_package.id")),
    )
    op.execute("""
        CREATE FUNCTION plugin_vfx_files_reject_output_file_mutation() RETURNS trigger AS $$
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
    op.execute(
        "CREATE TRIGGER plugin_vfx_files_output_package_file_immutable "
        "BEFORE INSERT OR UPDATE OR DELETE ON plugin_vfx_files_output_package_file "
        "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_output_file_mutation()"
    )
    op.execute("""
        CREATE FUNCTION plugin_vfx_files_reject_completed_package_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.state = 'completed' THEN
                RAISE EXCEPTION 'VFX Files completed Output Packages are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER plugin_vfx_files_output_package_immutable "
        "BEFORE UPDATE OR DELETE ON plugin_vfx_files_output_package "
        "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_completed_package_mutation()"
    )


def downgrade():
    bind = op.get_bind()
    has_package_rows = bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_output_package)"))
    has_team_handoff_rows = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_handoff WHERE recipient_department_id IS NOT NULL)"
    ))
    if has_package_rows or has_team_handoff_rows:
        raise RuntimeError(
            "vxf_0007 contains Output Package or Department Handoff data; restore a consistent pre-migration snapshot instead of downgrading"
        )
    op.execute("DROP TRIGGER plugin_vfx_files_output_package_immutable ON plugin_vfx_files_output_package")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_completed_package_mutation()")
    op.execute("DROP TRIGGER plugin_vfx_files_output_package_file_immutable ON plugin_vfx_files_output_package_file")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_output_file_mutation()")
    op.drop_table("plugin_vfx_files_output_package_pointer")
    op.drop_index("plugin_vfx_files_output_package_file_package_ix", table_name="plugin_vfx_files_output_package_file")
    op.drop_table("plugin_vfx_files_output_package_file")
    for column in ("production_id", "entity_id", "task_id"):
        op.drop_index(f"plugin_vfx_files_output_package_{column}_ix", table_name="plugin_vfx_files_output_package")
    op.drop_table("plugin_vfx_files_output_package")
    op.drop_index("plugin_vfx_files_handoff_pending_department_task_uc", table_name="plugin_vfx_files_handoff")
    op.drop_index("plugin_vfx_files_handoff_pending_person_uc", table_name="plugin_vfx_files_handoff")
    op.drop_constraint("plugin_vfx_files_handoff_target_ck", "plugin_vfx_files_handoff", type_="check")
    op.drop_column("plugin_vfx_files_handoff", "recipient_person_id")
    op.alter_column("plugin_vfx_files_handoff", "recipient_id", existing_type=_uuid(), nullable=False)
    op.drop_column("plugin_vfx_files_handoff", "recipient_task_id")
    op.drop_column("plugin_vfx_files_handoff", "recipient_department_id")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 5 WHERE id = 1")
