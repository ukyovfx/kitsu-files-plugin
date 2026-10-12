"""PostgreSQL-backed job leases using row locks, DB time and generation fencing."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import func, or_, select, update


@dataclass(frozen=True)
class LeasedJob:
    job_id: UUID
    job_type: str
    payload: dict[str, Any]
    worker_id: UUID
    generation: int
    attempts: int


@dataclass(frozen=True)
class JobResult:
    state: str
    external_mutation: bool = False
    provider_object_id: str | None = None
    byte_count: int | None = None
    sha256: str | None = None
    error_code: str | None = None
    retry_after_seconds: int | None = None


class PostgresJobQueue:
    def __init__(self, session_factory: Callable, lease_seconds: int = 120):
        if lease_seconds < 15 or lease_seconds > 3600:
            raise ValueError("Lease duration outside safe bounds")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds

    def claim_job(self, worker_id: UUID, now_from_db: bool = True) -> LeasedJob | None:
        from ..models import Job

        with self.session_factory.begin() as session:
            now = session.scalar(select(func.now())) if now_from_db else func.now()
            eligible = or_(
                Job.state == "queued",
                (Job.state == "leased") & (Job.lease_expires_at < func.now()),
            )
            job = session.scalar(
                select(Job)
                .where(eligible, Job.available_at <= func.now(), Job.attempts < Job.max_attempts)
                .order_by(Job.available_at, Job.created_at, Job.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.state = "leased"
            job.lease_owner = worker_id
            job.lease_generation += 1
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            job.attempts += 1
            session.flush()
            return LeasedJob(job.id, job.job_type, dict(job.payload), worker_id, job.lease_generation, job.attempts)

    def heartbeat(self, job_id: UUID, worker_id: UUID, generation: int) -> bool:
        from ..models import Job

        with self.session_factory.begin() as session:
            result = session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.state == "leased",
                    Job.lease_owner == worker_id,
                    Job.lease_generation == generation,
                    Job.lease_expires_at > func.now(),
                )
                .values(lease_expires_at=func.now() + timedelta(seconds=self.lease_seconds))
            )
            return result.rowcount == 1

    def complete_job(self, job_id: UUID, worker_id: UUID, generation: int, result: JobResult) -> bool:
        from ..models import Job, UploadSession, WorkingRevision, WorkingRevisionFile

        if result.external_mutation and not (result.provider_object_id and result.byte_count is not None and result.sha256):
            raise ValueError("External mutation completion requires verified provider ID, byte count and SHA-256")
        if result.state not in ("completed", "retry", "reconcile_required", "blocked_authorization", "failed"):
            raise ValueError("Unsupported terminal job state")
        if result.retry_after_seconds is not None and (
            isinstance(result.retry_after_seconds, bool)
            or not 1 <= result.retry_after_seconds <= 3600
        ):
            raise ValueError("Retry delay outside safe bounds")
        with self.session_factory.begin() as session:
            update_values = {
                "state": result.state,
                "lease_owner": None,
                "lease_expires_at": None,
                "last_error_code": result.error_code,
            }
            if result.state == "retry":
                update_values["available_at"] = func.now() + timedelta(seconds=result.retry_after_seconds or 30)
            update_stmt = update(Job).where(
                Job.id == job_id,
                Job.state == "leased",
                Job.lease_owner == worker_id,
                Job.lease_generation == generation,
                Job.lease_expires_at > func.now(),
            ).values(**update_values)
            if session.execute(update_stmt).rowcount != 1:
                return False
            job = session.get(Job, job_id)
            upload_id = job.payload.get("upload_session_id") if job.job_type == "publish_source" else None
            if upload_id:
                upload = session.scalar(
                    select(UploadSession).where(UploadSession.id == upload_id).with_for_update()
                )
                if upload is not None:
                    working_revision = None
                    if upload.working_revision_file_id is not None:
                        revision_id = session.scalar(
                            select(WorkingRevisionFile.revision_id)
                            .where(WorkingRevisionFile.id == upload.working_revision_file_id)
                        )
                        working_revision = session.scalar(
                            select(WorkingRevision).where(WorkingRevision.id == revision_id).with_for_update()
                        ) if revision_id is not None else None
                    if result.state == "completed":
                        if not (result.external_mutation and result.provider_object_id and result.byte_count is not None and result.sha256):
                            raise ValueError("Completed upload requires verified provider evidence")
                        if (
                            result.provider_object_id != upload.provider_object_id
                            or result.byte_count != upload.expected_size
                            or result.sha256 != upload.expected_sha256
                        ):
                            raise ValueError("Completed upload evidence does not match its reserved identity and source")
                        upload.state = "completed"
                        upload.provider_object_id = result.provider_object_id
                        upload.provider_size = result.byte_count
                        upload.provider_sha256 = result.sha256
                        upload.transferred_at = func.now()
                        upload.failure_code = None
                        if upload.working_revision_file_id is not None:
                            working_file = session.scalar(
                                select(WorkingRevisionFile)
                                .where(WorkingRevisionFile.id == upload.working_revision_file_id)
                                .with_for_update()
                            )
                            if working_file is None:
                                raise ValueError("Working upload has no reserved revision file")
                            if working_file.provider_object_id not in (None, result.provider_object_id):
                                raise ValueError("Working file provider identity cannot be replaced")
                            working_file.provider_object_id = result.provider_object_id
                            working_file.provider_size = result.byte_count
                            working_file.provider_sha256 = result.sha256
                            working_file.state = "completed"
                            if working_revision is None:
                                raise ValueError("Working revision reservation is unavailable")
                            from ..working_files.service import complete_working_revision_in_session

                            complete_working_revision_in_session(session, working_revision)
                    elif result.state == "reconcile_required":
                        upload.state = "reconcile_required"
                        upload.failure_code = result.error_code
                        if upload.working_revision_file_id is not None:
                            working_file = session.get(WorkingRevisionFile, upload.working_revision_file_id)
                            if working_file is not None and working_file.state != "completed":
                                working_file.state = "reconcile_required"
                                if working_revision is not None and working_revision.state not in ("completed", "stale_base"):
                                    working_revision.state = "reconcile_required"
                    elif result.state == "failed":
                        upload.state = "failed"
                        upload.failure_code = result.error_code
                        if upload.working_revision_file_id is not None:
                            working_file = session.get(WorkingRevisionFile, upload.working_revision_file_id)
                            if working_file is not None and working_file.state != "completed":
                                working_file.state = "failed"
                                if working_revision is not None and working_revision.state not in ("completed", "stale_base"):
                                    working_revision.state = "failed"
            return True

    def fail_exhausted(self) -> int:
        from ..models import Job

        with self.session_factory.begin() as session:
            result = session.execute(
                update(Job)
                .where(Job.state.in_(("queued", "leased")), Job.attempts >= Job.max_attempts, or_(Job.state == "queued", Job.lease_expires_at < func.now()))
                .values(state="failed", lease_owner=None, lease_expires_at=None, last_error_code="retry_limit")
            )
            return result.rowcount
