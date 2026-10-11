"""Plugin-owned state only; no Kitsu core table is modified by this schema."""

from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy_utils import UUIDType

from zou.app import db


class PluginState(db.Model):
    __tablename__ = "plugin_vfx_files_state"
    id = db.Column(Integer, primary_key=True)
    schema_epoch = db.Column(Integer, nullable=False)
    updated_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StorageConnection(db.Model):
    __tablename__ = "plugin_vfx_files_storage_connection"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    production_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    provider = db.Column(String(32), nullable=False)
    account_mode = db.Column(String(24), nullable=False)
    owner_subject = db.Column(String(255))
    drive_id = db.Column(String(255))
    allowed_root_id = db.Column(String(255), nullable=False)
    credential_ref = db.Column(String(255), nullable=False)
    max_file_bytes = db.Column(BigInteger, nullable=False, default=10 * 1024 * 1024 * 1024)
    quota_bytes = db.Column(BigInteger, nullable=False, default=100 * 1024 * 1024 * 1024)
    used_bytes = db.Column(BigInteger, nullable=False, default=0)
    enabled = db.Column(Boolean, nullable=False, default=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class FolderBinding(db.Model):
    __tablename__ = "plugin_vfx_files_folder_binding"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    storage_connection_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False)
    allowed_root_id = db.Column(String(255), nullable=False)
    canonical_scope_key = db.Column(String(512), nullable=False)
    parent_provider_id = db.Column(String(255), nullable=False)
    provider_folder_id = db.Column(String(255))
    state = db.Column(String(32), nullable=False, default="reserved")
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("storage_connection_id", "allowed_root_id", "canonical_scope_key", name="plugin_vfx_files_folder_identity_uc"),)


class UploadSession(db.Model):
    __tablename__ = "plugin_vfx_files_upload_session"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    task_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    actor_id = db.Column(UUIDType(binary=False), nullable=False)
    storage_connection_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False)
    state = db.Column(String(32), nullable=False, default="receiving")
    file_name = db.Column(String(512), nullable=False, default="unnamed")
    expected_size = db.Column(BigInteger, nullable=False)
    expected_sha256 = db.Column(String(64), nullable=False)
    received_size = db.Column(BigInteger, nullable=False, default=0)
    staging_key = db.Column(String(512), nullable=False)
    provider_object_id = db.Column(String(255))
    provider_size = db.Column(BigInteger)
    provider_sha256 = db.Column(String(64))
    transferred_at = db.Column(DateTime(timezone=True))
    failure_code = db.Column(String(64))
    expires_at = db.Column(DateTime(timezone=True), nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UploadPart(db.Model):
    __tablename__ = "plugin_vfx_files_upload_part"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    session_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_upload_session.id"), nullable=False)
    part_index = db.Column(Integer, nullable=False)
    byte_start = db.Column(BigInteger, nullable=False)
    byte_count = db.Column(BigInteger, nullable=False)
    sha256 = db.Column(String(64), nullable=False)
    staging_key = db.Column(String(512), nullable=False)
    state = db.Column(String(24), nullable=False, default="reserved")
    __table_args__ = (UniqueConstraint("session_id", "part_index", name="plugin_vfx_files_upload_part_uc"),)


class Job(db.Model):
    __tablename__ = "plugin_vfx_files_job"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    job_type = db.Column(String(64), nullable=False)
    idempotency_key = db.Column(String(512), nullable=False, unique=True)
    payload = db.Column(JSONB, nullable=False)
    state = db.Column(String(32), nullable=False, default="queued")
    attempts = db.Column(Integer, nullable=False, default=0)
    max_attempts = db.Column(Integer, nullable=False, default=5)
    available_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_owner = db.Column(UUIDType(binary=False))
    lease_generation = db.Column(Integer, nullable=False, default=0)
    lease_expires_at = db.Column(DateTime(timezone=True))
    last_error_code = db.Column(String(64))
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OutputFamilyMapping(db.Model):
    __tablename__ = "plugin_vfx_files_output_family"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    production_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    family_key = db.Column(String(128), nullable=False)
    output_type_id = db.Column(UUIDType(binary=False), nullable=False)
    task_type_id = db.Column(UUIDType(binary=False), nullable=False)
    name = db.Column(String(250), nullable=False)
    representation = db.Column(String(20), nullable=False, default="")
    extension = db.Column(String(10), nullable=False, default="")
    file_status_id = db.Column(UUIDType(binary=False), nullable=False)
    enabled = db.Column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint("production_id", "family_key", name="plugin_vfx_files_output_family_key_uc"),)


