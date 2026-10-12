"""Durable Working File to immutable Publish artifact worker flow."""

import hashlib
import tempfile
from uuid import UUID

from sqlalchemy import func, select

from ..jobs.queue import JobResult
from ..models import (
    FolderBinding,
    Job,
    OutputFamilyMapping,
    Publish,
    StorageConnection,
    WorkingRevision,
    WorkingRevisionFile,
)
from ..storage.base import AlreadyExists, AmbiguousOutcome, PermissionFailure, resolve_storage_adapter
from ..worker.scope import publish_job_scope_matches
from .checkpoints import PublishConflict, advance_checkpoint, advance_latest_published
from .manifest import build_manifest, canonical_manifest_bytes, manifest_sha256
from .qc import run_basic_qc
from .service import consume_native_create_attempt, mark_native_create_ambiguous


_QC_POLICY = "vfx-files-basic-integrity-v1"
_QC_POLICY_SHA256 = hashlib.sha256(_QC_POLICY.encode("ascii")).hexdigest()


def _working_file_url(provider_object_id):
    return f"https://drive.google.com/file/d/{provider_object_id}/view"


def _ensure_native_working_file(session_factory, *, task, revision, source):
    """Idempotently mirror the selected plugin file as the native Zou source file."""
    from zou.app.models.working_file import WorkingFile
    from zou.app.services import files_service

    name = f"vfx-files-{source.id.hex}"
    path = _working_file_url(source.provider_object_id)
    with session_factory() as session:
        existing = session.scalar(
            select(WorkingFile).where(
                WorkingFile.task_id == task.id,
                WorkingFile.entity_id == task.entity_id,
                WorkingFile.name == name,
                WorkingFile.revision == 1,
            )
        )
        if existing is not None:
            if existing.path != path:
                raise PublishConflict("Native WorkingFile mirror conflicts with the verified provider object")
            return existing.id

    created = files_service.create_new_working_revision(
        str(task.id),
        str(revision.created_by),
        None,
        name=name,
        path=path,
        comment=f"VFX Files {revision.version_label if hasattr(revision, 'version_label') else f'w{revision.version:03d}'} source {source.file_name}",
        revision=1,
    )
    return UUID(str(created["id"]))


