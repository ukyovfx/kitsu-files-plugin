"""Synthetic fault-injection tests for worker failure classification and duplicate leases.

Covers the untested high-risk worker paths in `plugin.worker.main` / `plugin.worker.leases`:
 * provider permission denial and unexpected handler crashes -> stable terminal/retry codes
 * a handler returning a non-`JobResult` fails closed
 * a transient heartbeat failure must not lose a still-valid lease
 * a queue that advertises no renewable lease runs synchronously without starting a poller
 * duplicate concurrent execution of one leased generation completes exactly once

All jobs, payloads and queues here are synthetic; no real Drive, Zou host or Discord is used.
"""

import threading
import time
from uuid import uuid4

import pytest

from plugin.compat import SCHEMA_EPOCH, WORKER_BUILD
from plugin.jobs.queue import JobResult, LeasedJob
from plugin.storage.base import PermissionFailure
from plugin.worker.leases import WorkerContext
from plugin.worker.main import WorkerRuntime, process_one


def synthetic_job():
    return LeasedJob(uuid4(), "source_upload", {"task_id": "synthetic"}, uuid4(), 1, 1)


def runtime(queue, handler, *, worker_id=None):
    return WorkerRuntime(
        queue,
        WorkerContext(worker_id or uuid4(), WORKER_BUILD, "1.0.94", SCHEMA_EPOCH),
        lambda _payload: True,
        {"source_upload": handler},
    )


class StaticQueue:
    """One claimable job, then idle. Renews no lease."""

    def __init__(self):
        self.job = synthetic_job()
        self.completed = None
        self._lock = threading.Lock()
        self._claimed = False

    def claim_job(self, _worker_id):
        with self._lock:
            if self._claimed:
                return None
            self._claimed = True
            return self.job

    def complete_job(self, job_id, worker_id, generation, result):
        with self._lock:
            self.completed = (job_id, worker_id, generation, result)
        return True


def test_provider_permission_denial_is_terminal_and_not_retried():
    def handler(_job):
        raise PermissionFailure("Drive credential is unavailable")

    queue = StaticQueue()

    assert process_one(runtime(queue, handler)) == "failed"
    assert queue.completed[3].state == "failed"
    assert queue.completed[3].error_code == "provider_permission_denied"


def test_unexpected_handler_crash_is_retried_without_leaking_the_exception():
    def handler(_job):
        raise RuntimeError("synthetic-secret-token-must-not-surface")

    queue = StaticQueue()

    assert process_one(runtime(queue, handler)) == "retry"
    result = queue.completed[3]
    assert result.state == "retry"
    assert result.error_code == "worker_operation_failed"
    assert "synthetic-secret-token-must-not-surface" not in repr(result)


def test_handler_returning_a_non_result_fails_closed():
    def handler(_job):
        return {"state": "completed"}

    queue = StaticQueue()

    assert process_one(runtime(queue, handler)) == "failed"
    assert queue.completed[3].error_code == "invalid_handler_result"


class FlakyHeartbeatQueue(StaticQueue):
    lease_seconds = 0.03

    def __init__(self):
        super().__init__()
        self.heartbeat_calls = 0

    def heartbeat(self, _job_id, _worker_id, _generation):
        self.heartbeat_calls += 1
        if self.heartbeat_calls == 1:
            raise RuntimeError("synthetic transient database failure")
        return True


def test_transient_heartbeat_failure_does_not_lose_a_valid_lease():
    queue = FlakyHeartbeatQueue()
    renewed = threading.Event()

    def handler(_job):
        deadline = time.monotonic() + 5
        while queue.heartbeat_calls < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        renewed.set()
        return JobResult("completed")

    assert process_one(runtime(queue, handler)) == "completed"

    assert renewed.is_set()
    assert queue.heartbeat_calls >= 2
    assert queue.completed is not None
    assert queue.completed[3].state == "completed"


class LeaselessQueue(StaticQueue):
    """A durable queue that never advertises a renewable lease."""

    def __init__(self, *, lease_seconds="absent"):
        super().__init__()
        self.heartbeat_calls = 0
        if lease_seconds != "absent":
            self.lease_seconds = lease_seconds

    def heartbeat(self, *_args):
        self.heartbeat_calls += 1
        raise AssertionError("a queue without a usable lease must not be heartbeat-polled")


@pytest.mark.parametrize("lease_seconds", ["absent", 0, "not-a-number"])
def test_queue_without_a_usable_lease_runs_synchronously(lease_seconds):
    queue = LeaselessQueue(lease_seconds=lease_seconds)

    assert process_one(runtime(queue, lambda _job: JobResult("completed"))) == "completed"

    assert queue.heartbeat_calls == 0
    assert queue.completed is not None
    assert queue.completed[3].state == "completed"


class ContendedQueue:
    """Two workers claim the same generation; only the first completion is accepted."""

    lease_seconds = 0.03

    def __init__(self):
        self.job = synthetic_job()
        self.attempts = []
        self.accepted = []
        self._lock = threading.Lock()
        self._claimed = 0

    def claim_job(self, _worker_id):
        with self._lock:
            self._claimed += 1
            return self.job if self._claimed <= 2 else None

    def complete_job(self, job_id, worker_id, generation, result):
        with self._lock:
            self.attempts.append((worker_id, result.state))
            if len(self.attempts) > 1:
                return False
            self.accepted.append((job_id, worker_id, generation, result))
            return True

    def heartbeat(self, _job_id, _worker_id, _generation):
        return True


def test_duplicate_concurrent_execution_completes_the_generation_exactly_once():
    queue = ContendedQueue()
    barrier = threading.Barrier(2, timeout=5)
    outcomes = []
    outcomes_lock = threading.Lock()

    def handler(_job):
        barrier.wait()
        return JobResult("completed")

    def run(worker_id):
        state = process_one(runtime(queue, handler, worker_id=worker_id))
        with outcomes_lock:
            outcomes.append(state)

    threads = [threading.Thread(target=run, args=(uuid4(),)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == ["completed", "stale_lease"]
    assert len(queue.attempts) == 2
    assert len(queue.accepted) == 1
    accepted_worker = queue.accepted[0][1]
    assert accepted_worker in {attempt[0] for attempt in queue.attempts}
