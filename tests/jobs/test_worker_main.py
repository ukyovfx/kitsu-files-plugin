from uuid import uuid4

import pytest

pytest.importorskip("vfx_files")

from plugin.jobs.queue import JobResult, LeasedJob
from plugin.compat import SCHEMA_EPOCH, WORKER_BUILD
from plugin.storage.base import AmbiguousOutcome
from worker.leases import WorkerContext
from worker.main import WorkerRuntime, process_one


class Queue:
    def __init__(self, *, complete=True):
        self.complete_result = complete
        self.job = LeasedJob(uuid4(), "source_upload", {"task_id": "synthetic"}, uuid4(), 1, 1)
        self.completed = None

    def claim_job(self, _worker_id):
        job = self.job
        self.job = None
        return job

    def complete_job(self, job_id, worker_id, generation, result):
        self.completed = (job_id, worker_id, generation, result)
        return self.complete_result


def runtime(queue, handler=None, *, allowed=True):
    context = WorkerContext(uuid4(), WORKER_BUILD, "1.0.94", SCHEMA_EPOCH)
    return WorkerRuntime(queue, context, lambda _payload: allowed, {"source_upload": handler} if handler else {})


def test_unknown_job_type_fails_closed():
    queue = Queue()
    assert process_one(runtime(queue)) == "failed"
    assert queue.completed[3].error_code == "unsupported_job_type"


def test_ambiguous_external_operation_requires_reconciliation():
    queue = Queue()

    def ambiguous(_job):
        raise AmbiguousOutcome("provider details stay out of logs")

    assert process_one(runtime(queue, ambiguous)) == "reconcile_required"
    assert queue.completed[3].error_code == "external_outcome_ambiguous"


def test_revoked_authorization_blocks_handler_before_side_effect():
    queue = Queue()
    calls = []
    assert process_one(runtime(queue, lambda _job: calls.append(True), allowed=False)) == "blocked_authorization"
    assert calls == []


def test_stale_lease_result_is_not_reported_as_complete():
    queue = Queue(complete=False)
    assert process_one(runtime(queue, lambda _job: JobResult("completed"))) == "stale_lease"
