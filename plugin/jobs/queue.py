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
        from ..models import Job

        if result.external_mutation and not (result.provider_object_id and result.byte_count is not None and result.sha256):
            raise ValueError("External mutation completion requires verified provider ID, byte count and SHA-256")
        if result.state not in ("completed", "retry", "reconcile_required", "blocked_authorization", "failed"):
            raise ValueError("Unsupported terminal job state")
        with self.session_factory.begin() as session:
            update_values = {
                "state": result.state,
                "lease_owner": None,
                "lease_expires_at": None,
                "last_error_code": result.error_code,
            }
            if result.state == "retry":
                update_values["available_at"] = func.now() + timedelta(seconds=30)
            update_stmt = update(Job).where(
                Job.id == job_id,
                Job.state == "leased",
                Job.lease_owner == worker_id,
                Job.lease_generation == generation,
                Job.lease_expires_at > func.now(),
            ).values(**update_values)
            return session.execute(update_stmt).rowcount == 1

    def fail_exhausted(self) -> int:
        from ..models import Job

        with self.session_factory.begin() as session:
            result = session.execute(
                update(Job)
                .where(Job.state.in_(("queued", "leased")), Job.attempts >= Job.max_attempts, or_(Job.state == "queued", Job.lease_expires_at < func.now()))
                .values(state="failed", lease_owner=None, lease_expires_at=None, last_error_code="retry_limit")
            )
            return result.rowcount
