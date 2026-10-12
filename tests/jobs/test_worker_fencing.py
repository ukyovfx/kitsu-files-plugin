from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import sessionmaker

from plugin.jobs.queue import JobResult, PostgresJobQueue
from plugin.models import Job


@pytest.fixture
def job_environment(zou_db_factory):
    factory = zou_db_factory
    job_id = uuid4()
    with factory.begin() as session:
        session.add(Job(id=job_id, job_type="synthetic_test", idempotency_key=f"test:{job_id}", payload={"actor_id": str(uuid4())}, state="queued", attempts=0, max_attempts=5))
    yield factory, PostgresJobQueue(factory, lease_seconds=15), job_id
    with factory.begin() as session:
        session.execute(Job.__table__.delete().where(Job.id == job_id))


def test_claim_uses_skip_locked_generation(job_environment):
    _, queue, job_id = job_environment
    worker_ids = [uuid4(), uuid4()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(queue.claim_job, worker_ids))

    acquired = [claim for claim in claims if claim is not None]
    assert len(acquired) == 1
    assert acquired[0].job_id == job_id
    assert acquired[0].generation == 1


def test_stale_worker_cannot_complete_after_takeover(job_environment):
    factory, queue, job_id = job_environment
    first = queue.claim_job(uuid4())
    assert first is not None
    with factory.begin() as session:
        session.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=func.now() - text("interval '1 second'")))
    second = queue.claim_job(uuid4())

    assert second is not None
    assert second.generation == first.generation + 1
    assert not queue.complete_job(job_id, first.worker_id, first.generation, JobResult("completed"))
    assert queue.complete_job(job_id, second.worker_id, second.generation, JobResult("completed"))


def test_external_job_completion_requires_verified_transfer_evidence(job_environment):
    _, queue, job_id = job_environment
    lease = queue.claim_job(uuid4())
    assert lease is not None
    with pytest.raises(ValueError):
        queue.complete_job(job_id, lease.worker_id, lease.generation, JobResult("completed", external_mutation=True))
