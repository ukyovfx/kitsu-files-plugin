"""Idempotent upload transfer handler for the durable Files job queue."""

from contextlib import ExitStack
import hashlib
import tempfile
from uuid import UUID

from sqlalchemy import func, select

from ..jobs.queue import JobResult, LeasedJob
from ..models import Job, StorageConnection, UploadPart, UploadSession
from ..storage.base import AlreadyExists, AmbiguousOutcome, PermissionFailure


def make_upload_handler(session_factory, staging, adapter):
    """Build the worker's ``publish_source`` handler for resumable uploads.

    A provider ID is durably reserved under the current lease before any
    external write. Retries only inspect or write that exact ID; an unknown
    provider outcome is fenced as reconcile_required.
    """

    def transfer(job: LeasedJob) -> JobResult:
        try:
            upload_id = UUID(str(job.payload["upload_session_id"]))
        except (KeyError, ValueError, TypeError):
            return JobResult("failed", error_code="invalid_upload_payload")

        with session_factory.begin() as session:
            db_job = session.scalar(select(Job).where(Job.id == job.job_id).with_for_update())
            if (
                db_job is None
                or db_job.state != "leased"
                or db_job.lease_owner != job.worker_id
                or db_job.lease_generation != job.generation
                or db_job.lease_expires_at <= session.scalar(select(func.now()))
            ):
                return JobResult("retry", error_code="lease_lost_before_transfer")
            upload = session.scalar(select(UploadSession).where(UploadSession.id == upload_id).with_for_update())
            if upload is None or upload.state not in ("staged", "transferring", "reconcile_required"):
                return JobResult("failed", error_code="upload_not_transferable")
            connection = session.get(StorageConnection, upload.storage_connection_id)
            if connection is None or not connection.enabled:
                return JobResult("failed", error_code="storage_connection_unavailable")
            if upload.provider_object_id:
                provider_id = upload.provider_object_id
            else:
                provider_id = adapter.reserve_id(f"upload:{upload.id}")
                upload.provider_object_id = provider_id
            upload.state = "transferring"
            parent_id = connection.allowed_root_id
            expected_size = upload.expected_size
            expected_sha256 = upload.expected_sha256
            part_rows = session.scalars(
                select(UploadPart).where(UploadPart.session_id == upload_id).order_by(UploadPart.part_index)
            ).all()
            parts = [(part.staging_key, part.byte_start, part.byte_count, part.sha256, part.state) for part in part_rows]

        capabilities = adapter.capabilities(parent_id)
        if not capabilities.root_visible or not capabilities.can_add_children:
            raise PermissionFailure("Configured storage root is unavailable for writes")
        adapter.assert_parent_within_root(parent_id)

        existing = adapter.get(provider_id)
        if existing is not None:
            if adapter.verify(provider_id, expected_size, expected_sha256):
                return JobResult("completed", True, provider_id, expected_size, expected_sha256)
            return JobResult("reconcile_required", error_code="provider_object_mismatch")

        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as source:
            digest = hashlib.sha256()
            total = 0
            next_offset = 0
            for index, (key, offset, size, part_sha256, state) in enumerate(parts):
                if state != "staged" or offset != next_offset:
                    return JobResult("failed", error_code="staging_parts_incomplete")
                part_digest = hashlib.sha256()
                with staging.open(key) as part_stream:
                    while block := part_stream.read(1024 * 1024):
                        total += len(block)
                        next_offset += len(block)
                        digest.update(block)
                        part_digest.update(block)
                        source.write(block)
                if next_offset - offset != size or part_digest.hexdigest() != part_sha256:
                    return JobResult("failed", error_code="staging_part_corrupt")
            if total != expected_size or digest.hexdigest() != expected_sha256:
                return JobResult("failed", error_code="staging_source_corrupt")
            source.seek(0)
            try:
                adapter.upload(source, provider_id, expected_size, expected_sha256, parent_id)
            except AlreadyExists:
                pass
            except AmbiguousOutcome:
                try:
                    if adapter.verify(provider_id, expected_size, expected_sha256):
                        return JobResult("completed", True, provider_id, expected_size, expected_sha256)
                except AmbiguousOutcome:
                    pass
                return JobResult("reconcile_required", error_code="provider_outcome_ambiguous")

        try:
            verified = adapter.verify(provider_id, expected_size, expected_sha256)
        except AmbiguousOutcome:
            return JobResult("reconcile_required", error_code="provider_verification_ambiguous")
        if not verified:
            return JobResult("reconcile_required", error_code="provider_verification_failed")
        return JobResult("completed", True, provider_id, expected_size, expected_sha256)

    return transfer
