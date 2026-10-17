import hashlib
import io
from uuid import uuid4

import pytest

from plugin.compat import HOST_ZOU_VERSION, SCHEMA_EPOCH, WORKER_BUILD
from plugin.storage.base import StorageObject
from plugin.jobs.queue import PostgresJobQueue
from plugin.storage.reconciliation import enqueue_drive_reconciliation, make_drive_reconciliation_handler, metadata_fingerprint, record_observations
from plugin.worker.leases import WorkerContext
from plugin.worker.main import WorkerRuntime, process_one


def test_drive_reconciliation_persists_distinct_provider_observations(synthetic_upload_task, zou_db_factory):
    from sqlalchemy import select
    from plugin.models import DriveReconciliationObservation

    production_id, _entity_type, _task_type, _entity, _task, connection_id, _actor = synthetic_upload_task
    existing = StorageObject(
        "drive-file-1", "synthetic-root-id", 4, None, "synthetic-drive-id",
        "application/octet-stream", name="plate.exr", modified_time="2026-10-13T00:00:00Z", md5_checksum="abcd",
    )
    changed = StorageObject(
        "drive-file-2", "synthetic-root-id", 9, None, "synthetic-drive-id",
        "application/octet-stream", name="track.csv", modified_time="2026-10-13T00:01:00Z", md5_checksum="efgh",
    )
    expected = [
        {"object_id": "drive-file-1", "parent_id": "synthetic-root-id", "metadata_fingerprint": metadata_fingerprint(existing)},
        {"object_id": "drive-file-2", "parent_id": "synthetic-root-id", "metadata_fingerprint": "0" * 64},
        {"object_id": "drive-file-3", "parent_id": "synthetic-root-id"},
    ]
    run_id = uuid4()
    with zou_db_factory.begin() as session:
        rows = record_observations(
            session,
            reconciliation_id=run_id,
            production_id=production_id,
            storage_connection_id=connection_id,
            allowed_root_id="synthetic-root-id",
            expected=expected,
            observed={existing.object_id: existing, changed.object_id: changed},
            listing_complete=True,
        )
        assert {row.state for row in rows} == {"present", "modified", "missing"}
    with zou_db_factory() as session:
        persisted = list(session.scalars(select(DriveReconciliationObservation).where(
            DriveReconciliationObservation.reconciliation_id == run_id
        )))
        assert len(persisted) == 3
        assert {row.provider_object_id: row.state for row in persisted} == {
            "drive-file-1": "present", "drive-file-2": "modified", "drive-file-3": "missing",
        }
    with zou_db_factory.begin() as session:
        denied = record_observations(
            session,
            reconciliation_id=uuid4(),
            production_id=production_id,
            storage_connection_id=connection_id,
            allowed_root_id="synthetic-root-id",
            expected=expected[:1],
            observed={},
            listing_complete=False,
            inaccessible=True,
            error_code="provider_permission_denied",
        )
        assert denied[0].state == "inaccessible"
        persisted_denied = session.scalar(select(DriveReconciliationObservation).where(
            DriveReconciliationObservation.reconciliation_id == denied[0].reconciliation_id,
            DriveReconciliationObservation.provider_object_id == "drive-file-1",
        ))
        assert persisted_denied.error_code == "provider_permission_denied"


def test_drive_reconciliation_never_marks_incomplete_listing_as_missing(synthetic_upload_task, zou_db_factory):
    from sqlalchemy import select
    from plugin.models import DriveReconciliationObservation

    production_id, _entity_type, _task_type, _entity, _task, connection_id, _actor = synthetic_upload_task
    run_id = uuid4()
    with zou_db_factory.begin() as session:
        rows = record_observations(
            session,
            reconciliation_id=run_id,
            production_id=production_id,
            storage_connection_id=connection_id,
            allowed_root_id="synthetic-root-id",
            expected=[{"object_id": "drive-file-1"}],
            observed={},
            listing_complete=False,
        )
        assert rows[0].state == "stale"
    with zou_db_factory() as session:
        row = session.scalar(select(DriveReconciliationObservation).where(
            DriveReconciliationObservation.reconciliation_id == run_id
        ))
        assert row.state == "stale"
        assert row.error_code is None


