"""Plugin-owned state only; no Kitsu core table is modified by this schema."""

from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
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
    __table_args__ = (
        UniqueConstraint("storage_connection_id", "allowed_root_id", "canonical_scope_key", name="plugin_vfx_files_folder_identity_uc"),
        Index(
            "plugin_vfx_files_folder_provider_id_uc",
            "storage_connection_id",
            "allowed_root_id",
            "provider_folder_id",
            unique=True,
            postgresql_where=text("provider_folder_id IS NOT NULL"),
        ),
    )


class SharedTemplateConfiguration(db.Model):
    __tablename__ = "plugin_vfx_files_shared_template"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    production_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    storage_connection_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False)
    allowed_root_id = db.Column(String(255), nullable=False)
    template_folder_id = db.Column(String(255), nullable=False)
    template_tree = db.Column(JSONB, nullable=False)
    role_mappings = db.Column(JSONB, nullable=False)
    version = db.Column(Integer, nullable=False, default=1)
    enabled = db.Column(Boolean, nullable=False, default=False)
    updated_by = db.Column(UUIDType(binary=False), nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("production_id", "storage_connection_id", name="plugin_vfx_files_shared_template_scope_uc"),
    )


class UploadSession(db.Model):
    __tablename__ = "plugin_vfx_files_upload_session"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    task_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    actor_id = db.Column(UUIDType(binary=False), nullable=False)
    storage_connection_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False)
    working_revision_file_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_working_revision_file.id"))
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


class WorkingRevision(db.Model):
    __tablename__ = "plugin_vfx_files_working_revision"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    production_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    task_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    version = db.Column(Integer, nullable=False)
    base_revision_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_working_revision.id"))
    created_by = db.Column(UUIDType(binary=False), nullable=False)
    state = db.Column(String(32), nullable=False, default="receiving")
    idempotency_key = db.Column(String(128), nullable=False, unique=True)
    manifest_bytes = db.Column(db.LargeBinary())
    manifest_sha256 = db.Column(String(64))
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = db.Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("task_id", "version", name="plugin_vfx_files_working_version_uc"),)


class WorkingRevisionFile(db.Model):
    __tablename__ = "plugin_vfx_files_working_revision_file"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    revision_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_working_revision.id"), nullable=False, index=True)
    ordinal = db.Column(Integer, nullable=False)
    file_name = db.Column(String(512), nullable=False)
    mime_type = db.Column(String(255))
    category = db.Column(String(32), nullable=False, default="working")
    expected_size = db.Column(BigInteger, nullable=False)
    expected_sha256 = db.Column(String(64), nullable=False)
    provider_object_id = db.Column(String(255))
    provider_size = db.Column(BigInteger)
    provider_sha256 = db.Column(String(64))
    state = db.Column(String(32), nullable=False, default="receiving")
    __table_args__ = (UniqueConstraint("revision_id", "ordinal", name="plugin_vfx_files_working_file_ordinal_uc"),)


class WorkingDependency(db.Model):
    __tablename__ = "plugin_vfx_files_working_dependency"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    revision_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_working_revision.id"), nullable=False, index=True)
    dependency_type = db.Column(String(16), nullable=False)
    dependency_id = db.Column(UUIDType(binary=False), nullable=False)
    manifest_sha256 = db.Column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("revision_id", "dependency_type", "dependency_id", name="plugin_vfx_files_working_dependency_uc"),)


class LatestWorkingPointer(db.Model):
    __tablename__ = "plugin_vfx_files_latest_working"
    task_id = db.Column(UUIDType(binary=False), primary_key=True)
    revision_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_working_revision.id"), nullable=False)


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
    publish_folder_id = db.Column(String(255))
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
    source_revision_file_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_working_revision_file.id"))
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


