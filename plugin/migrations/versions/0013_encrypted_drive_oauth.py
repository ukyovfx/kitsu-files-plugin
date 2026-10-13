"""Add encrypted Google OAuth grants and single-use authorization intents.

Revision ID: vxf_0013
Revises: vxf_0012
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "vxf_0013"
down_revision = "vxf_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_vfx_files_drive_credential",
        sa.Column("connection_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_storage_connection.id"), primary_key=True),
        sa.Column("production_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("account_mode", sa.String(24), nullable=False),
        sa.Column("account_subject", sa.String(255), nullable=False),
        sa.Column("owner_subject", sa.String(255)),
        sa.Column("drive_id", sa.String(255)),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="active"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("account_mode IN ('my_drive','shared_drive')", name="plugin_vfx_files_credential_account_mode_ck"),
        sa.CheckConstraint("state IN ('active','revoking','revoke_pending','revoked','reauth_required')", name="plugin_vfx_files_credential_state_ck"),
        sa.CheckConstraint("(account_mode = 'my_drive' AND owner_subject = account_subject AND drive_id IS NULL) OR (account_mode = 'shared_drive' AND owner_subject IS NULL)", name="plugin_vfx_files_credential_identity_ck"),
    )
    op.create_index("plugin_vfx_files_drive_credential_production_ix", "plugin_vfx_files_drive_credential", ["production_id"])
    op.create_table(
        "plugin_vfx_files_drive_authorization_intent",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("production_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False),
        sa.Column("account_mode", sa.String(24), nullable=False),
        sa.Column("state_sha256", sa.String(64), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("verifier_nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("verifier_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("response_nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("response_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("state_sha256", name="plugin_vfx_files_drive_authorization_intent_state_sha256_key"),
        sa.UniqueConstraint("production_id", "idempotency_key", name="plugin_vfx_files_drive_auth_idem_uc"),
        sa.CheckConstraint("account_mode IN ('my_drive','shared_drive')", name="plugin_vfx_files_drive_auth_mode_ck"),
        sa.CheckConstraint("status IN ('pending','processing','connected','failed','expired')", name="plugin_vfx_files_drive_auth_state_ck"),
    )
    op.create_index("plugin_vfx_files_drive_auth_production_id_ix", "plugin_vfx_files_drive_authorization_intent", ["production_id"])
    op.create_index("plugin_vfx_files_drive_auth_expiry_ix", "plugin_vfx_files_drive_authorization_intent", ["expires_at"])
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 12 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_drive_credential)")):
        raise RuntimeError("vxf_0013 contains encrypted OAuth grants; restore a consistent snapshot instead of downgrading")
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_drive_authorization_intent)")):
        raise RuntimeError("vxf_0013 contains OAuth authorization audit state; restore a consistent snapshot instead of downgrading")
    op.drop_index("plugin_vfx_files_drive_auth_expiry_ix", table_name="plugin_vfx_files_drive_authorization_intent")
    op.drop_index("plugin_vfx_files_drive_auth_production_id_ix", table_name="plugin_vfx_files_drive_authorization_intent")
    op.drop_table("plugin_vfx_files_drive_authorization_intent")
    op.drop_index("plugin_vfx_files_drive_credential_production_ix", table_name="plugin_vfx_files_drive_credential")
    op.drop_table("plugin_vfx_files_drive_credential")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 12 WHERE id = 1")
