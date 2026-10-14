"""Bind a QC-passed Output Package to its exact formally approved Publish."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select

from ..models import (
    ApprovalEvent,
    OutputPackage,
    OutputPackageFile,
    OutputPackagePointer,
    OutputPackagePublication,
    Publish,
    WorkingRevision,
    WorkingRevisionFile,
)


class PackagePublicationConflict(Exception):
    pass


@dataclass(frozen=True)
class PackagePublicationResult:
    id: UUID
    package_id: UUID
    publish_id: UUID
    replayed: bool = False


def bind_approved_publish(
    session_factory, *, package_id: UUID, publish_id: UUID,
    actor_id: UUID, idempotency_key: str,
) -> PackagePublicationResult:
    if not idempotency_key or len(idempotency_key) > 255:
        raise ValueError("A bounded publication-link idempotency key is required")

    with session_factory.begin() as session:
        prior = session.scalar(select(OutputPackagePublication).where(
            OutputPackagePublication.idempotency_key == idempotency_key,
        ))
        if prior is not None:
            if (prior.package_id, prior.publish_id, prior.created_by) != (package_id, publish_id, actor_id):
                raise PackagePublicationConflict("Publication-link idempotency key is already used")
            return _result(prior, replayed=True)

        package = session.scalar(select(OutputPackage).where(
            OutputPackage.id == package_id,
        ).with_for_update())
        publish = session.scalar(select(Publish).where(
            Publish.id == publish_id,
        ).with_for_update())
        if package is None or publish is None:
            raise PackagePublicationConflict("Package or Publish is unavailable")
        existing_package = session.scalar(select(OutputPackagePublication).where(
            OutputPackagePublication.package_id == package_id,
        ))
        existing_publish = session.scalar(select(OutputPackagePublication).where(
            OutputPackagePublication.publish_id == publish_id,
        ))
        if existing_package is not None or existing_publish is not None:
            if existing_package is not None and existing_package.publish_id == publish_id:
                return _result(existing_package, replayed=True)
            raise PackagePublicationConflict("A Package or Publish is already linked to another publication")

        if (
            package.state != "completed" or package.qc_state != "passed"
            or publish.state != "completed" or publish.qc_state != "passed"
            or not publish.manifest_sha256 or not publish.sha256
            or package.task_id != publish.task_id
        ):
            raise PackagePublicationConflict("Package and Publish must be completed, QC-passed outputs for the same Task")

        package_files = session.scalars(select(OutputPackageFile).where(
            OutputPackageFile.package_id == package.id,
        ).order_by(OutputPackageFile.ordinal)).all()
        source_file = session.get(WorkingRevisionFile, publish.source_revision_file_id) if publish.source_revision_file_id else None
        source_revision = session.get(WorkingRevision, source_file.revision_id) if source_file is not None else None
        if (
            len(package_files) != 1 or package_files[0].is_proxy
            or source_file is None or source_revision is None
            or source_revision.state != "completed"
            or source_revision.id != package.source_working_revision_id
            or package_files[0].source_provider_object_id != source_file.provider_object_id
            or package_files[0].sha256 != publish.sha256
            or source_file.provider_sha256 != publish.sha256
        ):
            raise PackagePublicationConflict(
                "Only a single-file Package with exact Working-file provenance and checksum can link to this Publish"
            )

        if not _publish_has_active_approval(session, publish):
            raise PackagePublicationConflict("The exact formal Publish has no active approval")

        binding = OutputPackagePublication(
            id=uuid4(), package_id=package.id, publish_id=publish.id,
            created_by=actor_id, idempotency_key=idempotency_key,
        )
        session.add(binding)
        session.flush()
        refresh_package_publication_pointers(session, package.task_id)
        return _result(binding)


def approved_publish_candidates(session, package: OutputPackage) -> list[Publish]:
    """Return only exact single-file provenance matches, never name matches."""
    if package.state != "completed" or package.qc_state != "passed" or not package.source_working_revision_id:
        return []
    files = session.scalars(select(OutputPackageFile).where(
        OutputPackageFile.package_id == package.id,
    ).order_by(OutputPackageFile.ordinal)).all()
    if len(files) != 1 or files[0].is_proxy or not files[0].sha256:
        return []
    source = session.scalar(select(WorkingRevisionFile).where(
        WorkingRevisionFile.revision_id == package.source_working_revision_id,
        WorkingRevisionFile.provider_object_id == files[0].source_provider_object_id,
        WorkingRevisionFile.provider_sha256 == files[0].sha256,
        WorkingRevisionFile.state == "completed",
    ))
    if source is None:
        return []
    candidates = session.scalars(select(Publish).where(
        Publish.task_id == package.task_id,
        Publish.source_revision_file_id == source.id,
        Publish.state == "completed",
        Publish.qc_state == "passed",
        Publish.sha256 == files[0].sha256,
        Publish.manifest_sha256.is_not(None),
    ).order_by(Publish.version.desc())).all()
    return [publish for publish in candidates if _publish_has_active_approval(session, publish)]


def refresh_package_publication_pointers(session, task_id: UUID) -> None:
    """Recompute Package pointers from immutable links and current approvals."""
    packages = session.scalars(select(OutputPackage).where(
        OutputPackage.task_id == task_id,
    )).all()
    package_ids = [row.id for row in packages]
    if not package_ids:
        return
    bindings = session.scalars(select(OutputPackagePublication).where(
        OutputPackagePublication.package_id.in_(package_ids),
    )).all()
    by_package = {row.package_id: row for row in bindings}
    publishes = {
        row.id: row for row in session.scalars(select(Publish).where(
            Publish.id.in_([binding.publish_id for binding in bindings]),
        )).all()
    } if bindings else {}
    groups = {}
    for package in packages:
        key = (package.output_type_id, package.package_type)
        group = groups.setdefault(key, {"published": [], "approved": []})
        binding = by_package.get(package.id)
        publish = publishes.get(binding.publish_id) if binding else None
        if publish is None or publish.state != "completed":
            continue
        group["published"].append(package)
        if _publish_has_active_approval(session, publish):
            group["approved"].append(package)

    for (output_type_id, package_type), pointers in groups.items():
        pointer = session.get(OutputPackagePointer, (task_id, output_type_id, package_type))
        if pointer is None:
            pointer = OutputPackagePointer(
                task_id=task_id, output_type_id=output_type_id,
                package_type=package_type,
            )
            session.add(pointer)
            session.flush()
        pointer.latest_published_id = _newest(pointers["published"])
        pointer.latest_approved_id = _newest(pointers["approved"])


def _publish_has_active_approval(session, publish: Publish) -> bool:
    approved = ApprovalEvent
    revoked = ApprovalEvent.__table__.alias("revoked_package_publish_approval")
    return session.scalar(select(approved.id).select_from(approved).outerjoin(
        revoked, revoked.c.reverses_event_id == approved.id,
    ).where(
        approved.publish_id == publish.id,
        approved.event_type == "approved",
        approved.manifest_sha256 == publish.manifest_sha256,
        revoked.c.id.is_(None),
    ).limit(1)) is not None


def _newest(rows):
    return max(rows, key=lambda row: row.version).id if rows else None


def _result(binding, *, replayed=False):
    return PackagePublicationResult(
        id=binding.id, package_id=binding.package_id,
        publish_id=binding.publish_id, replayed=replayed,
    )
