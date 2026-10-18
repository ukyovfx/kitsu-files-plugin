from uuid import uuid4

import pytest

from plugin.jobs.queue import JobResult, LeasedJob
from plugin.compat import SCHEMA_EPOCH, WORKER_BUILD
from plugin.storage.base import AmbiguousOutcome, RetryableStorageFailure
from plugin.worker.leases import WorkerContext
from plugin.worker.main import WorkerRuntime, process_one


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


def test_retryable_provider_failure_is_scheduled_without_provider_details():
    queue = Queue()

    def temporarily_unavailable(_job):
        raise RetryableStorageFailure(17, "synthetic private response")

    assert process_one(runtime(queue, temporarily_unavailable)) == "retry"
    result = queue.completed[3]
    assert result.error_code == "provider_temporarily_unavailable"
    assert result.retry_after_seconds == 17
    assert "synthetic private response" not in str(result)


def test_worker_dispatches_outbox_with_same_worker_even_when_product_queue_is_idle(monkeypatch):
    from plugin.integrations import event_delivery

    class EmptyQueue:
        def claim_job(self, _worker_id):
            return None

    context = WorkerContext(uuid4(), WORKER_BUILD, "1.0.94", SCHEMA_EPOCH)
    delivered = []
    monkeypatch.setattr(
        event_delivery, "dispatch_one",
        lambda repository, receiver, config, *, worker_id: delivered.append(
            (repository, receiver, config, worker_id)
        ) or "delivered",
    )
    runtime_with_events = WorkerRuntime(
        EmptyQueue(), context, lambda _payload: True, {},
        event_delivery=("outbox", "synthetic-receiver", "synthetic-producer-key"),
    )

    assert process_one(runtime_with_events) is None
    assert delivered == [("outbox", "synthetic-receiver", "synthetic-producer-key", context.worker_id)]


def test_outbox_failure_does_not_fail_product_job_or_log_secret(caplog, monkeypatch):
    from plugin.integrations import event_delivery

    queue = Queue()
    monkeypatch.setattr(
        event_delivery, "dispatch_one",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic-hmac-secret")),
    )
    worker = runtime(queue, lambda _job: JobResult("completed"))
    worker = WorkerRuntime(
        worker.queue, worker.context, worker.authorize, worker.handlers,
        event_delivery=("outbox", "synthetic-receiver", "synthetic-key"),
    )

    assert process_one(worker) == "completed"
    assert "synthetic-hmac-secret" not in caplog.text
    assert "synthetic-key" not in caplog.text


def test_run_forever_stops_cleanly_after_requested_shutdown(monkeypatch):
    from plugin.worker import main as worker_main

    class StopAfterWaits:
        def __init__(self):
            self.stopped = False
            self.waits = 0

        def is_set(self):
            return self.stopped

        def wait(self, _seconds):
            self.waits += 1
            if self.waits == 2:
                self.stopped = True

    stop = StopAfterWaits()
    calls = []
    monkeypatch.setattr(worker_main, "process_one", lambda _runtime: calls.append("tick") or None)

    worker_main.run_forever(runtime(Queue()), stop_event=stop, idle_seconds=0.25)

    assert len(calls) == 2
    assert stop.stopped


def test_run_forever_rejects_unsafe_poll_intervals():
    import threading

    from plugin.worker.main import run_forever

    with pytest.raises(ValueError, match="poll interval"):
        run_forever(runtime(Queue()), stop_event=threading.Event(), idle_seconds=0.1)


def test_long_worker_operation_renews_its_database_lease():
    import threading

    class HeartbeatQueue(Queue):
        lease_seconds = 0.03

        def __init__(self):
            super().__init__()
            self.heartbeat_seen = threading.Event()

        def heartbeat(self, job_id, worker_id, generation):
            self.heartbeat_seen.set()
            return True

    queue = HeartbeatQueue()

    def long_operation(_job):
        assert queue.heartbeat_seen.wait(timeout=1)
        return JobResult("completed")

    assert process_one(runtime(queue, long_operation)) == "completed"
    assert queue.heartbeat_seen.is_set()


def test_worker_suppresses_completion_after_lease_generation_is_lost():
    import threading

    class LostLeaseQueue(Queue):
        lease_seconds = 0.03

        def __init__(self):
            super().__init__()
            self.heartbeat_seen = threading.Event()

        def heartbeat(self, _job_id, _worker_id, _generation):
            self.heartbeat_seen.set()
            return False

    queue = LostLeaseQueue()

    def operation_finishes_after_takeover(_job):
        assert queue.heartbeat_seen.wait(timeout=1)
        return JobResult("completed")

    assert process_one(runtime(queue, operation_finishes_after_takeover)) == "stale_lease"
    assert queue.completed is None
