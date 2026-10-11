from dataclasses import dataclass
from uuid import UUID

from ..jobs.queue import JobResult, LeasedJob, PostgresJobQueue


@dataclass(frozen=True)
class WorkerContext:
    worker_id: UUID
    build: str
    host_version: str
    schema_epoch: int


def run_one(queue: PostgresJobQueue, context: WorkerContext, authorize, execute):
    """Check build compatibility and permissions before any job side effect."""
    from ..authz import assert_compatible_host

    assert_compatible_host(context.host_version, context.schema_epoch, context.build)
    job = queue.claim_job(context.worker_id)
    if job is None:
        return None
    if not authorize(job.payload):
        queue.complete_job(job.job_id, context.worker_id, job.generation, JobResult("blocked_authorization", error_code="permission_revoked"))
        return "blocked_authorization"
    result = execute(job)
    if not queue.complete_job(job.job_id, context.worker_id, job.generation, result):
        return "stale_lease"
    return result.state
