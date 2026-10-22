"""Immutable provider copy, readback, manifest and sequence QC handler."""

import hashlib
import json
import tempfile
from uuid import UUID

from sqlalchemy import func, select

from ..jobs.queue import JobResult
from ..models import Job, OutputPackage, OutputPackageFile, OutputPackagePointer
from ..storage.base import (
    AlreadyExists,
    AmbiguousOutcome,
    PermissionFailure,
    is_folder,
    resolve_storage_adapter,
)
from .manifest import _SHA256, evaluate_package_qc


def _lease_is_current(session, job, lease):
    return bool(
        lease is not None and lease.state == "leased"
        and lease.lease_owner == job.worker_id
        and lease.lease_generation == job.generation
        and lease.lease_expires_at > session.scalar(select(func.now()))
    )


def _persist_reserved_id(session_factory, job, *, package_id, file_id=None, provider_id):
    """Persist an immutable target ID before its provider create/upload call."""
    with session_factory.begin() as session:
        lease = session.scalar(select(Job).where(Job.id == job.job_id).with_for_update())
        package = session.scalar(select(OutputPackage).where(OutputPackage.id == package_id).with_for_update())
        if not _lease_is_current(session, job, lease) or package is None:
            return False
        if file_id is None:
            if package.provider_folder_id is None:
                package.provider_folder_id = provider_id
            return package.provider_folder_id == provider_id
        file_row = session.scalar(select(OutputPackageFile).where(OutputPackageFile.id == file_id).with_for_update())
        if file_row is None:
            return False
        if file_row.provider_object_id is None:
            file_row.provider_object_id = provider_id
        return file_row.provider_object_id == provider_id


