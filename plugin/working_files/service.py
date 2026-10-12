"""Task-scoped immutable Working revisions and snapshot pointers."""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from ..models import (
    Job,
    LatestWorkingPointer,
    StorageConnection,
    UploadSession,
    WorkingDependency,
    WorkingRevision,
    WorkingRevisionFile,
)


_HASH = re.compile(r"^[0-9a-f]{64}$")
_FILE_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")
_EXCLUDED_CATEGORIES = {"cache", "simulation", "intermediate_render"}


class WorkingConflict(Exception):
    pass


class WorkingDenied(Exception):
    pass


def validate_file_type(value: object) -> str:
    """Validate a human-entered type label before storing it in the legacy category field."""
    if not isinstance(value, str) or not _FILE_TYPE.fullmatch(value):
        raise ValueError("Working file type must be 1 to 32 letters, digits, spaces, underscores or hyphens")
    return value


def requires_intermediate_opt_in(value: str) -> bool:
    normalized = value.casefold().replace(" ", "_").replace("-", "_")
    return normalized in _EXCLUDED_CATEGORIES


@dataclass(frozen=True)
class WorkingFileReceipt:
    id: UUID
    upload_session_id: UUID
    file_name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class WorkingRevisionReceipt:
    id: UUID
    task_id: UUID
    version: int
    state: str
    files: tuple[WorkingFileReceipt, ...]

    @property
    def version_label(self) -> str:
        return f"w{self.version:03d}"


