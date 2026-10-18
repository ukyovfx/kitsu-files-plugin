import hashlib

import pytest
from uuid import uuid4


def test_task_working_revisions_allocate_immutable_w_series(synthetic_upload_task, zou_db_factory):
    from plugin.working_files.service import reserve_working_revision

    production_id, _entity_type, _task_type, _entity, task_id, connection_id, actor_id = synthetic_upload_task
    revision = reserve_working_revision(
        zou_db_factory,
        production_id=production_id,
        task_id=task_id,
        storage_connection_id=connection_id,
        actor_id=actor_id,
        base_revision_id=None,
        idempotency_key=f"working:{uuid4()}",
        files=[{"name": "plate.exr", "size": 7, "sha256": hashlib.sha256(b"pixels!", usedforsecurity=False).hexdigest()}],
        dependencies=[],
    )

    assert revision.version == 1
    assert revision.version_label == "w001"
    assert revision.state == "receiving"
    assert len(revision.files) == 1


def test_incomplete_working_snapshot_cannot_advance_latest(synthetic_upload_task, zou_db_factory):
    from plugin.working_files.service import WorkingConflict, complete_working_revision, reserve_working_revision

    production_id, _entity_type, _task_type, _entity, task_id, connection_id, actor_id = synthetic_upload_task
    revision = reserve_working_revision(
        zou_db_factory,
        production_id=production_id,
        task_id=task_id,
        storage_connection_id=connection_id,
        actor_id=actor_id,
        base_revision_id=None,
        idempotency_key=f"working:{uuid4()}",
        files=[
            {"name": "scene.nk", "size": 4, "sha256": hashlib.sha256(b"nuke", usedforsecurity=False).hexdigest()},
            {"name": "render.exr", "size": 5, "sha256": hashlib.sha256(b"image", usedforsecurity=False).hexdigest()},
        ],
        dependencies=[],
    )

    with pytest.raises(WorkingConflict, match="all files"):
        complete_working_revision(zou_db_factory, revision_id=revision.id)


def test_stale_base_working_revision_never_replaces_latest(synthetic_upload_task, zou_db_factory):
    from plugin.working_files.service import WorkingConflict, complete_working_revision, record_verified_file, reserve_working_revision

    production_id, _entity_type, _task_type, _entity, task_id, connection_id, actor_id = synthetic_upload_task

    def reserve(key, base):
        return reserve_working_revision(
            zou_db_factory,
            production_id=production_id,
            task_id=task_id,
            storage_connection_id=connection_id,
            actor_id=actor_id,
            base_revision_id=base,
            idempotency_key=key,
            files=[{"name": f"{key}.nk", "size": 3, "sha256": hashlib.sha256(key.encode()).hexdigest()}],
            dependencies=[],
        )

    first = reserve(f"working:{uuid4()}", None)
    concurrent = reserve(f"working:{uuid4()}", None)
    record_verified_file(zou_db_factory, revision_file_id=first.files[0].id, provider_object_id="fake-first", byte_count=3, sha256=first.files[0].sha256)
    complete_working_revision(zou_db_factory, revision_id=first.id)
    record_verified_file(zou_db_factory, revision_file_id=concurrent.files[0].id, provider_object_id="fake-concurrent", byte_count=3, sha256=concurrent.files[0].sha256)

    with pytest.raises(WorkingConflict, match="stale base"):
        complete_working_revision(zou_db_factory, revision_id=concurrent.id)


def test_each_working_file_respects_the_production_file_limit(synthetic_upload_task, zou_db_factory):
    from plugin.working_files.service import WorkingDenied, reserve_working_revision

    production_id, _entity_type, _task_type, _entity, task_id, connection_id, actor_id = synthetic_upload_task
    content = b"x" * 1025
    with pytest.raises(WorkingDenied, match="file limits"):
        reserve_working_revision(
            zou_db_factory,
            production_id=production_id,
            task_id=task_id,
            storage_connection_id=connection_id,
            actor_id=actor_id,
            base_revision_id=None,
            idempotency_key=f"working:{uuid4()}",
            files=[{"name": "too-large.bin", "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}],
            dependencies=[],
        )


def test_custom_working_file_type_is_persisted_without_changing_legacy_version_identity(synthetic_upload_task, zou_db_factory):
    from plugin.models import WorkingRevision, WorkingRevisionFile
    from plugin.working_files.service import reserve_working_revision
    from sqlalchemy import select

    production_id, _entity_type, _task_type, _entity, task_id, connection_id, actor_id = synthetic_upload_task
    receipt = reserve_working_revision(
        zou_db_factory,
        production_id=production_id,
        task_id=task_id,
        storage_connection_id=connection_id,
        actor_id=actor_id,
        base_revision_id=None,
        idempotency_key=f"working:{uuid4()}",
        files=[{"name": "camera-track.csv", "size": 3, "sha256": hashlib.sha256(b"csv").hexdigest(), "category": "Tracking Data"}],
        dependencies=[],
    )
    with zou_db_factory() as session:
        revision = session.get(WorkingRevision, receipt.id)
        file_row = session.scalar(select(WorkingRevisionFile).where(WorkingRevisionFile.revision_id == receipt.id))
        assert revision.version == 1
        assert receipt.version_label == "w001"
        assert file_row.category == "Tracking Data"


@pytest.mark.parametrize("file_type", ["", "../outside", "x" * 33, "type\\escape"])
def test_custom_working_file_type_rejects_unsafe_or_unbounded_labels(synthetic_upload_task, zou_db_factory, file_type):
    from plugin.working_files.service import reserve_working_revision

    production_id, _entity_type, _task_type, _entity, task_id, connection_id, actor_id = synthetic_upload_task
    with pytest.raises(ValueError, match="file type"):
        reserve_working_revision(
            zou_db_factory,
            production_id=production_id,
            task_id=task_id,
            storage_connection_id=connection_id,
            actor_id=actor_id,
            base_revision_id=None,
            idempotency_key=f"working:{uuid4()}",
            files=[{"name": "custom.dat", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest(), "category": file_type}],
            dependencies=[],
        )
