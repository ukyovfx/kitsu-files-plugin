"""Add personal (per Kitsu user) Google OAuth grants and the per-Production allow/block setting.

Revision ID: vxf_0022
Revises: vxf_0021
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "vxf_0022"
down_revision = "vxf_0021"
branch_labels = None
depends_on = None


def upgrade():
    uuid = postgresql.UUID(as_uuid=False)
    op.add_column(
        "plugin_vfx_files_production_output_setting",
        sa.Column("personal_google_policy", sa.String(16), nullable=False, server_default="allowed"),
    )
    op.create_check_constraint(
        "plugin_vfx_files_personal_google_policy_ck", "plugin_vfx_files_production_output_setting",
        "personal_google_policy IN ('allowed','blocked')",
    )
    op.create_table(
        "plugin_vfx_files_google_user_authorization_intent",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("actor_id", uuid, nullable=False),
        sa.Column("production_id", uuid, nullable=False),
        sa.Column("state_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("verifier_nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("verifier_ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("response_nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("response_ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="plugin_vfx_files_google_user_oauth_idem_uc"),
        sa.CheckConstraint("status IN ('pending','processing','connected','failed','expired')", name="plugin_vfx_files_google_user_oauth_state_ck"),
    )
    op.create_index("plugin_vfx_files_google_user_oauth_actor_ix", "plugin_vfx_files_google_user_authorization_intent", ["actor_id"])
    op.create_index("plugin_vfx_files_google_user_oauth_expiry_ix", "plugin_vfx_files_google_user_authorization_intent", ["expires_at"])
    op.create_table(
        "plugin_vfx_files_google_user_credential",
        sa.Column("actor_id", uuid, primary_key=True),
        sa.Column("google_subject", sa.String(255), unique=True),
        sa.Column("key_id", sa.String(64)),
        sa.Column("nonce", sa.LargeBinary(12)),
        sa.Column("ciphertext", sa.LargeBinary),
        sa.Column("granted_scopes", sa.String(512)),
        sa.Column("state", sa.String(24), nullable=False, server_default="revoked"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("state IN ('active','revoking','revoke_pending','revoked','reauth_required')", name="plugin_vfx_files_google_user_credential_state_ck"),
        sa.CheckConstraint(
            "(state = 'revoked' AND google_subject IS NULL AND ciphertext IS NULL) OR "
            "(state <> 'revoked' AND google_subject IS NOT NULL AND key_id IS NOT NULL AND nonce IS NOT NULL AND ciphertext IS NOT NULL)",
            name="plugin_vfx_files_google_user_credential_material_ck",
        ),
    )
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 21 WHERE id = 1")


def downgrade():
    bind = op.get_bind()
    has_data = bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM plugin_vfx_files_google_user_credential) "
        "OR EXISTS (SELECT 1 FROM plugin_vfx_files_google_user_authorization_intent) "
        "OR EXISTS (SELECT 1 FROM plugin_vfx_files_production_output_setting WHERE personal_google_policy <> 'allowed')"
    ))
    if has_data:
        raise RuntimeError("Personal Google grants or settings exist; restore a consistent snapshot instead of downgrading")
    op.drop_table("plugin_vfx_files_google_user_credential")
    op.drop_index("plugin_vfx_files_google_user_oauth_expiry_ix", table_name="plugin_vfx_files_google_user_authorization_intent")
    op.drop_index("plugin_vfx_files_google_user_oauth_actor_ix", table_name="plugin_vfx_files_google_user_authorization_intent")
    op.drop_table("plugin_vfx_files_google_user_authorization_intent")
    op.drop_constraint("plugin_vfx_files_personal_google_policy_ck", "plugin_vfx_files_production_output_setting", type_="check")
    op.drop_column("plugin_vfx_files_production_output_setting", "personal_google_policy")
    op.execute("UPDATE plugin_vfx_files_state SET schema_epoch = 20 WHERE id = 1")