def make_output_package_handler(session_factory, adapter_factory):
    def verify_package(job):
        try:
            package_id = UUID(str(job.payload["package_id"]))
            task_id = UUID(str(job.payload["task_id"]))
        except (KeyError, ValueError, TypeError):
            return JobResult("failed", error_code="invalid_output_package_payload")

        with session_factory.begin() as session:
            lease = session.scalar(select(Job).where(Job.id == job.job_id).with_for_update())
            if not _lease_is_current(session, job, lease):
                return JobResult("retry", error_code="lease_lost_before_package_verify")
            package = session.scalar(select(OutputPackage).where(OutputPackage.id == package_id).with_for_update())
            from zou.app.models.task import Task
            task = session.get(Task, task_id)
            if package is None or package.state not in ("verifying", "reconcile_required"):
                if package is not None and package.state == "completed":
                    return JobResult("completed")
                return JobResult("failed", error_code="package_not_verifiable")
            from ..integrations.storage_authority import selected_storage_connection

            connection = selected_storage_connection(session, package.production_id)
            if (task is None or task.id != package.task_id or task.project_id != package.production_id
                    or task.entity_id != package.entity_id or task_id != package.task_id
                    or str(job.payload.get("actor_id")) != str(package.created_by)
                    or connection is None):
                return JobResult("failed", error_code="output_package_scope_unavailable")
            if not connection.allowed_root_id:
                return JobResult("failed", error_code="output_package_root_unavailable")
            expected_manifest = bytes(package.manifest_bytes)
            expected_manifest_sha256 = package.manifest_sha256
            folder_id = package.provider_folder_id
            files = session.scalars(select(OutputPackageFile).where(
                OutputPackageFile.package_id == package.id,
            ).order_by(OutputPackageFile.ordinal)).all()
            file_rows = [{
                "id": row.id,
                "source_provider_object_id": row.source_provider_object_id,
                "provider_object_id": row.provider_object_id,
                "file_name": row.file_name,
                "byte_count": row.byte_count,
                "sha256": row.sha256,
                "frame_number": row.frame_number,
                "file_format": row.file_format,
                "color_space": row.color_space,
                "is_proxy": row.is_proxy,
            } for row in files]
            allowed_root_id = connection.allowed_root_id

        if hashlib.sha256(expected_manifest).hexdigest() != expected_manifest_sha256:
            return JobResult("reconcile_required", error_code="output_package_manifest_digest_mismatch")
        try:
            manifest = json.loads(expected_manifest)
        except (UnicodeDecodeError, ValueError):
            return JobResult("failed", error_code="output_package_manifest_invalid")
        if (not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list)
                or len(manifest["files"]) != len(file_rows)
                or not _SHA256.fullmatch(expected_manifest_sha256 or "")):
            return JobResult("failed", error_code="output_package_manifest_invalid")

        adapter = resolve_storage_adapter(adapter_factory, connection)
        from ..integrations.storage_authority import is_selected_storage_connection

        if not is_selected_storage_connection(session_factory, package.production_id, connection.id):
            return JobResult("blocked_authorization", error_code="storage_selection_changed")
        capabilities = adapter.capabilities(allowed_root_id)
        if not capabilities.root_visible or not capabilities.can_add_children:
            raise PermissionFailure("Output Package storage root is unavailable for writes")
        adapter.assert_parent_within_root(allowed_root_id)

        if folder_id is None:
            folder_id = adapter.reserve_id(f"output-package-folder:{package_id}")
            if not _persist_reserved_id(session_factory, job, package_id=package_id, provider_id=folder_id):
                return JobResult("retry", error_code="lease_lost_before_package_folder_reservation")
        folder_name = f"output-package-{package_id.hex}"
        if not is_selected_storage_connection(session_factory, package.production_id, connection.id):
            return JobResult("blocked_authorization", error_code="storage_selection_changed")
        try:
            folder = adapter.create_folder(
                allowed_root_id, folder_id,
                {"scope_key": f"output-package:{package_id}", "display_name": folder_name},
            )
        except (AlreadyExists, AmbiguousOutcome):
            folder = adapter.get(folder_id)
        if (folder is None or folder.object_id != folder_id or folder.parent_id != allowed_root_id
                or not is_folder(folder) or folder.trashed or folder.name != folder_name):
            return JobResult("reconcile_required", error_code="output_package_folder_unconfirmed")
        adapter.assert_parent_within_root(folder.object_id)

        verified_files = []
        for item, row in zip(manifest["files"], file_rows, strict=True):
            source_id = row["source_provider_object_id"]
            target_id = row["provider_object_id"]
            if (item.get("source_provider_object_id"), item.get("provider_object_id"),
                    item.get("file_name"), item.get("byte_count"), item.get("sha256"),
                    item.get("frame_number"), item.get("file_format"), item.get("color_space"), item.get("is_proxy")) != (
                    source_id, target_id, row["file_name"], row["byte_count"], row["sha256"],
                    row["frame_number"], row["file_format"], row["color_space"], row["is_proxy"]):
                return JobResult("reconcile_required", error_code="output_package_metadata_mismatch")
            if not source_id:
                return JobResult("failed", error_code="output_package_source_id_missing")
            source_object = adapter.get(source_id)
            if (source_object is None or source_object.trashed or is_folder(source_object)
                    or source_object.parent_id is None or source_object.size != row["byte_count"]):
                return JobResult("reconcile_required", error_code="output_package_source_unavailable")
            adapter.assert_parent_within_root(source_object.parent_id)

            if target_id is None:
                target_id = adapter.reserve_id(f"output-package-file:{row['id']}")
                if not _persist_reserved_id(
                    session_factory, job, package_id=package_id, file_id=row["id"], provider_id=target_id,
                ):
                    return JobResult("retry", error_code="lease_lost_before_package_file_reservation")

            # One source object is streamed at a time. SpooledTemporaryFile spills
            # large files to the worker's configured local staging disk, not Zou's
            # request process or the browser. The provider transfer is resumable by
            # the reserved destination ID and never overwrites an unrelated object.
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as media:
                source_size, source_sha256 = adapter.download_to(source_id, media)
                expected_sha256 = row["sha256"]
                if (source_size != row["byte_count"]
                        or (expected_sha256 and expected_sha256 != source_sha256)):
                    return JobResult("reconcile_required", error_code="output_package_source_readback_failed")
                media.seek(0)
                if not is_selected_storage_connection(session_factory, package.production_id, connection.id):
                    return JobResult("blocked_authorization", error_code="storage_selection_changed")
                try:
                    adapter.upload(media, target_id, source_size, source_sha256, folder_id, file_name=row["file_name"])
                except (AlreadyExists, AmbiguousOutcome):
                    pass

            target_object = adapter.get(target_id)
            if (target_object is None or target_object.trashed or target_object.object_id != target_id
                    or target_object.parent_id != folder_id or target_object.name != row["file_name"]
                    or is_folder(target_object)):
                return JobResult("reconcile_required", error_code="output_package_target_unconfirmed")
            if not adapter.verify(target_id, row["byte_count"], source_sha256):
                return JobResult("reconcile_required", error_code="output_package_target_readback_failed")
            item["provider_object_id"] = target_id
            item["sha256"] = source_sha256
            verified_files.append((row["id"], target_id, source_sha256))

        try:
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            qc_state, qc_report = evaluate_package_qc(manifest)
        except ValueError:
            return JobResult("failed", error_code="output_package_frame_qc_invalid")

        with session_factory.begin() as session:
            lease = session.scalar(select(Job).where(Job.id == job.job_id).with_for_update())
            package = session.scalar(select(OutputPackage).where(OutputPackage.id == package_id).with_for_update())
            if not _lease_is_current(session, job, lease) or package is None:
                return JobResult("retry", error_code="lease_lost_after_package_verify")
            from ..integrations.storage_authority import selected_storage_connection

            selected = selected_storage_connection(session, package.production_id)
            if selected is None or selected.id != connection.id:
                return JobResult("blocked_authorization", error_code="storage_selection_changed")
            if package.state == "completed":
                return JobResult("completed")
            for file_id, target_id, sha256 in verified_files:
                file_row = session.scalar(select(OutputPackageFile).where(OutputPackageFile.id == file_id).with_for_update())
                if file_row is None or (file_row.provider_object_id not in (None, target_id)):
                    return JobResult("reconcile_required", error_code="output_package_target_conflict")
                if file_row.sha256 is not None and file_row.sha256 != sha256:
                    return JobResult("reconcile_required", error_code="output_package_checksum_conflict")
                file_row.provider_object_id = target_id
                file_row.sha256 = sha256
            package.provider_folder_id = folder_id
            package.manifest_bytes = manifest_bytes
            package.manifest_sha256 = manifest_sha256
            package.state = "completed"
            package.qc_state = qc_state
            package.qc_report = qc_report
            package.completed_at = func.now()
            pointer = session.scalar(select(OutputPackagePointer).where(
                OutputPackagePointer.task_id == package.task_id,
                OutputPackagePointer.output_type_id == package.output_type_id,
                OutputPackagePointer.package_type == package.package_type,
            ).with_for_update())
            current_version = session.scalar(select(OutputPackage.version).where(
                OutputPackage.id == pointer.latest_output_id,
            )) if pointer and pointer.latest_output_id else None
            if pointer and (current_version is None or package.version > current_version):
                pointer.latest_output_id = package.id
            session.flush()
            session.refresh(package, attribute_names=["completed_at"])
            from ..integrations.event_producers import append_output_package_event

            append_output_package_event(
                session, package, event_type="output_package.ready",
                selected_connection_id=connection.id,
                expected_activation_epoch=package.event_activation_epoch,
            )
        return JobResult("completed")

    return verify_package
