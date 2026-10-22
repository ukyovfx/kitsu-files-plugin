"""Permanent deletion of one Output version's plugin-managed Drive files.

The Output Package row, its file rows, deliveries and review history are never
modified or removed. A durable deletion record marks the version deleted and a
queued job removes only the objects this plugin created for that version.
"""

from dataclasses import dataclass
import hashlib
import json
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..jobs.queue import JobResult
from ..models import (
    Job, LinkShare, OutputPackage, OutputPackageDeletion, OutputPackageDelivery,
    OutputPackageFile, OutputPackagePointer, ProductionOutputSetting,
)
from ..storage.base import (
    AmbiguousOutcome, ObjectNotFound, PermissionFailure, resolve_storage_adapter,
)
from .worker import _lease_is_current

POLICY_REGISTRANT_AND_ADMIN = "registrant_and_admin"
POLICY_ADMIN_ONLY = "admin_only"
DELETE_POLICIES = (POLICY_REGISTRANT_AND_ADMIN, POLICY_ADMIN_ONLY)
ADMIN_ROLES = frozenset({"admin", "manager"})
JOB_TYPE = "delete_output_package"
_TERMINAL_JOB_STATES = ("failed", "reconcile_required", "blocked_authorization")
_MAX_LISTING_PAGES = 1000


class DeletionDenied(Exception):
    pass


class DeletionNotFound(Exception):
    pass