def make_publish_handler(session_factory, adapter_factory):
    def handle(job):
        try:
            publish_id = UUID(str(job.payload["publish_id"]))
        except (KeyError, ValueError, TypeError):
            return JobResult("failed", error_code="invalid_publish_payload")

        with session_factory.begin() as session:
            lease = session.scalar(
                select(Job).where(Job.id == job.job_id).with_for_update()
            )
            if (
                lease is None or lease.state != "leased" or lease.lease_owner != job.worker_id
                or lease.lease_generation != job.generation
                or lease.lease_expires_at <= session.scalar(select(func.now()))
            ):
                return JobResult("retry", error_code="lease_lost_before_publish")
            publish = session.scalar(select(Publish).where(Publish.id == publish_id).with_for_update())
            source = session.get(WorkingRevisionFile, publish.source_revision_file_id) if publish else None
            revision = session.get(WorkingRevision, source.revision_id) if source else None
            family = session.get(OutputFamilyMapping, publish.output_family_id) if publish else None
            connection = session.get(StorageConnection, publish.storage_connection_id) if publish else None
            binding = session.scalar(
                select(FolderBinding).where(
                    FolderBinding.storage_connection_id == publish.storage_connection_id,
                    FolderBinding.allowed_root_id == connection.allowed_root_id,
                    FolderBinding.provider_folder_id == family.publish_folder_id,
                    FolderBinding.state == "ready",
                )
            ) if publish and family and connection else None
            from zou.app.models.task import Task
            task = session.get(Task, publish.task_id) if publish else None
            if (
                publish is None or source is None or revision is None or family is None
                or connection is None or task is None or binding is None
                or not connection.enabled or not family.enabled
                or publish.state not in ("reserved", "source_ready", "artifact_verified", "qc_recorded", "manifest_verified", "native_linked")
                or revision.state != "completed" or source.state != "completed"
                or task.project_id != connection.production_id or family.production_id != connection.production_id
                or task.task_type_id != family.task_type_id
                or revision.task_id != task.id or not source.provider_object_id
            ):
                return JobResult("failed", error_code="publish_scope_unavailable")
            if not publish_job_scope_matches(
                task_id=task.id,
                created_by=publish.created_by,
                payload=job.payload,
            ):
                return JobResult("failed", error_code="publish_scope_mismatch")
            job_adapter = resolve_storage_adapter(adapter_factory, connection)
            source_id = source.provider_object_id
            source_size = source.provider_size
            source_sha256 = source.provider_sha256
            artifact_id = publish.provider_artifact_id
            root_id = connection.allowed_root_id
            destination_id = family.publish_folder_id
            source_file_id = source.id
            output_family_id = family.id
            task_id = task.id

        if artifact_id is None:
            reserved_artifact_id = job_adapter.reserve_id(f"publish:{publish_id}")
            with session_factory.begin() as session:
                lease = session.scalar(select(Job).where(Job.id == job.job_id).with_for_update())
                publish = session.scalar(select(Publish).where(Publish.id == publish_id).with_for_update())
                if (
                    lease is None or lease.state != "leased" or lease.lease_owner != job.worker_id
                    or lease.lease_generation != job.generation
                    or lease.lease_expires_at <= session.scalar(select(func.now()))
                    or publish is None
                ):
                    return JobResult("retry", error_code="lease_lost_before_artifact_reservation")
                if publish.provider_artifact_id is None:
                    publish.provider_artifact_id = reserved_artifact_id
                artifact_id = publish.provider_artifact_id

        capabilities = job_adapter.capabilities(root_id)
        if not capabilities.root_visible or not capabilities.can_add_children:
            raise PermissionFailure("Publish root is unavailable for writes")
        job_adapter.assert_parent_within_root(destination_id)
        if not job_adapter.verify(source_id, source_size, source_sha256):
            return JobResult("reconcile_required", error_code="publish_source_integrity_failed")

        if publish.state == "reserved":
            advance_checkpoint(
                session_factory, publish_id, "reserved", "source_ready",
                {
                    "source_revision_file_id": str(source_file_id),
                    "provider_readback_verified": True,
                    "provider_object_id": source_id,
                    "byte_count": source_size,
                    "sha256": source_sha256,
                },
            )

        stored_artifact = job_adapter.get(artifact_id)
        if stored_artifact is None:
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as buffer:
                byte_count, checksum = job_adapter.download_to(source_id, buffer)
                if byte_count != source_size or checksum != source_sha256:
                    return JobResult("reconcile_required", error_code="publish_source_download_mismatch")
                buffer.seek(0)
                try:
                    job_adapter.upload(buffer, artifact_id, source_size, source_sha256, destination_id)
                except AlreadyExists:
                    pass
                except AmbiguousOutcome:
                    pass
            stored_artifact = job_adapter.get(artifact_id)
        try:
            artifact_verified = bool(
                stored_artifact and not stored_artifact.trashed
                and stored_artifact.parent_id == destination_id
                and job_adapter.verify(artifact_id, source_size, source_sha256)
            )
        except AmbiguousOutcome:
            return JobResult("reconcile_required", error_code="publish_provider_readback_ambiguous")
        if not artifact_verified:
            return JobResult("reconcile_required", error_code="publish_provider_readback_failed")

        with session_factory() as session:
            current = session.get(Publish, publish_id)
        if current.state == "source_ready":
            advance_checkpoint(
                session_factory, publish_id, "source_ready", "artifact_verified",
                {
                    "provider_object_id": artifact_id,
                    "readback_verified": True,
                    "byte_count": source_size,
                    "sha256": source_sha256,
                },
            )

        with session_factory() as session:
            current = session.get(Publish, publish_id)
            current_state = current.state
        if current_state == "artifact_verified":
            qc = run_basic_qc(
                policy_id=_QC_POLICY,
                policy_sha256=_QC_POLICY_SHA256,
                byte_count=source_size,
                expected_size=source_size,
                artifact_sha256=source_sha256,
                readback_sha256=stored_artifact.sha256,
                artifact_id=artifact_id,
                root_verified=True,
            )
            advance_checkpoint(
                session_factory, publish_id, "artifact_verified", "qc_recorded",
                {
                    "policy_id": qc.policy_id,
                    "policy_sha256": qc.policy_sha256,
                    "passed": qc.passed,
                    "checks": qc.checks,
                },
            )
            if not qc.passed:
                return JobResult("failed", error_code="publish_qc_failed")
            current_state = "qc_recorded"
        if current_state == "qc_recorded":
            manifest = build_manifest(
                publish_id=publish_id,
                task_id=task_id,
                output_family_id=output_family_id,
                version=publish.version,
                artifact_id=artifact_id,
                byte_count=source_size,
                artifact_sha256=source_sha256,
                source_revision_file_id=source_file_id,
            )
            exact_bytes = canonical_manifest_bytes(manifest)
            advance_checkpoint(
                session_factory, publish_id, "qc_recorded", "manifest_verified",
                {"manifest_sha256": manifest_sha256(exact_bytes), "manifest_bytes_hex": exact_bytes.hex()},
            )
            current_state = "manifest_verified"

        with session_factory() as session:
            current = session.get(Publish, publish_id)
            native_working_file_id = current.source_working_file_id
        if current_state == "manifest_verified" and native_working_file_id is None:
            from zou.app.models.task import Task
            with session_factory() as session:
                source = session.get(WorkingRevisionFile, source_file_id)
                revision = session.get(WorkingRevision, source.revision_id)
                task = session.get(Task, task_id)
            native_working_file_id = _ensure_native_working_file(
                session_factory, task=task, revision=revision, source=source,
            )
            with session_factory.begin() as session:
                publish = session.scalar(select(Publish).where(Publish.id == publish_id).with_for_update())
                if publish.source_working_file_id is None:
                    publish.source_working_file_id = native_working_file_id

        if current_state == "manifest_verified":
            from ..kitsu_files.adapter import ZouOutputFileAdapter
            try:
                permit = consume_native_create_attempt(
                    session_factory,
                    publish_id=publish_id,
                    worker_id=job.worker_id,
                    generation=job.generation,
                )
            except PublishConflict:
                try:
                    mark_native_create_ambiguous(session_factory, publish_id=publish_id, evidence_reference="permit-already-consumed")
                except PublishConflict:
                    pass
                return JobResult("reconcile_required", error_code="native_output_create_requires_reconciliation")
            try:
                native_link = ZouOutputFileAdapter().create_native_output(permit, entity_id=str(task.entity_id))
                from zou.app.models.output_file import OutputFile
                with session_factory.begin() as session:
                    native = session.get(OutputFile, UUID(native_link.output_file_id))
                    native.path = _working_file_url(artifact_id)
                    native.size = source_size
                    native.data = {
                        "vfx_files_publish_id": str(publish_id),
                        "manifest_sha256": current.manifest_sha256,
                        "sha256": source_sha256,
                        "provider_object_id": artifact_id,
                    }
                advance_checkpoint(
                    session_factory, publish_id, "manifest_verified", "native_linked",
                    {"native_output_file_id": native_link.output_file_id, "native_revision": native_link.native_revision},
                )
            except Exception:
                try:
                    mark_native_create_ambiguous(session_factory, publish_id=publish_id, evidence_reference="native-output-create-outcome-uncertain")
                except PublishConflict:
                    pass
                return JobResult("reconcile_required", error_code="native_output_create_ambiguous")
            current_state = "native_linked"

        if current_state == "native_linked":
            advance_checkpoint(session_factory, publish_id, "native_linked", "completed", {"eligible": True})
            advance_latest_published(session_factory, publish_id)
        return JobResult("completed", True, artifact_id, source_size, source_sha256)

    return handle
