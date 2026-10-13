"""Bind signed business events to the KitsuSync-selected storage connection.

Revision ID: vxf_0014
Revises: vxf_0013
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "vxf_0014"
down_revision = "vxf_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plugin_vfx_files_output_package",
        sa.Column("event_activation_epoch", sa.Integer(), nullable=True),
    )
    op.add_column(
        "plugin_vfx_files_kitsusync_event_outbox",
        sa.Column(
            "storage_connection_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("plugin_vfx_files_storage_connection.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "plugin_vfx_files_event_outbox_connection_ix",
        "plugin_vfx_files_kitsusync_event_outbox",
        ["storage_connection_id", "production_id", "state", "available_at"],
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION plugin_vfx_files_reject_completed_package_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP <> 'DELETE' AND OLD.event_activation_epoch IS DISTINCT FROM NEW.event_activation_epoch THEN
                RAISE EXCEPTION 'VFX Files Output Package event eligibility is immutable';
            END IF;
            IF TG_OP = 'DELETE' OR OLD.state = 'completed' THEN
                RAISE EXCEPTION 'VFX Files completed Output Packages are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION plugin_vfx_files_reject_ks_event_identity_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.production_id IS DISTINCT FROM NEW.production_id
                OR OLD.event_id IS DISTINCT FROM NEW.event_id
                OR OLD.event_type IS DISTINCT FROM NEW.event_type
                OR OLD.payload IS DISTINCT FROM NEW.payload
                OR OLD.payload_sha256 IS DISTINCT FROM NEW.payload_sha256
                OR OLD.storage_connection_id IS DISTINCT FROM NEW.storage_connection_id THEN
                RAISE EXCEPTION 'KitsuSync event identity, payload and connection binding are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 13 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_rows = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_kitsusync_event_outbox "
        "WHERE storage_connection_id IS NOT NULL) OR EXISTS (SELECT 1 "
        "FROM plugin_vfx_files_output_package WHERE event_activation_epoch IS NOT NULL)"
    ))
    if has_rows:
        raise RuntimeError(
            "vxf_0014 has event eligibility or delivery bindings; restore a consistent snapshot instead of downgrading"
        )
    op.execute("""
        CREATE OR REPLACE FUNCTION plugin_vfx_files_reject_completed_package_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.state = 'completed' THEN
                RAISE EXCEPTION 'VFX Files completed Output Packages are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION plugin_vfx_files_reject_ks_event_identity_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.production_id IS DISTINCT FROM NEW.production_id
                OR OLD.event_id IS DISTINCT FROM NEW.event_id
                OR OLD.event_type IS DISTINCT FROM NEW.event_type
                OR OLD.payload IS DISTINCT FROM NEW.payload
                OR OLD.payload_sha256 IS DISTINCT FROM NEW.payload_sha256 THEN
                RAISE EXCEPTION 'KitsuSync event identity and payload are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.drop_index(
        "plugin_vfx_files_event_outbox_connection_ix",
        table_name="plugin_vfx_files_kitsusync_event_outbox",
    )
    op.drop_column("plugin_vfx_files_kitsusync_event_outbox", "storage_connection_id")
    op.drop_column("plugin_vfx_files_output_package", "event_activation_epoch")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 12 WHERE id = 1")