class DeletionConflict(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DeletionRequest:
    package_id: UUID
    state: str
    job_id: UUID
    created: bool


def _job_is_stuck(session, job) -> bool:
    """Terminal, or out of attempts with nothing running. Nothing sweeps exhausted jobs, so check here."""
    if job.state in _TERMINAL_JOB_STATES:
        return True
    if job.attempts < job.max_attempts or job.state not in ("queued", "retry", "leased"):
        return False
    return job.state != "leased" or job.lease_expires_at is None         or job.lease_expires_at < session.scalar(select(func.now()))


def deletion_job_key(package_id) -> str:
    return f"delete-output-package:{package_id}"


def get_delete_policy(session, production_id) -> str:
    row = session.get(ProductionOutputSetting, production_id)
    policy = row.output_delete_policy if row is not None else POLICY_REGISTRANT_AND_ADMIN
    return policy if policy in DELETE_POLICIES else POLICY_ADMIN_ONLY


def may_delete(policy: str, role: str, actor_id, created_by) -> bool:
    """Server-side rule. Only the Zou-resolved role and the stored registrant ID are inputs."""
    if role in ADMIN_ROLES:
        return True
    return policy == POLICY_REGISTRANT_AND_ADMIN and str(actor_id) == str(created_by)


def set_delete_policy(session, production_id, policy: str, actor_id) -> None:
    if policy not in DELETE_POLICIES:
        raise ValueError("Unsupported Output deletion policy")
    session.execute(insert(ProductionOutputSetting).values(
        production_id=production_id, output_delete_policy=policy, updated_by=actor_id,
    ).on_conflict_do_update(
        index_elements=[ProductionOutputSetting.production_id],
        set_={"output_delete_policy": policy, "updated_by": actor_id, "updated_at": func.now()},
    ))


def build_delete_impact(session, package) -> dict:
    """Only references this plugin can actually verify; nothing is inferred."""
    deliveries = session.scalars(select(OutputPackageDelivery).where(
        OutputPackageDelivery.package_id == package.id,
    ).order_by(OutputPackageDelivery.id)).all()
    pointers = session.scalars(select(OutputPackagePointer).where(
        OutputPackagePointer.task_id == package.task_id,
        OutputPackagePointer.output_type_id == package.output_type_id,
        OutputPackagePointer.package_type == package.package_type,
    )).all()
    latest = sorted({
        name for pointer in pointers for name, value in (
            ("latest_output", pointer.latest_output_id), ("latest_published", pointer.latest_published_id),
            ("latest_approved", pointer.latest_approved_id), ("latest_final_delivery", pointer.latest_final_delivery_id),
        ) if value == package.id
    })
    return {
        "package_id": str(package.id),
        "version": package.version,
        "file_count": session.scalar(select(func.count()).select_from(OutputPackageFile).where(
            OutputPackageFile.package_id == package.id)) or 0,
        "deliveries": [{
            "id": str(row.id), "recipient_task_id": str(row.recipient_task_id),
            "revision": row.delivery_revision, "state": row.state,
        } for row in deliveries],
        "latest_pointers": latest,
    }


def impact_digest(impact: dict) -> str:
    return hashlib.sha256(json.dumps(impact, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def package_is_deleted(session, package_id) -> bool:
    """True from the moment deletion is requested, so downloads stop immediately."""
    return session.scalar(select(OutputPackageDeletion.id).where(
        OutputPackageDeletion.package_id == package_id)) is not None


def request_package_deletion(
    session_factory, *, package_id: UUID, actor_id: UUID, actor_role: str,
    expected_impact_sha256: str,
) -> DeletionRequest:
    """Authorize, record and enqueue one deletion in a single commit. Safe to repeat."""
    with session_factory.begin() as session:
        package = session.scalar(select(OutputPackage).where(OutputPackage.id == package_id).with_for_update())
        if package is None:
            raise DeletionNotFound
        policy = get_delete_policy(session, package.production_id)
        if not may_delete(policy, actor_role, actor_id, package.created_by):
            raise DeletionDenied
        job_key = deletion_job_key(package.id)
        existing = session.scalar(select(OutputPackageDeletion).where(
            OutputPackageDeletion.package_id == package.id).with_for_update())
        if existing is not None:
            job = session.scalar(select(Job).where(Job.idempotency_key == job_key).with_for_update())
            if existing.state == "deleting" and job is not None and _job_is_stuck(session, job):
                # Explicit retry of a stuck deletion; the job re-derives all state from the provider.
                job.state, job.attempts, job.last_error_code = "queued", 0, None
                job.lease_owner, job.lease_expires_at = None, None
                job.available_at = func.now()
            return DeletionRequest(package.id, existing.state, job.id if job is not None else existing.id, False)
        if package.state != "completed":
            raise DeletionConflict("package_not_completed")
        impact = build_delete_impact(session, package)
        if impact_digest(impact) != expected_impact_sha256:
            raise DeletionConflict("impact_changed")
        deletion = OutputPackageDeletion(
            package_id=package.id, production_id=package.production_id, task_id=package.task_id,
            requested_by=actor_id, state="deleting", impact_snapshot=impact,
        )
        session.add(deletion)
        job = Job(
            job_type=JOB_TYPE, idempotency_key=job_key, state="queued",
            payload={"job_type": JOB_TYPE, "package_id": str(package.id), "task_id": str(package.task_id),
                     "production_id": str(package.production_id), "actor_id": str(actor_id)},
        )
        session.add(job)
        session.flush()
        return DeletionRequest(package.id, deletion.state, job.id, True)


def _listing(adapter, parent_id):
    items, token = [], None
    for _ in range(_MAX_LISTING_PAGES):
        page = adapter.list_children(parent_id, token)
        if not page.complete:
            raise AmbiguousOutcome("Provider listing is incomplete")
        items.extend(page.items)
        token = page.next_page_token
        if not token:
            return items
    raise AmbiguousOutcome("Provider listing did not terminate")


_FOLDER_MIME_TYPES = ("folder", "application/vnd.google-apps.folder")  # FakeStorage / Google Drive


def _is_folder(obj) -> bool:
    return obj.mime_type in _FOLDER_MIME_TYPES


def _present(adapter, object_id, parent_id):
    """Return the object, or None only when its verified parent's complete listing proves it is gone.

    A provider "not found" can also mean "not visible to this credential", so on its own it is never
    treated as deleted; a lookup that disagrees with the listing fails closed.
    """
    try:
        found = adapter.get(object_id)
    except ObjectNotFound:
        found = None
    if found is not None:
        return found
    if any(item.object_id == object_id for item in _listing(adapter, parent_id)):
        raise AmbiguousOutcome("Provider lookup and parent listing disagree")
    return None


def _target_matches_path(adapter, obj, folder_id, file_name):
    from .personal_import import relative_parts
    from ..integrations.personal_google import PersonalSourceDenied
    try:
        parts = relative_parts(file_name)
    except PersonalSourceDenied:
        return False
    if obj.name != parts[-1] or _is_folder(obj):
        return False
    parent_id = obj.parent_id
    for name in reversed(parts[:-1]):
        parent = adapter.get(parent_id)
        if parent is None or parent.trashed or not _is_folder(parent) or parent.name != name:
            return False
        parent_id = parent.parent_id
    return parent_id == folder_id


def _verified_target_parent(adapter, root_id, file_name, directories):
    """Resolve only directory IDs persisted by this package's creation job."""
    from .personal_import import relative_parts
    parent_id, prefix = root_id, []
    for name in relative_parts(file_name)[:-1]:
        prefix.append(name)
        directory_id = directories.get("/".join(prefix))
        if not directory_id:
            raise AmbiguousOutcome("Nested target parent was not persisted")
        directory = adapter.get(directory_id)
        if (directory is None or directory.trashed or not _is_folder(directory)
                or directory.object_id != directory_id or directory.parent_id != parent_id or directory.name != name):
            raise AmbiguousOutcome("Nested target parent ownership is unverified")
        adapter.assert_parent_within_root(directory_id)
        parent_id = directory_id
    return parent_id


def make_output_package_deletion_handler(session_factory, adapter_factory):
    def delete_package(job):
        try:
            package_id = UUID(str(job.payload["package_id"]))
        except (KeyError, ValueError, TypeError):
            return JobResult("failed", error_code="invalid_output_delete_payload")

        with session_factory.begin() as session:
            lease = session.scalar(select(Job).where(Job.id == job.job_id).with_for_update())
            if not _lease_is_current(session, job, lease):
                return JobResult("retry", error_code="lease_lost_before_output_delete")
            deletion = session.scalar(select(OutputPackageDeletion).where(
                OutputPackageDeletion.package_id == package_id))
            package = session.get(OutputPackage, package_id)
            if deletion is None or package is None or deletion.production_id != package.production_id \
                    or deletion.task_id != package.task_id:
                return JobResult("failed", error_code="output_delete_record_missing")
            if deletion.state == "deleted":
                return JobResult("completed")
            from ..integrations.storage_authority import selected_storage_connection

            connection = selected_storage_connection(session, package.production_id)
            if connection is None or not connection.allowed_root_id:
                return JobResult("blocked_authorization", error_code="output_delete_storage_unavailable")
            personal_source = package.source_kind == "personal"
            creation = session.scalar(select(Job).where(
                Job.job_type == "verify_output_package",
                Job.payload["package_id"].astext == str(package.id))) if personal_source else None
            directories = dict(creation.payload.get("output_directories", {})) if creation else {}
            allowed_root_id = connection.allowed_root_id
            folder_id = package.provider_folder_id
            folder_name = f"output-package-{package.id.hex}"
            rows = session.scalars(select(OutputPackageFile).where(
                OutputPackageFile.package_id == package.id).order_by(OutputPackageFile.ordinal)).all()
            own_sources = {row.source_provider_object_id for row in rows if row.source_provider_object_id}
            targets = [(row.ordinal, row.provider_object_id, row.file_name) for row in rows if row.provider_object_id]
            candidate_ids = {target_id for _o, target_id, _n in targets} | ({folder_id} if folder_id else set())
            # Anything another version, another Task's share or a reference points at is not ours to remove.
            protected = set()
            if candidate_ids:
                protected |= set(session.scalars(select(OutputPackageFile.provider_object_id).where(
                    OutputPackageFile.package_id != package.id,
                    OutputPackageFile.provider_object_id.in_(candidate_ids))))
                protected |= set(session.scalars(select(OutputPackageFile.source_provider_object_id).where(
                    OutputPackageFile.source_provider_object_id.in_(candidate_ids))))
                protected |= set(session.scalars(select(OutputPackage.provider_folder_id).where(
                    OutputPackage.id != package.id, OutputPackage.provider_folder_id.in_(candidate_ids))))
                protected |= set(session.scalars(select(LinkShare.object_id).where(LinkShare.object_id.in_(candidate_ids))))
            protected |= own_sources | {allowed_root_id}

        if not folder_id:
            return JobResult("failed", error_code="output_delete_folder_missing")
        adapter = resolve_storage_adapter(adapter_factory, connection)
        capabilities = adapter.capabilities(allowed_root_id)
        if not capabilities.root_visible:
            raise PermissionFailure("Output storage root is unavailable")
        adapter.assert_parent_within_root(allowed_root_id)

        result = {"files_deleted": 0, "files_already_absent": 0, "files_retained_shared": 0,
                  "folder": "absent_confirmed"}
        folder = _present(adapter, folder_id, allowed_root_id)
        if folder is None:
            result["files_already_absent"] = sum(1 for _o, target_id, _n in targets if target_id not in protected)
        else:
            if folder_id in protected or folder.object_id != folder_id or not _is_folder(folder) \
                    or folder.parent_id != allowed_root_id or folder.name != folder_name:
                return JobResult("failed", error_code="output_delete_folder_ownership_mismatch")
            adapter.assert_parent_within_root(folder.object_id)

            # Validate every target before the first destructive call.
            to_delete = []
            for _ordinal, target_id, file_name in targets:
                if target_id in protected:
                    result["files_retained_shared"] += 1
                    continue
                parent_id = (_verified_target_parent(adapter, folder_id, file_name, directories)
                             if personal_source else folder_id)
                obj = _present(adapter, target_id, parent_id)
                if obj is None:
                    result["files_already_absent"] += 1
                    continue
                matches = (_target_matches_path(adapter, obj, folder_id, file_name) if personal_source
                           else obj.parent_id == folder_id and not _is_folder(obj) and obj.name == file_name)
                if obj.object_id != target_id or not matches:
                    return JobResult("failed", error_code="output_delete_target_ownership_mismatch")
                to_delete.append((target_id, parent_id))

            for target_id, parent_id in to_delete:
                adapter.delete(target_id)
                if _present(adapter, target_id, parent_id) is not None:
                    raise AmbiguousOutcome("Provider delete was not confirmed")
                result["files_deleted"] += 1

            # Drive folder deletion cascades to every descendant and has no "only if empty" form, so a
            # file added between a check and the delete would be destroyed. The version folder is therefore
            # never deleted; it stays as an empty (or foreign-content) container.
            result["folder"] = "retained_not_empty" if _listing(adapter, folder_id) else "retained_empty"

        with session_factory.begin() as session:
            lease = session.scalar(select(Job).where(Job.id == job.job_id).with_for_update())
            if not _lease_is_current(session, job, lease):
                return JobResult("retry", error_code="lease_lost_after_output_delete")
            deletion = session.scalar(select(OutputPackageDeletion).where(
                OutputPackageDeletion.package_id == package_id).with_for_update())
            if deletion is None:
                return JobResult("failed", error_code="output_delete_record_missing")
            if deletion.state != "deleted":
                deletion.state = "deleted"
                deletion.result = result
                deletion.completed_at = func.now()
        return JobResult("completed")

    return delete_package