class Publish(db.Model):
    __tablename__ = "plugin_vfx_files_publish"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    task_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    output_family_id = db.Column(UUIDType(binary=False), nullable=False)
    version = db.Column(Integer, nullable=False)
    idempotency_key = db.Column(String(128), unique=True)
    source_working_file_id = db.Column(UUIDType(binary=False))
    created_by = db.Column(UUIDType(binary=False), nullable=False)
    state = db.Column(String(32), nullable=False, default="reserved")
    byte_count = db.Column(BigInteger)
    sha256 = db.Column(String(64))
    storage_connection_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_storage_connection.id"))
    provider_artifact_id = db.Column(String(255))
    manifest_sha256 = db.Column(String(64))
    manifest_bytes = db.Column(db.LargeBinary)
    native_output_file_id = db.Column(UUIDType(binary=False))
    native_revision = db.Column(Integer)
    native_create_permit_used = db.Column(Boolean, nullable=False, default=False)
    native_retry_count = db.Column(Integer, nullable=False, default=0)
    qc_state = db.Column(String(24))
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("task_id", "output_family_id", "version", name="plugin_vfx_files_publish_version_uc"),)


class PublishPointer(db.Model):
    __tablename__ = "plugin_vfx_files_publish_pointer"
    task_id = db.Column(UUIDType(binary=False), primary_key=True)
    output_family_id = db.Column(UUIDType(binary=False), primary_key=True)
    latest_published_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_publish.id"))
    latest_approved_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_publish.id"))


class PublishCheckpoint(db.Model):
    __tablename__ = "plugin_vfx_files_publish_checkpoint"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    publish_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_publish.id"), nullable=False, index=True)
    checkpoint = db.Column(String(32), nullable=False)
    evidence = db.Column(JSONB, nullable=False)
    evidence_sha256 = db.Column(String(64), nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("publish_id", "checkpoint", name="plugin_vfx_files_publish_checkpoint_uc"),)


class NativeCreateEvent(db.Model):
    __tablename__ = "plugin_vfx_files_native_create_event"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    publish_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_publish.id"), nullable=False, index=True)
    event_type = db.Column(String(32), nullable=False)
    actor_id = db.Column(UUIDType(binary=False))
    evidence = db.Column(JSONB, nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ApprovalEvent(db.Model):
    __tablename__ = "plugin_vfx_files_approval_event"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    publish_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_publish.id"), nullable=False, index=True)
    actor_id = db.Column(UUIDType(binary=False), nullable=False)
    event_type = db.Column(String(16), nullable=False)
    reverses_event_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_approval_event.id"))
    idempotency_key = db.Column(String(128), nullable=False, unique=True)
    policy_id = db.Column(String(128), nullable=False)
    policy_sha256 = db.Column(String(64), nullable=False)
    manifest_sha256 = db.Column(String(64), nullable=False)
    reason = db.Column(Text)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PinnedDependency(db.Model):
    __tablename__ = "plugin_vfx_files_pinned_dependency"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    task_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    publish_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_publish.id"), nullable=False)
    manifest_sha256 = db.Column(String(64), nullable=False)
    created_by = db.Column(UUIDType(binary=False), nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Handoff(db.Model):
    __tablename__ = "plugin_vfx_files_handoff"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    task_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    publish_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_publish.id"), nullable=False)
    sender_id = db.Column(UUIDType(binary=False), nullable=False)
    recipient_id = db.Column(UUIDType(binary=False), nullable=False)
    idempotency_key = db.Column(String(128), nullable=False, unique=True)
    state = db.Column(String(24), nullable=False, default="pending")
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class HandoffEvent(db.Model):
    __tablename__ = "plugin_vfx_files_handoff_event"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    handoff_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_handoff.id"), nullable=False, index=True)
    actor_id = db.Column(UUIDType(binary=False), nullable=False)
    event_type = db.Column(String(24), nullable=False)
    feedback = db.Column(Text)
    idempotency_key = db.Column(String(128), nullable=False, unique=True)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ReconciliationFinding(db.Model):
    __tablename__ = "plugin_vfx_files_reconciliation_finding"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    object_type = db.Column(String(32), nullable=False)
    kitsu_object_id = db.Column(UUIDType(binary=False), nullable=False)
    state = db.Column(String(32), nullable=False, default="orphaned")
    evidence = db.Column(JSONB, nullable=False, default=dict)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("object_type", "kitsu_object_id", name="plugin_vfx_files_reconciliation_identity_uc"),)
