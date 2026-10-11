from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4
import hashlib

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import func, select


def test_concurrent_snapshot_workers_complete_without_deadlock(synthetic_upload_task, zou_db_factory):
    from plugin.jobs.queue import JobResult, PostgresJobQueue
    from plugin.models import Job, LatestWorkingPointer, UploadSession, WorkingRevision, WorkingRevisionFile
    from plugin.working_files.service import reserve_working_revision

    production_id, _entity_type, _task_type, _entity, task_id, connection_id, actor_id = synthetic_upload_task
    payloads = [b"first file", b"second file"]
    descriptors = [
        {"name": f"file-{index}.bin", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        for index, payload in enumerate(payloads)
    ]
    revision = reserve_working_revision(
        zou_db_factory,
        production_id=production_id,
        task_id=task_id,
        storage_connection_id=connection_id,
        actor_id=actor_id,
        base_revision_id=None,
        idempotency_key=f"working:{uuid4()}",
        files=descriptors,
        dependencies=[],
    )
    worker_rows = []
    with zou_db_factory.begin() as session:
        for file_receipt, payload, descriptor in zip(revision.files, payloads, descriptors, strict=True):
            provider_id = f"fake-{uuid4()}"
            worker_id = uuid4()
            job_id = uuid4()
            upload = session.get(UploadSession, file_receipt.upload_session_id)
            upload.state = "transferring"
            upload.provider_object_id = provider_id
            file_row = session.get(WorkingRevisionFile, file_receipt.id)
            file_row.state = "transferring"
            session.add(Job(
                id=job_id,
                job_type="publish_source",
                idempotency_key=f"working-transfer:{uuid4()}",
                payload={"upload_session_id": str(upload.id), "task_id": str(task_id), "actor_id": str(actor_id)},
                state="leased",
                lease_owner=worker_id,
                lease_generation=1,
                lease_expires_at=func.now() + timedelta(minutes=10),
            ))
            worker_rows.append((job_id, worker_id, provider_id, len(payload), descriptor["sha256"]))

    barrier = Barrier(2)

    def complete(row):
        job_id, worker_id, provider_id, byte_count, digest = row
        barrier.wait(timeout=5)
        return PostgresJobQueue(zou_db_factory).complete_job(
            job_id,
            worker_id,
            1,
            JobResult("completed", True, provider_id, byte_count, digest),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(complete, worker_rows))

    assert results == [True, True]
    with zou_db_factory() as session:
        stored_revision = session.get(WorkingRevision, revision.id)
        pointer = session.get(LatestWorkingPointer, task_id)
        stored_files = session.scalars(
            select(WorkingRevisionFile)
            .where(WorkingRevisionFile.revision_id == revision.id)
            .order_by(WorkingRevisionFile.ordinal)
        ).all()
        assert stored_revision.state == "completed"
        assert pointer.revision_id == revision.id
        assert [row.state for row in stored_files] == ["completed", "completed"]