class OutputPackage(db.Model):
    """Immutable, manifest-backed deliverable package (independent of Zou OutputFile numbering)."""
    __tablename__ = "plugin_vfx_files_output_package"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    production_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    entity_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    task_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    output_type_id = db.Column(UUIDType(binary=False), nullable=False)
    package_type = db.Column(String(64), nullable=False)
    version = db.Column(Integer, nullable=False)
    state = db.Column(String(24), nullable=False, default="receiving")
    manifest_bytes = db.Column(db.LargeBinary, nullable=False)
    manifest_sha256 = db.Column(String(64), nullable=False)
    source_working_revision_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_working_revision.id"))
    provider_folder_id = db.Column(String(255))
    created_by = db.Column(UUIDType(binary=False), nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = db.Column(DateTime(timezone=True))
    qc_state = db.Column(String(24), nullable=False, default="pending")
    qc_report = db.Column(JSONB)
    __table_args__ = (
        UniqueConstraint("task_id", "output_type_id", "package_type", "version", name="plugin_vfx_files_output_package_version_uc"),
    )


class OutputPackageFile(db.Model):
    """One immutable provider object entry in a package, including optional sequence frame metadata."""
    __tablename__ = "plugin_vfx_files_output_package_file"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    package_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_output_package.id"), nullable=False, index=True)
    ordinal = db.Column(Integer, nullable=False)
    file_name = db.Column(String(1024), nullable=False)
    source_provider_object_id = db.Column(String(255))
    provider_object_id = db.Column(String(255))
    frame_number = db.Column(Integer)
    byte_count = db.Column(BigInteger, nullable=False)
    sha256 = db.Column(String(64), nullable=True)
    file_format = db.Column(String(32))
    color_space = db.Column(String(128))
    is_proxy = db.Column(Boolean, nullable=False, default=False)
    __table_args__ = (
        UniqueConstraint("package_id", "ordinal", name="plugin_vfx_files_output_package_file_ordinal_uc"),
        UniqueConstraint("package_id", "provider_object_id", name="plugin_vfx_files_output_package_provider_object_uc"),
    )


class OutputPackagePointer(db.Model):
    """Independent latest pointers; no inference from Working/Publish/native revisions."""
    __tablename__ = "plugin_vfx_files_output_package_pointer"
    task_id = db.Column(UUIDType(binary=False), primary_key=True)
    output_type_id = db.Column(UUIDType(binary=False), primary_key=True)
    package_type = db.Column(String(64), primary_key=True)
    latest_output_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_output_package.id"))
    latest_published_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_output_package.id"))
    latest_approved_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_output_package.id"))
    latest_final_delivery_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_output_package.id"))


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
    recipient_id = db.Column(UUIDType(binary=False))
    recipient_department_id = db.Column(UUIDType(binary=False))
    recipient_task_id = db.Column(UUIDType(binary=False))
    recipient_person_id = db.Column(UUIDType(binary=False))
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


class KitsuSyncDesiredConfiguration(db.Model):
    """Latest KitsuSync-owned desired state; applied state is tracked separately."""
    __tablename__ = "plugin_vfx_files_kitsusync_desired_config"
    production_id = db.Column(UUIDType(binary=False), primary_key=True)
    config_revision = db.Column(Integer, nullable=False)
    notification_epoch = db.Column(Integer, nullable=False, default=0)
    desired_config = db.Column(JSONB, nullable=False)
    updated_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class KitsuSyncHierarchySnapshot(db.Model):
    """Bounded, KitsuSync-supplied hierarchy snapshot for one desired revision."""
    __tablename__ = "plugin_vfx_files_kitsusync_snapshot"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    production_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    config_revision = db.Column(Integer, nullable=False)
    source_etag = db.Column(String(200), nullable=False)
    page_count = db.Column(Integer, nullable=False)
    object_count = db.Column(Integer, nullable=False)
    hierarchy_sha256 = db.Column(String(64), nullable=False)
    state = db.Column(String(24), nullable=False, default="uploading")
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = db.Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("production_id", "config_revision", "hierarchy_sha256", name="plugin_vfx_files_kitsusync_snapshot_revision_sha_uc"),)