def reserve_working_revision(
    session_factory,
    *,
    production_id: UUID,
    task_id: UUID,
    storage_connection_id: UUID,
    actor_id: UUID,
    base_revision_id: UUID | None,
    idempotency_key: str,
    files: list[dict],
    dependencies: list[dict],
    include_intermediate: bool = False,
    max_chunk_bytes: int = 8 * 1024 * 1024,
    ttl_hours: int = 168,
) -> WorkingRevisionReceipt:
    """Atomically reserve a revision, its file slots, resumable sessions and quota."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("Working revision idempotency key is invalid")
    if not files or len(files) > 128:
        raise ValueError("A Working revision must contain between 1 and 128 files")
    if len({str(item.get("name", "")).casefold() for item in files}) != len(files):
        raise ValueError("A Working snapshot cannot contain duplicate file names")
    if not 1 <= max_chunk_bytes <= 64 * 1024 * 1024 or not 1 <= ttl_hours <= 168:
        raise ValueError("Working upload limits are outside safe bounds")

    normalized = []
    for item in files:
        name = item.get("name")
        size = item.get("size")
        digest = item.get("sha256")
        category = item.get("category", "working")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 512
            or any(ord(char) < 32 for char in name)
            or "/" in name
            or "\\" in name
        ):
            raise ValueError("Working file name is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError("Working file size or SHA-256 is invalid")
        category = validate_file_type(category)
        if requires_intermediate_opt_in(category) and not include_intermediate:
            raise WorkingDenied("Cache, simulation and intermediate renders require explicit opt-in")
        mime_type = item.get("mime_type")
        if mime_type is not None and (not isinstance(mime_type, str) or len(mime_type) > 255):
            raise ValueError("Working file media type is invalid")
        normalized.append((name, size, digest, category, mime_type))

    normalized_dependencies = []
    for dependency in dependencies:
        dependency_type = dependency.get("type")
        if dependency_type not in ("working", "publish"):
            raise ValueError("Working dependency type is invalid")
        dependency_id = UUID(str(dependency.get("id")))
        digest = dependency.get("manifest_sha256")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError("Working dependency manifest SHA-256 is invalid")
        normalized_dependencies.append((dependency_type, dependency_id, digest))

    new_revision_id = uuid4()
    with session_factory.begin() as session:
        # Serialize Task version allocation even when no prior revision row exists.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"), {"scope": f"vfx-files-working:{task_id}"})
        existing = session.scalar(select(WorkingRevision).where(WorkingRevision.idempotency_key == idempotency_key))
        if existing is not None:
            return _receipt(session, existing)

        connection = session.scalar(select(StorageConnection).where(StorageConnection.id == storage_connection_id).with_for_update())
        if connection is None or not connection.enabled or connection.production_id != production_id:
            raise WorkingDenied("Production storage connection is unavailable")
        if any(item[1] > connection.max_file_bytes for item in normalized):
            raise WorkingDenied("Working snapshot exceeds file limits")
        total_size = sum(item[1] for item in normalized)
        if connection.used_bytes + total_size > connection.quota_bytes:
            raise WorkingDenied("Working snapshot exceeds Production storage quota")

        pointer = session.get(LatestWorkingPointer, task_id)
        current_base = pointer.revision_id if pointer is not None else None
        if current_base != base_revision_id:
            raise WorkingConflict("Working revision has a stale base")
        version = session.scalar(select(func.coalesce(func.max(WorkingRevision.version), 0)).where(WorkingRevision.task_id == task_id)) + 1
        revision = WorkingRevision(
            id=new_revision_id,
            production_id=production_id,
            task_id=task_id,
            version=version,
            base_revision_id=base_revision_id,
            created_by=actor_id,
            state="receiving",
            idempotency_key=idempotency_key,
        )
        session.add(revision)
        session.flush()
        expires_at = session.scalar(select(func.now())) + timedelta(hours=ttl_hours)
        for ordinal, (name, size, digest, category, mime_type) in enumerate(normalized):
            file_row = WorkingRevisionFile(
                id=uuid4(),
                revision_id=revision.id,
                ordinal=ordinal,
                file_name=name,
                mime_type=mime_type,
                category=category,
                expected_size=size,
                expected_sha256=digest,
                state="receiving",
            )
            session.add(file_row)
            session.flush()
            upload_id = uuid4()
            session.add(UploadSession(
                id=upload_id,
                task_id=task_id,
                actor_id=actor_id,
                storage_connection_id=storage_connection_id,
                working_revision_file_id=file_row.id,
                state="receiving",
                file_name=name,
                expected_size=size,
                expected_sha256=digest,
                received_size=0,
                staging_key=f"upload/{upload_id}",
                expires_at=expires_at,
            ))
        for dependency_type, dependency_id, digest in normalized_dependencies:
            session.add(WorkingDependency(
                id=uuid4(),
                revision_id=revision.id,
                dependency_type=dependency_type,
                dependency_id=dependency_id,
                manifest_sha256=digest,
            ))
        connection.used_bytes += total_size
        session.flush()
        revision = session.get(WorkingRevision, revision.id)
        return _receipt(session, revision)


def record_verified_file(session_factory, *, revision_file_id: UUID, provider_object_id: str, byte_count: int, sha256: str) -> None:
    if not provider_object_id or byte_count < 0 or not isinstance(sha256, str) or not _HASH.fullmatch(sha256):
        raise ValueError("Verified provider evidence is invalid")
    with session_factory.begin() as session:
        file_row = session.scalar(select(WorkingRevisionFile).where(WorkingRevisionFile.id == revision_file_id).with_for_update())
        if file_row is None:
            raise WorkingConflict("Working revision file is unavailable")
        revision = session.scalar(select(WorkingRevision).where(WorkingRevision.id == file_row.revision_id).with_for_update())
        if revision is None or revision.state not in ("receiving", "transferring"):
            raise WorkingConflict("Completed or closed Working revisions are immutable")
        if byte_count != file_row.expected_size or sha256 != file_row.expected_sha256:
            raise WorkingConflict("Provider evidence does not match the declared file")
        if file_row.provider_object_id not in (None, provider_object_id):
            raise WorkingConflict("Working file provider identity cannot be replaced")
        if file_row.state == "completed" and (file_row.provider_size, file_row.provider_sha256) != (byte_count, sha256):
            raise WorkingConflict("Completed Working file evidence is immutable")
        file_row.provider_object_id = provider_object_id
        file_row.provider_size = byte_count
        file_row.provider_sha256 = sha256
        file_row.state = "completed"
        revision.state = "transferring"


def complete_working_revision(session_factory, *, revision_id: UUID) -> WorkingRevisionReceipt:
    stale = False
    with session_factory.begin() as session:
        revision = session.scalar(select(WorkingRevision).where(WorkingRevision.id == revision_id).with_for_update())
        if revision is None:
            raise WorkingConflict("Working revision is unavailable")
        if revision.state == "completed":
            return _receipt(session, revision)
        state = complete_working_revision_in_session(session, revision)
        if state == revision.state and state not in ("completed", "stale_base"):
            raise WorkingConflict("Working revision does not have all files transferred and verified")
        stale = state == "stale_base"
        session.flush()
        receipt = _receipt(session, revision)
    if stale:
        raise WorkingConflict("Working revision has a stale base")
    return receipt


def complete_working_revision_in_session(session, revision) -> str:
    """Advance Latest Working atomically after every file has verified."""
    if revision.state in ("completed", "stale_base"):
        return revision.state
    files = session.scalars(
        select(WorkingRevisionFile)
        .where(WorkingRevisionFile.revision_id == revision.id)
        .order_by(WorkingRevisionFile.ordinal)
        .with_for_update()
    ).all()
    if not files or any(
        file_row.state != "completed"
        or not file_row.provider_object_id
        or file_row.provider_size != file_row.expected_size
        or file_row.provider_sha256 != file_row.expected_sha256
        for file_row in files
    ):
        return revision.state
    pointer = session.scalar(
        select(LatestWorkingPointer)
        .where(LatestWorkingPointer.task_id == revision.task_id)
        .with_for_update()
    )
    current_base = pointer.revision_id if pointer is not None else None
    if current_base != revision.base_revision_id:
        revision.state = "stale_base"
        return revision.state
    dependencies = session.scalars(
        select(WorkingDependency)
        .where(WorkingDependency.revision_id == revision.id)
        .order_by(WorkingDependency.dependency_type, WorkingDependency.dependency_id)
    ).all()
    document = {
        "production_id": str(revision.production_id),
        "task_id": str(revision.task_id),
        "revision_id": str(revision.id),
        "version": f"w{revision.version:03d}",
        "created_by": str(revision.created_by),
        "files": [{
            "id": str(file_row.id),
            "name": file_row.file_name,
            "mime_type": file_row.mime_type,
            "category": file_row.category,
            "provider_object_id": file_row.provider_object_id,
            "byte_count": file_row.provider_size,
            "sha256": file_row.provider_sha256,
        } for file_row in files],
        "dependencies": [{
            "type": row.dependency_type,
            "id": str(row.dependency_id),
            "manifest_sha256": row.manifest_sha256,
        } for row in dependencies],
    }
    manifest_bytes = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    revision.manifest_bytes = manifest_bytes
    revision.manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    revision.state = "completed"
    revision.completed_at = session.scalar(select(func.now()))
    if pointer is None:
        session.add(LatestWorkingPointer(task_id=revision.task_id, revision_id=revision.id))
    else:
        pointer.revision_id = revision.id
    return revision.state


def _receipt(session, revision) -> WorkingRevisionReceipt:
    files = session.scalars(select(WorkingRevisionFile).where(WorkingRevisionFile.revision_id == revision.id).order_by(WorkingRevisionFile.ordinal)).all()
    sessions = {
        row.working_revision_file_id: row
        for row in session.scalars(select(UploadSession).where(UploadSession.working_revision_file_id.in_([item.id for item in files]))).all()
    } if files else {}
    return WorkingRevisionReceipt(
        revision.id,
        revision.task_id,
        revision.version,
        revision.state,
        tuple(WorkingFileReceipt(item.id, sessions[item.id].id, item.file_name, item.expected_size, item.expected_sha256) for item in files if item.id in sessions),
    )
