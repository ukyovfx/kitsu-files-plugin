from dataclasses import dataclass
import threading
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
    heartbeat = getattr(queue, "heartbeat", None)
    lease_seconds = getattr(queue, "lease_seconds", None)
    if not callable(heartbeat) or not isinstance(lease_seconds, (int, float)) or lease_seconds <= 0:
        result = execute(job)
    else:
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()
        heartbeat_interval = max(0.01, min(30.0, float(lease_seconds) / 3.0))

        def renew_lease():
            while not stop_heartbeat.wait(heartbeat_interval):
                try:
                    if not heartbeat(job.job_id, context.worker_id, job.generation):
                        lease_lost.set()
                        return
                except Exception:
                    # A transient DB failure is safe to retry while the lease
                    # remains valid. Final completion still fences stale work.
                    continue

        heartbeat_thread = threading.Thread(target=renew_lease, name="vfx-files-lease-heartbeat", daemon=True)
        heartbeat_thread.start()
        try:
            result = execute(job)
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=max(1.0, heartbeat_interval * 2.0))
            if heartbeat_thread.is_alive():
                lease_lost.set()
        if lease_lost.is_set():
            return "stale_lease"
    if not queue.complete_job(job.job_id, context.worker_id, job.generation, result):
        return "stale_lease"
    return result.state
