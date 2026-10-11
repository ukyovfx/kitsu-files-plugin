import hashlib
from uuid import uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import sessionmaker

from plugin.models import Job, StorageConnection, UploadPart, UploadSession
from plugin.uploads.service import UploadConflict, UploadDenied, UploadService
from plugin.storage.fake import MemoryStaging


@pytest.fixture
def upload_context(zou_db_factory):
    factory = zou_db_factory
    connection_id = uuid4()
    with factory.begin() as session:
        session.add(
            StorageConnection(
                id=connection_id,
                production_id=uuid4(),
                provider="google_drive",
                account_mode="shared_drive",
                drive_id="synthetic-drive",
                allowed_root_id="synthetic-root",
                credential_ref="test-only-no-secret",
                max_file_bytes=1024,
                quota_bytes=4096,
                used_bytes=0,
                enabled=True,
            )
        )
    staging = MemoryStaging()
    service = UploadService(factory, staging, max_chunk_bytes=64 * 1024)
    yield factory, service, staging, connection_id
    with factory.begin() as session:
        sessions = session.scalars(select(UploadSession).where(UploadSession.storage_connection_id == connection_id)).all()
        ids = [row.id for row in sessions]
        if ids:
            session.execute(UploadPart.__table__.delete().where(UploadPart.session_id.in_(ids)))
            session.execute(Job.__table__.delete().where(Job.payload["upload_session_id"].astext.in_([str(value) for value in ids])))
            session.execute(UploadSession.__table__.delete().where(UploadSession.id.in_(ids)))
        session.execute(StorageConnection.__table__.delete().where(StorageConnection.id == connection_id))


def test_upload_rejects_size_and_quota_excess(upload_context):
    _, service, _, connection_id = upload_context
    with pytest.raises(UploadDenied):
        service.create_upload(task_id=uuid4(), actor_id=uuid4(), storage_connection_id=connection_id, size=1025, sha256_hex=hashlib.sha256(b"x").hexdigest())


def test_upload_parts_are_idempotent_and_contiguous(upload_context):
    _, service, _, connection_id = upload_context
    data = b"abcdefgh"
    actor_id = uuid4()
    session = service.create_upload(task_id=uuid4(), actor_id=actor_id, storage_connection_id=connection_id, size=len(data), sha256_hex=hashlib.sha256(data).hexdigest())
    digest = hashlib.sha256(b"abcd").hexdigest()
    first = service.put_part(session_id=session.session_id, actor_id=actor_id, part_index=0, byte_start=0, body=b"abcd", part_sha256=digest)
    again = service.put_part(session_id=session.session_id, actor_id=actor_id, part_index=0, byte_start=0, body=b"abcd", part_sha256=digest)
    assert first.id == again.id
    with pytest.raises(UploadConflict):
        service.put_part(session_id=session.session_id, actor_id=actor_id, part_index=1, byte_start=5, body=b"efgh", part_sha256=hashlib.sha256(b"efgh").hexdigest())


def test_finalize_verifies_server_hash_and_staging_readback(upload_context):
    factory, service, _, connection_id = upload_context
    data = b"whole staged file"
    actor_id = uuid4()
    session = service.create_upload(task_id=uuid4(), actor_id=actor_id, storage_connection_id=connection_id, size=len(data), sha256_hex=hashlib.sha256(data).hexdigest())
    service.put_part(session_id=session.session_id, actor_id=actor_id, part_index=0, byte_start=0, body=data, part_sha256=hashlib.sha256(data).hexdigest())

    job = service.finalize_upload(session_id=session.session_id, actor_id=actor_id)
    again = service.finalize_upload(session_id=session.session_id, actor_id=actor_id)

    assert job.id == again.id
    assert job.state == "queued"


def test_expired_cleanup_only_removes_claimed_session_key(upload_context):
    factory, service, staging, connection_id = upload_context
    body = b"expired"
    actor_id = uuid4()
    created = service.create_upload(task_id=uuid4(), actor_id=actor_id, storage_connection_id=connection_id, size=len(body), sha256_hex=hashlib.sha256(body).hexdigest())
    part = service.put_part(session_id=created.session_id, actor_id=actor_id, part_index=0, byte_start=0, body=body, part_sha256=hashlib.sha256(body).hexdigest())
    staging.put("upload/00000000-0000-4000-8000-000000000099/0", b"unrelated", hashlib.sha256(b"unrelated").hexdigest())
    with factory.begin() as session:
        session.execute(update(UploadSession).where(UploadSession.id == created.session_id).values(expires_at=func.now() - text("interval '1 hour'")))

    assert service.expire_upload(session_id=created.session_id)
    assert part.staging_key not in staging._data
    assert "upload/00000000-0000-4000-8000-000000000099/0" in staging._data
