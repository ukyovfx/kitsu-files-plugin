"""Add Output version deletion records and the Production deletion-permission setting.

Revision ID: vxf_0021
Revises: vxf_0020
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "vxf_0021"
down_revision = "vxf_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_vfx_files_production_output_setting",
        sa.Column("production_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("output_delete_policy", sa.String(32), nullable=False, server_default="registrant_and_admin"),
        sa.Column("updated_by", postgresql.UUID(as_uuid=False)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("output_delete_policy IN ('registrant_and_admin','admin_only')", name="plugin_vfx_files_output_setting_policy_ck"),
    )
    op.create_table(
        "plugin_vfx_files_output_package_deletion",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("package_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_output_package.id"), nullable=False, unique=True),
        sa.Column("production_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("state", sa.String(16), nullable=False, server_default="deleting"),
        sa.Column("impact_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('deleting','deleted')", name="plugin_vfx_files_output_deletion_state_ck"),
    )
    op.create_index("plugin_vfx_files_output_deletion_production_ix", "plugin_vfx_files_output_package_deletion", ["production_id"])
    # Deletion history is audit evidence: identity is fixed, a deleted row is final,
    # and rows can never be removed. Only the retry/completion fields may change.
    op.execute("""
        CREATE FUNCTION plugin_vfx_files_reject_output_deletion_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'VFX Files Output deletion history cannot be deleted';
            END IF;
            IF OLD.package_id IS DISTINCT FROM NEW.package_id
                OR OLD.production_id IS DISTINCT FROM NEW.production_id
                OR OLD.task_id IS DISTINCT FROM NEW.task_id
                OR OLD.requested_by IS DISTINCT FROM NEW.requested_by
                OR OLD.requested_at IS DISTINCT FROM NEW.requested_at
                OR OLD.impact_snapshot IS DISTINCT FROM NEW.impact_snapshot THEN
                RAISE EXCEPTION 'VFX Files Output deletion identity is immutable';
            END IF;
            IF OLD.state = 'deleted' AND (
                NEW.state IS DISTINCT FROM OLD.state
                OR NEW.result IS DISTINCT FROM OLD.result
                OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
            ) THEN
                RAISE EXCEPTION 'VFX Files completed Output deletion is final';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER plugin_vfx_files_output_deletion_immutable "
        "BEFORE UPDATE OR DELETE ON plugin_vfx_files_output_package_deletion "
        "FOR EACH ROW EXECUTE FUNCTION plugin_vfx_files_reject_output_deletion_mutation()"
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 20 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_records = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_output_package_deletion) "
        "OR EXISTS (SELECT 1 FROM plugin_vfx_files_production_output_setting)"
    ))
    if has_records:
        raise RuntimeError("Output deletion history or settings exist; restore a consistent snapshot instead of downgrading")
    op.execute("DROP TRIGGER plugin_vfx_files_output_deletion_immutable ON plugin_vfx_files_output_package_deletion")
    op.execute("DROP FUNCTION plugin_vfx_files_reject_output_deletion_mutation()")
    op.drop_index("plugin_vfx_files_output_deletion_production_ix", table_name="plugin_vfx_files_output_package_deletion")
    op.drop_table("plugin_vfx_files_output_package_deletion")
    op.drop_table("plugin_vfx_files_production_output_setting")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 19 WHERE id = 1")
