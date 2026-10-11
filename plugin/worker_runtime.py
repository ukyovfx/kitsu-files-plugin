"""Concrete Zou process entrypoint for the Files durable queue.

Google credentials are not required for the default disabled worker. The
process only enables the disk-backed fake adapter when the explicit test
storage mode and a disposable fake-storage directory are configured.
"""

import argparse
import json
import os
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


def build_runtime(app):
    if not _enabled("VFX_FILES_WORKER_ENABLED"):
        raise RuntimeError("VFX Files worker is disabled")
    if os.environ.get("VFX_FILES_STORAGE_MODE") != "fake":
        raise RuntimeError("No configured StorageAdapter is available to this worker build")
    staging_root = os.environ.get("VFX_FILES_STAGING_ROOT")
    fake_root = os.environ.get("VFX_FILES_FAKE_STORAGE_ROOT")
    if not staging_root or not fake_root:
        raise RuntimeError("Worker staging and fake storage roots are required")

    from sqlalchemy.orm import sessionmaker
    from zou import __version__ as host_version
    from zou.app import db

    from .compat import SCHEMA_EPOCH, WORKER_BUILD
    from .jobs.queue import PostgresJobQueue
    from .storage.fake import PersistentFakeStorage
    from .folders.worker import make_task_folder_handler
    from .uploads.staging import FileSystemStaging
    from .uploads.worker import make_upload_handler
    from .worker.leases import WorkerContext
    from .worker.main import WorkerRuntime

    sessions = sessionmaker(bind=db.engine, expire_on_commit=False)
    queue = PostgresJobQueue(sessions)
    adapter = PersistentFakeStorage(
        fake_root,
        root_id=os.environ.get("VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id"),
        drive_id=os.environ.get("VFX_FILES_FAKE_DRIVE_ID"),
    )
    staging = FileSystemStaging(staging_root)
    worker_id = UUID(os.environ["VFX_FILES_WORKER_ID"]) if os.environ.get("VFX_FILES_WORKER_ID") else uuid4()
    return WorkerRuntime(
        queue=queue,
        context=WorkerContext(worker_id, WORKER_BUILD, host_version, SCHEMA_EPOCH),
        authorize=_authorize_job,
        handlers={
            "publish_source": make_upload_handler(sessions, staging, adapter),
            "provision_task_folders": make_task_folder_handler(sessions, adapter),
        },
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a VFX Files worker process")
    parser.add_argument("--once", action="store_true", help="Claim at most one durable job")
    args = parser.parse_args(argv)
    if not args.once:
        parser.error("Only --once is supported by this isolated worker entrypoint")

    from zou.app import create_app

    app = create_app()
    with app.app_context():
        runtime = build_runtime(app)
        from .worker.main import process_one

        result = process_one(runtime)
        print(json.dumps({"result": result or "idle"}, sort_keys=True))
        return 0 if result in (None, "completed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
