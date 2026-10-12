import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytest.importorskip("zou")


def test_upload_worker_recovers_after_process_dies_post_transfer(
    zou_app, zou_db_factory, synthetic_upload_task, tmp_path
):
    from plugin.models import Job, UploadPart, UploadSession
    from plugin.storage.fake import PersistentFakeStorage
    from plugin.uploads.staging import FileSystemStaging

    _production_id, _entity_type_id, _task_type_id, _entity_id, task_id, connection_id, actor_id = synthetic_upload_task
    source = b"synthetic plate bytes"
    source_hash = hashlib.sha256(source).hexdigest()
    upload_id = uuid4()
    job_id = uuid4()
    staging = FileSystemStaging(tmp_path)
    staging.put(f"upload/{upload_id}/0", source, source_hash)
    with zou_app.app_context(), zou_db_factory.begin() as session:
        session.add(UploadSession(
            id=upload_id, task_id=task_id, actor_id=actor_id,
            storage_connection_id=connection_id, state="staged",
            expected_size=len(source), expected_sha256=source_hash,
            received_size=len(source), staging_key=f"upload/{upload_id}",
            expires_at=datetime.now(timezone.utc),
            file_name="plate.mov",
        ))
        session.flush()
        session.add(UploadPart(
            session_id=upload_id, part_index=0, byte_start=0,
            byte_count=len(source), sha256=source_hash,
            staging_key=f"upload/{upload_id}/0", state="staged",
        ))
        session.add(Job(
            id=job_id, job_type="publish_source",
            idempotency_key=f"upload-finalize:{upload_id}",
            payload={"upload_session_id": str(upload_id), "task_id": str(task_id), "actor_id": str(actor_id)},
            state="queued",
        ))

    environment = os.environ.copy()
    environment.update({
        "SECRET_KEY": "vfx-files-isolated-test-secret-key-32-bytes-minimum",
        "VFX_FILES_ENABLED": "true",
        "VFX_FILES_WORKER_ENABLED": "true",
        "VFX_FILES_STORAGE_MODE": "fake",
        "VFX_FILES_STAGING_ROOT": str(tmp_path),
        "VFX_FILES_FAKE_STORAGE_ROOT": str(tmp_path / "fake-storage" / "state.json"),
        "VFX_FILES_FAKE_ROOT_ID": "synthetic-root-id",
        "VFX_FILES_TEST_PLUGIN_FOLDER": os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER", "/opt/zou/plugins"),
        "PYTHONPATH": os.environ.get("PYTHONPATH", "/opt/zou/plugins"),
    })
    crashed = subprocess.run(
        [sys.executable, "/tmp/vfx-kitsu-test-root/tests/e2e/crash_worker_after_provider_write.py"],
        check=False, capture_output=True, text=True, timeout=30, env=environment,
    )
    assert crashed.returncode == 86, crashed.stderr
    from sqlalchemy import func, text, update
    with zou_app.app_context(), zou_db_factory.begin() as session:
        stranded_upload = session.get(UploadSession, upload_id)
        stranded_job = session.get(Job, job_id)
        assert stranded_upload.state == "transferring"
        assert stranded_upload.provider_object_id
        assert stranded_job.state == "leased"
        provider_id = stranded_upload.provider_object_id
        session.execute(update(Job).where(Job.id == job_id).values(
            lease_expires_at=func.now() - text("interval '1 second'"),
            available_at=func.now() - text("interval '1 second'"),
        ))

    completed = subprocess.run(
        [sys.executable, "-m", "vfx_files.worker_runtime", "--once"],
        check=False, capture_output=True, text=True, timeout=30, env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"result": "completed"' in completed.stdout
    with zou_db_factory() as session:
        stored = session.get(UploadSession, upload_id)
        job = session.get(Job, job_id)
        assert stored.state == "completed"
        assert stored.provider_object_id == provider_id
        assert stored.provider_size == len(source)
        assert stored.provider_sha256 == source_hash
        assert job.state == "completed"
        provider_id = stored.provider_object_id
    adapter = PersistentFakeStorage(tmp_path / "fake-storage" / "state.json", root_id="synthetic-root-id")
    assert adapter.verify(provider_id, len(source), source_hash)
    assert sum(1 for obj in adapter.objects.values() if obj.size is not None) == 1


def test_upload_worker_reconciles_ambiguous_provider_success_without_duplicate(zou_app, zou_db_factory, synthetic_upload_task):
    # The provider may have accepted an upload and lost its response. The same
    # reserved provider ID must be read back and verified on the worker retry.
    from plugin.jobs.queue import PostgresJobQueue
    from plugin.models import Job, UploadPart, UploadSession
    from plugin.storage.fake import FakeStorage, MemoryStaging
    from plugin.uploads.worker import make_upload_handler
    from sqlalchemy import select

    _production_id, _entity_type_id, _task_type_id, _entity_id, task_id, connection_id, actor_id = synthetic_upload_task
    source = b"ambiguous provider result"
    source_hash = hashlib.sha256(source).hexdigest()
    upload_id = uuid4()
    job_id = uuid4()
    staging = MemoryStaging()
    staging.put(f"upload/{upload_id}/0", source, source_hash)
    with zou_app.app_context(), zou_db_factory.begin() as session:
        session.add(UploadSession(
            id=upload_id, task_id=task_id, actor_id=actor_id,
            storage_connection_id=connection_id, state="staged",
            expected_size=len(source), expected_sha256=source_hash,
            received_size=len(source), staging_key=f"upload/{upload_id}",
            expires_at=datetime.now(timezone.utc), file_name="ambiguous.mov",
        ))
        session.flush()
        session.add(UploadPart(session_id=upload_id, part_index=0, byte_start=0,
            byte_count=len(source), sha256=source_hash,
            staging_key=f"upload/{upload_id}/0", state="staged"))
        session.add(Job(id=job_id, job_type="publish_source",
            idempotency_key=f"upload-finalize:{upload_id}",
            payload={"upload_session_id": str(upload_id)}, state="queued"))

    adapter = FakeStorage(root_id="synthetic-root-id")
    adapter.fail_upload_after_write = True
    handler = make_upload_handler(zou_db_factory, staging, adapter)
    queue = PostgresJobQueue(zou_db_factory)
    worker_id = uuid4()
    with zou_app.app_context():
        lease = queue.claim_job(worker_id)
        first_result = handler(lease)
        assert first_result.state == "completed"
        assert queue.complete_job(job_id, worker_id, lease.generation, first_result)
        assert len([call for call in adapter.calls if call[0] == "upload"]) == 1