def test_drive_reconciliation_rejects_scope_mismatch(synthetic_upload_task, zou_db_factory):
    from plugin.storage.base import PermissionFailure

    production_id, _entity_type, _task_type, _entity, _task, connection_id, _actor = synthetic_upload_task
    with zou_db_factory.begin() as session, pytest.raises(PermissionFailure):
        record_observations(
            session,
            reconciliation_id=uuid4(),
            production_id=production_id,
            storage_connection_id=connection_id,
            allowed_root_id="other-root",
            expected=[{"object_id": "drive-file-1"}],
            observed={},
            listing_complete=True,
        )


def test_durable_worker_reconciliation_uses_fake_storage_and_persists_missing_modified_present(synthetic_upload_task, zou_db_factory):
    from sqlalchemy import select
    from plugin.models import DriveReconciliationObservation, Job
    from plugin.storage.fake import FakeStorage

    production_id, _entity_type, _task_type, _entity, _task, connection_id, actor_id = synthetic_upload_task
    adapter = FakeStorage("synthetic-root-id", "synthetic-drive-id")
    first = adapter.upload(io.BytesIO(b"plate"), "drive-file-present", 5, hashlib.sha256(b"plate").hexdigest(), "synthetic-root-id", file_name="plate.exr")
    second = adapter.upload(io.BytesIO(b"track"), "drive-file-modified", 5, hashlib.sha256(b"track").hexdigest(), "synthetic-root-id", file_name="track.csv")
    expected = [
        {"object_id": first.object_id, "parent_id": "synthetic-root-id", "metadata_fingerprint": metadata_fingerprint(first)},
        {"object_id": second.object_id, "parent_id": "synthetic-root-id", "metadata_fingerprint": "0" * 64},
        {"object_id": "drive-file-missing", "parent_id": "synthetic-root-id"},
    ]
    reconciliation_id = uuid4()
    with zou_db_factory.begin() as session:
        job = enqueue_drive_reconciliation(
            session,
            reconciliation_id=reconciliation_id,
            production_id=production_id,
            storage_connection_id=connection_id,
            allowed_root_id="synthetic-root-id",
            actor_id=actor_id,
            expected_objects=expected,
        )
        replay = enqueue_drive_reconciliation(
            session,
            reconciliation_id=reconciliation_id,
            production_id=production_id,
            storage_connection_id=connection_id,
            allowed_root_id="synthetic-root-id",
            actor_id=actor_id,
            expected_objects=expected,
        )
        assert replay.id == job.id
    handler = make_drive_reconciliation_handler(zou_db_factory, lambda _connection: adapter)
    from plugin.worker_runtime import _authorize_job
    runtime = WorkerRuntime(
        queue=PostgresJobQueue(zou_db_factory),
        context=WorkerContext(uuid4(), WORKER_BUILD, HOST_ZOU_VERSION, SCHEMA_EPOCH),
        authorize=_authorize_job,
        handlers={"reconcile_drive_objects": handler},
    )
    assert process_one(runtime) == "completed"
    with zou_db_factory() as session:
        rows = list(session.scalars(select(DriveReconciliationObservation).where(
            DriveReconciliationObservation.reconciliation_id == reconciliation_id
        )))
        assert {row.provider_object_id: row.state for row in rows} == {
            "drive-file-present": "present",
            "drive-file-modified": "modified",
            "drive-file-missing": "missing",
        }
        assert len(rows) == 3
    with zou_db_factory.begin() as session:
        session.execute(Job.__table__.delete().where(Job.idempotency_key == f"drive-reconcile:{reconciliation_id}"))
