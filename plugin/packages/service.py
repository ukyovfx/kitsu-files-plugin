"""Transactional reservation of immutable, provider-referenced output packages."""

from dataclasses import dataclass
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..models import Job, OutputPackage, OutputPackageFile, OutputPackagePointer
from .manifest import canonical_package_manifest


class PackageConflict(Exception):
    pass


@dataclass(frozen=True)
class PackageReservation:
    package_id: UUID
    version: int
    state: str
    job_id: UUID


def reserve_output_package(
    session_factory,
    *, task_id: UUID, production_id: UUID, entity_id: UUID, output_type_id: UUID,
    package_type: str, actor_id: UUID, idempotency_key: str, files: list[dict],
    frame_start: int | None = None, frame_end: int | None = None,
    color_space: str | None = None, source_working_revision_id: UUID | None = None,
) -> PackageReservation:
    """Reserve a package version and verification job in one PostgreSQL commit."""
    manifest, manifest_bytes, manifest_sha256 = canonical_package_manifest(
        output_type_id=str(output_type_id), package_type=package_type, files=files,
        frame_start=frame_start, frame_end=frame_end, color_space=color_space,
    )
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("A bounded idempotency key is required")
    source_working_id = str(source_working_revision_id) if source_working_revision_id else None
    request_sha256 = hashlib.sha256(manifest_bytes + (source_working_id or "").encode("ascii")).hexdigest()
    with session_factory.begin() as session:
        job_key = f"output-package:{task_id}:{idempotency_key}"
        existing_job = session.scalar(select(Job).where(Job.idempotency_key == job_key))
        if existing_job is not None:
            if (existing_job.payload.get("request_sha256") != request_sha256
                    or existing_job.payload.get("actor_id") != str(actor_id)
                    or existing_job.payload.get("source_working_revision_id") != source_working_id):
                raise PackageConflict("Idempotency key is already used for a different package manifest")
            package_id = UUID(str(existing_job.payload["package_id"]))
            package = session.get(OutputPackage, package_id)
            if package is None:
                raise PackageConflict("Reserved package job has no package record")
            return PackageReservation(package.id, package.version, package.state, existing_job.id)

        pointer_key = {"task_id": task_id, "output_type_id": output_type_id, "package_type": package_type}
        session.execute(insert(OutputPackagePointer).values(**pointer_key).on_conflict_do_nothing())
        session.scalar(
            select(OutputPackagePointer).where(
                OutputPackagePointer.task_id == task_id,
                OutputPackagePointer.output_type_id == output_type_id,
                OutputPackagePointer.package_type == package_type,
            ).with_for_update()
        )
        latest = session.scalar(select(func.max(OutputPackage.version)).where(
            OutputPackage.task_id == task_id,
            OutputPackage.output_type_id == output_type_id,
            OutputPackage.package_type == package_type,
        )) or 0
        package_id = uuid4()
        package = OutputPackage(
            id=package_id, production_id=production_id, entity_id=entity_id, task_id=task_id,
            output_type_id=output_type_id, package_type=package_type, version=latest + 1,
            state="verifying", qc_state="pending", manifest_bytes=manifest_bytes,
            manifest_sha256=manifest_sha256, source_working_revision_id=source_working_revision_id,
            created_by=actor_id,
        )
        session.add(package)
        session.flush()
        for item in manifest["files"]:
            session.add(OutputPackageFile(
                id=uuid4(), package_id=package_id, ordinal=item["ordinal"],
                file_name=item["file_name"],
                source_provider_object_id=item["source_provider_object_id"],
                provider_object_id=None,
                frame_number=item["frame_number"], byte_count=item["byte_count"],
                sha256=item["sha256"], file_format=item["file_format"],
                color_space=item["color_space"], is_proxy=item["is_proxy"],
            ))
        job = Job(
            job_type="verify_output_package", idempotency_key=job_key,
            payload={"package_id": str(package_id), "task_id": str(task_id),
                     "actor_id": str(actor_id), "request_sha256": request_sha256,
                     "source_working_revision_id": source_working_id},
            state="queued",
        )
        session.add(job)
        session.flush()
        return PackageReservation(package_id, package.version, package.state, job.id)
