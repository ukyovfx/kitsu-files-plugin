"""Fenced durable worker process loop."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import threading
from typing import Any

from ..jobs.queue import JobResult, LeasedJob, PostgresJobQueue
from ..storage.base import AmbiguousOutcome, PermissionFailure, RetryableStorageFailure
from .leases import WorkerContext, run_one


JobHandler = Callable[[LeasedJob], JobResult]


@dataclass(frozen=True)
class WorkerRuntime:
    queue: PostgresJobQueue
    context: WorkerContext
    authorize: Callable[[Mapping[str, Any]], bool]
    handlers: Mapping[str, JobHandler]


def process_one(runtime: WorkerRuntime):
    """Claim and process one job; opaque failures never leak provider data."""

    def execute(job):
        handler = runtime.handlers.get(job.job_type)
        if handler is None:
            return JobResult("failed", error_code="unsupported_job_type")
        try:
            result = handler(job)
            if not isinstance(result, JobResult):
                return JobResult("failed", error_code="invalid_handler_result")
            return result
        except AmbiguousOutcome:
            return JobResult("reconcile_required", error_code="external_outcome_ambiguous")
        except PermissionFailure:
            return JobResult("failed", error_code="provider_permission_denied")
        except RetryableStorageFailure as error:
            return JobResult(
                "retry",
                error_code="provider_temporarily_unavailable",
                retry_after_seconds=error.retry_after_seconds,
            )
        except Exception:
            return JobResult("retry", error_code="worker_operation_failed")

    return run_one(runtime.queue, runtime.context, runtime.authorize, execute)


def run_forever(runtime: WorkerRuntime, *, stop_event: threading.Event, idle_seconds: float = 2.0):
    if not 0.25 <= idle_seconds <= 30:
        raise ValueError("Worker poll interval must be between 0.25 and 30 seconds")
    while not stop_event.is_set():
        outcome = process_one(runtime)
        if outcome is None:
            stop_event.wait(idle_seconds)
