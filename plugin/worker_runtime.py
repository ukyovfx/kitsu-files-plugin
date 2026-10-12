"""Concrete Zou process entrypoint for the Files durable queue.

Google credentials are not required for the default disabled worker. The
process only enables the disk-backed fake adapter when the explicit test
storage mode and a disposable fake-storage directory are configured.
"""

import argparse
import json
import os
import re
import signal
import threading
from pathlib import Path
from uuid import UUID, uuid4


def _enabled(name):
    return os.environ.get(name, "false").lower() in ("true", "1", "yes")


def _authorize_job(payload):
    """Recheck the submitting actor's current Zou task permission before I/O."""
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import Task, TaskPersonLink

    try:
        actor_id = UUID(str(payload["actor_id"]))
        task_id = UUID(str(payload["task_id"]))
    except (KeyError, ValueError, TypeError):
        return False
    task = Task.query.filter_by(id=task_id).one_or_none()
    person = Person.query.filter_by(id=actor_id).one_or_none()
    if task is None or person is None or not getattr(person, "active", True):
        return False
    membership = ProjectPersonLink.query.filter_by(project_id=task.project_id, person_id=actor_id).one_or_none()
    role = (membership.role if membership is not None else None) or person.role
    role = getattr(role, "code", role)
    if payload.get("job_type") == "provision_task_folders" and role not in ("admin", "manager"):
        return False
    if role == "admin":
        return True
    if role == "manager":
        return membership is not None or TaskPersonLink.query.filter_by(task_id=task.id, person_id=actor_id).one_or_none() is not None
    if role in ("supervisor", "user"):
        return TaskPersonLink.query.filter_by(task_id=task.id, person_id=actor_id).one_or_none() is not None
    return False


def assert_restore_validation_complete():
    """Fail closed during restore until a trusted operator validates the snapshot."""
    guard_path = os.environ.get("VFX_FILES_RESTORE_GUARD_PATH")
    expected_manifest = os.environ.get("VFX_FILES_RESTORE_EXPECTED_MANIFEST_SHA256")
    if not guard_path and not expected_manifest:
        return
    if not guard_path or not expected_manifest or not re.fullmatch(r"[0-9a-f]{64}", expected_manifest):
        raise RuntimeError("Restore integrity verification is required before worker start")
    try:
        marker = json.loads(Path(guard_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("Restore integrity verification is required before worker start") from None
    if marker.get("integrity_verified") is not True or marker.get("manifest_sha256") != expected_manifest:
        raise RuntimeError("Restore integrity verification is required before worker start")


def build_runtime(app):
    if not _enabled("VFX_FILES_WORKER_ENABLED"):
        raise RuntimeError("VFX Files worker is disabled")
    staging_root = os.environ.get("VFX_FILES_STAGING_ROOT")
    storage_mode = os.environ.get("VFX_FILES_STORAGE_MODE")
    if not staging_root:
        raise RuntimeError("Worker staging root is required")

    from sqlalchemy.orm import sessionmaker
    from zou import __version__ as host_version
    from zou.app import db

    from .compat import SCHEMA_EPOCH, WORKER_BUILD
    from .jobs.queue import PostgresJobQueue
    from .storage.worker_factory import make_worker_storage_adapter_factory
    from .folders.worker import make_task_folder_handler
    from .uploads.staging import FileSystemStaging
    from .uploads.worker import make_upload_handler
    from .publishes.worker import make_publish_handler
    from .worker.leases import WorkerContext
    from .worker.main import WorkerRuntime

    sessions = sessionmaker(bind=db.engine, expire_on_commit=False)
    queue = PostgresJobQueue(sessions)
    adapter_factory = make_worker_storage_adapter_factory(
        storage_mode,
        fake_storage_path=os.environ.get("VFX_FILES_FAKE_STORAGE_ROOT"),
        fake_root_id=os.environ.get("VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id"),
        fake_drive_id=os.environ.get("VFX_FILES_FAKE_DRIVE_ID"),
        credentials_dir=os.environ.get("VFX_FILES_DRIVE_CREDENTIALS_DIR"),
    )
    staging = FileSystemStaging(staging_root)
    worker_id = UUID(os.environ["VFX_FILES_WORKER_ID"]) if os.environ.get("VFX_FILES_WORKER_ID") else uuid4()
    return WorkerRuntime(
        queue=queue,
        context=WorkerContext(worker_id, WORKER_BUILD, host_version, SCHEMA_EPOCH),
        authorize=_authorize_job,
        handlers={
            "publish_source": make_upload_handler(sessions, staging, adapter_factory),
            "publish_artifact": make_publish_handler(sessions, adapter_factory),
            "provision_task_folders": make_task_folder_handler(sessions, adapter_factory),
        },
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a VFX Files worker process")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true", help="Claim at most one durable job")
    modes.add_argument("--poll", action="store_true", help="Continuously claim durable jobs until SIGTERM or SIGINT")
    parser.add_argument("--idle-seconds", type=float, default=2.0, help="Idle delay for --poll (0.25 to 30 seconds)")
    args = parser.parse_args(argv)

    assert_restore_validation_complete()

    from zou.app import create_app

    app = create_app()
    with app.app_context():
        runtime = build_runtime(app)
        if args.poll:
            if not 0.25 <= args.idle_seconds <= 30:
                parser.error("--idle-seconds must be between 0.25 and 30")
            stop_event = threading.Event()

            def request_stop(_signum, _frame):
                stop_event.set()

            signal.signal(signal.SIGTERM, request_stop)
            signal.signal(signal.SIGINT, request_stop)
            from .worker.main import run_forever

            run_forever(runtime, stop_event=stop_event, idle_seconds=args.idle_seconds)
            return 0

        from .worker.main import process_one

        result = process_one(runtime)
        print(json.dumps({"result": result or "idle"}, sort_keys=True))
        return 0 if result in (None, "completed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