class KitsuSyncHierarchySnapshotPage(db.Model):
    __tablename__ = "plugin_vfx_files_kitsusync_snapshot_page"
    snapshot_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_kitsusync_snapshot.id", ondelete="CASCADE"), primary_key=True)
    page_index = db.Column(Integer, primary_key=True)
    page_sha256 = db.Column(String(64), nullable=False)
    objects = db.Column(JSONB, nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class KitsuSyncEventOutbox(db.Model):
    """Durable, idempotent Plugin-to-KitsuSync event delivery queue."""
    __tablename__ = "plugin_vfx_files_kitsusync_event_outbox"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    production_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    event_id = db.Column(String(255), nullable=False)
    event_type = db.Column(String(128), nullable=False)
    payload = db.Column(JSONB, nullable=False)
    payload_sha256 = db.Column(String(64), nullable=False)
    state = db.Column(String(24), nullable=False, default="queued")
    attempts = db.Column(Integer, nullable=False, default=0)
    available_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_owner = db.Column(UUIDType(binary=False))
    lease_generation = db.Column(Integer, nullable=False, default=0)
    lease_expires_at = db.Column(DateTime(timezone=True))
    last_response_code = db.Column(String(64))
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("event_id", name="plugin_vfx_files_kitsusync_event_id_uc"),
        UniqueConstraint("production_id", "event_id", name="plugin_vfx_files_kitsusync_event_production_uc"),
    )


class DriveReconciliationObservation(db.Model):
    """Append-only provider readback evidence for one bounded reconciliation."""
    __tablename__ = "plugin_vfx_files_drive_observation"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    reconciliation_id = db.Column(UUIDType(binary=False), nullable=False)
    production_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    storage_connection_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_storage_connection.id"), nullable=False)
    allowed_root_id = db.Column(String(255), nullable=False)
    provider_object_id = db.Column(String(255), nullable=False)
    state = db.Column(String(24), nullable=False)
    metadata_fingerprint = db.Column(String(64))
    provider_modified_at = db.Column(String(64))
    byte_count = db.Column(BigInteger)
    error_code = db.Column(String(64))
    observed_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("reconciliation_id", "provider_object_id", name="plugin_vfx_files_drive_observation_identity_uc"),
        Index("plugin_vfx_files_drive_observation_scope_ix", "storage_connection_id", "allowed_root_id", "observed_at"),
        db.CheckConstraint("state IN ('present','missing','modified','inaccessible','stale')", name="plugin_vfx_files_drive_observation_state_ck"),
    )


class OutputPackageDelivery(db.Model):
    """Independent receipt state for each receiving Task, referencing one package."""
    __tablename__ = "plugin_vfx_files_output_package_delivery"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    package_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_output_package.id"), nullable=False, index=True)
    production_id = db.Column(UUIDType(binary=False), nullable=False, index=True)
    recipient_task_id = db.Column(UUIDType(binary=False), nullable=False)
    recipient_task_type_id = db.Column(UUIDType(binary=False), nullable=False)
    delivery_revision = db.Column(Integer, nullable=False, default=1)
    state = db.Column(String(24), nullable=False, default="available")
    feedback = db.Column(Text)
    idempotency_key = db.Column(String(255), nullable=False, unique=True)
    created_by = db.Column(UUIDType(binary=False), nullable=False)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("package_id", "recipient_task_id", "delivery_revision", name="plugin_vfx_files_package_delivery_revision_uc"),)


class OutputPackageDeliveryEvent(db.Model):
    __tablename__ = "plugin_vfx_files_output_package_delivery_event"
    id = db.Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    delivery_id = db.Column(UUIDType(binary=False), ForeignKey("plugin_vfx_files_output_package_delivery.id"), nullable=False, index=True)
    actor_id = db.Column(UUIDType(binary=False), nullable=False)
    event_type = db.Column(String(24), nullable=False)
    feedback = db.Column(Text)
    idempotency_key = db.Column(String(255), nullable=False, unique=True)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
