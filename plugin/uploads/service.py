"""Quota-checked, resumable browser uploads to durable server staging."""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import re
from uuid import UUID, uuid4

from sqlalchemy import func, select

from ..models import Job, StorageConnection, UploadPart, UploadSession


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UploadConflict(Exception):
    pass


class UploadDenied(Exception):
    pass


@dataclass(frozen=True)
class UploadCreated:
    session_id: UUID
    expires_at: object
    max_chunk_bytes: int


@dataclass(frozen=True)
class UploadPartReceipt:
    id: UUID
    session_id: UUID
    part_index: int
    byte_start: int
    byte_count: int
    sha256: str
    state: str
    staging_key: str


@dataclass(frozen=True)
class UploadJobReceipt:
    id: UUID
    state: str


def _part_receipt(part):
    return UploadPartReceipt(part.id, part.session_id, part.part_index, part.byte_start, part.byte_count, part.sha256, part.state, part.staging_key)


class UploadService:
    def __init__(self, session_factory, staging, *, max_chunk_bytes=8 * 1024 * 1024, ttl_hours=24):
        if not (64 * 1024 <= max_chunk_bytes <= 64 * 1024 * 1024):
            raise ValueError("Chunk size must be between 64 KiB and 64 MiB")
        if not (1 <= ttl_hours <= 168):
            raise ValueError("Upload expiry must be between one hour and seven days")
        self.session_factory = session_factory
        self.staging = staging
        self.max_chunk_bytes = max_chunk_bytes
        self.ttl_hours = ttl_hours

    def create_upload(self, *, task_id: UUID, actor_id: UUID, storage_connection_id: UUID, size: int, sha256_hex: str, file_name: str = "unnamed") -> UploadCreated:
        if size <= 0 or not SHA256_RE.fullmatch(sha256_hex):
            raise ValueError("Upload size and expected SHA-256 are required")
        if not file_name or len(file_name) > 512 or any(ord(char) < 32 for char in file_name) or "/" in file_name or "\\" in file_name:
            raise ValueError("Upload file name is invalid")
        session_id = uuid4()
        with self.session_factory.begin() as session:
            connection = session.scalar(
                select(StorageConnection).where(StorageConnection.id == storage_connection_id).with_for_update()
            )
            if connection is None or not connection.enabled:
                raise UploadDenied("Storage connection is unavailable")
            if size > connection.max_file_bytes or connection.used_bytes + size > connection.quota_bytes:
                raise UploadDenied("Upload exceeds storage size or quota limit")
            connection.used_bytes += size
            expires_at = session.scalar(select(func.now())) + timedelta(hours=self.ttl_hours)
            record = UploadSession(
                id=session_id,
                task_id=task_id,
                actor_id=actor_id,
                storage_connection_id=storage_connection_id,
                file_name=file_name,
                expected_size=size,
                expected_sha256=sha256_hex,
                received_size=0,
                staging_key=f"upload/{session_id}",
                expires_at=expires_at,
            )
            session.add(record)
        return UploadCreated(session_id, expires_at, self.max_chunk_bytes)

    def put_part(self, *, session_id: UUID, actor_id: UUID, part_index: int, byte_start: int, body: bytes, part_sha256: str):
        if not body or len(body) > self.max_chunk_bytes or not SHA256_RE.fullmatch(part_sha256):
            raise ValueError("Chunk size or checksum is invalid")
        actual_hash = hashlib.sha256(body).hexdigest()
        if actual_hash != part_sha256:
            raise UploadConflict("Chunk checksum does not match its bytes")
        with self.session_factory.begin() as session:
            upload = session.scalar(select(UploadSession).where(UploadSession.id == session_id).with_for_update())
            if upload is None or upload.actor_id != actor_id:
                raise UploadDenied("Upload session is unavailable")
            now = session.scalar(select(func.now()))
            if upload.expires_at <= now:
                raise UploadConflict("Upload session expired")
            if upload.state != "receiving":
                raise UploadConflict("Upload is closed or expired")
            existing = session.scalar(select(UploadPart).where(UploadPart.session_id == session_id, UploadPart.part_index == part_index))
            if existing is not None:
                if (existing.byte_start, existing.byte_count, existing.sha256) != (byte_start, len(body), part_sha256):
                    raise UploadConflict("Chunk index was already used for different bytes")
                if existing.state == "staged":
                    return _part_receipt(existing)
                part = existing
                key = existing.staging_key
            else:
                if part_index < 0 or byte_start != upload.received_size or byte_start + len(body) > upload.expected_size:
                    raise UploadConflict("Chunks must be contiguous, ordered and within the declared size")
                key = f"{upload.staging_key}/{part_index}"
                part = UploadPart(
                    session_id=session_id,
                    part_index=part_index,
                    byte_start=byte_start,
                    byte_count=len(body),
                    sha256=part_sha256,
                    staging_key=key,
                    state="reserved",
                )
                session.add(part)
                upload.received_size += len(body)
                session.flush()
        # The reservation is durable before file I/O. A restart can repeat this
        # exact staging write and finalize will reject it until readback is staged.
        self.staging.put(key, body, part_sha256)
        with self.session_factory.begin() as session:
            part = session.scalar(
                select(UploadPart).where(
                    UploadPart.session_id == session_id,
                    UploadPart.part_index == part_index,
                    UploadPart.sha256 == part_sha256,
                    UploadPart.staging_key == key,
                ).with_for_update()
            )
            if part is None:
                raise UploadConflict("Chunk reservation disappeared after staging write")
            part.state = "staged"
            session.flush()
            return _part_receipt(part)

    def finalize_upload(self, *, session_id: UUID, actor_id: UUID):
        with self.session_factory.begin() as session:
            upload = session.scalar(select(UploadSession).where(UploadSession.id == session_id).with_for_update())
            if upload is None or upload.actor_id != actor_id:
                raise UploadDenied("Upload session is unavailable")
            job_key = f"upload-finalize:{session_id}"
            if upload.state in ("staged", "transferring", "completed", "reconcile_required"):
                job = session.scalar(select(Job).where(Job.idempotency_key == job_key))
                return UploadJobReceipt(job.id, job.state) if job else None
            if upload.expires_at <= session.scalar(select(func.now())):
                raise UploadConflict("Upload session expired")
            parts = session.scalars(select(UploadPart).where(UploadPart.session_id == session_id).order_by(UploadPart.part_index)).all()
            if upload.state not in ("receiving", "verifying") or upload.received_size != upload.expected_size:
                raise UploadConflict("Upload is not complete")
            expected_start = 0
            for index, part in enumerate(parts):
                if part.state != "staged" or part.part_index != index or part.byte_start != expected_start:
                    raise UploadConflict("Upload contains a gap or reordered chunk")
                expected_start += part.byte_count
            if expected_start != upload.expected_size:
                raise UploadConflict("Upload size does not match declared bytes")
            upload.state = "verifying"
            staged = [(p.staging_key, p.byte_count, p.sha256) for p in parts]

        digest = hashlib.sha256()
        count = 0
        for key, size, expected_hash in staged:
            part_digest = hashlib.sha256()
            with self.staging.open(key) as stream:
                while chunk := stream.read(1024 * 1024):
                    count += len(chunk)
                    digest.update(chunk)
                    part_digest.update(chunk)
            if part_digest.hexdigest() != expected_hash:
                self._mark_failed(session_id)
                raise UploadConflict("Staged chunk readback checksum failed")
        with self.session_factory.begin() as session:
            upload = session.scalar(select(UploadSession).where(UploadSession.id == session_id).with_for_update())
            if upload is None:
                raise UploadConflict("Upload disappeared during verification")
            if upload.state == "staged":
                job = session.scalar(select(Job).where(Job.idempotency_key == job_key))
                return UploadJobReceipt(job.id, job.state) if job else None
            if upload.state != "verifying":
                raise UploadConflict("Upload state changed during verification")
            if count != upload.expected_size or digest.hexdigest() != upload.expected_sha256:
                upload.state = "failed_integrity"
                integrity_failed = True
            else:
                integrity_failed = False
                upload.state = "staged"
                job = Job(
                    job_type="publish_source",
                    idempotency_key=job_key,
                    payload={"upload_session_id": str(session_id), "task_id": str(upload.task_id), "actor_id": str(actor_id)},
                    state="queued",
                )
                session.add(job)
                session.flush()
                job_receipt = UploadJobReceipt(job.id, job.state)
        if integrity_failed:
            raise UploadConflict("Staged source does not match declared size and SHA-256")
        return job_receipt

    def _mark_failed(self, session_id):
        with self.session_factory.begin() as session:
            upload = session.scalar(select(UploadSession).where(UploadSession.id == session_id).with_for_update())
            if upload is not None and upload.state == "verifying":
                upload.state = "failed_integrity"

    def expire_upload(self, *, session_id: UUID):
        """Delete only recorded staging keys, then release quota after cleanup succeeds."""
        with self.session_factory.begin() as session:
            upload = session.scalar(select(UploadSession).where(UploadSession.id == session_id).with_for_update())
            if upload is None or upload.state not in ("receiving", "verifying", "cleanup_pending"):
                return False
            if upload.state in ("receiving", "verifying") and upload.expires_at > session.scalar(select(func.now())):
                return False
            upload.state = "cleanup_pending"
            keys = session.scalars(select(UploadPart.staging_key).where(UploadPart.session_id == session_id)).all()
            connection_id = upload.storage_connection_id
            expected_size = upload.expected_size
        self.staging.delete_session_chunks(str(session_id), keys)
        with self.session_factory.begin() as session:
            upload = session.scalar(select(UploadSession).where(UploadSession.id == session_id).with_for_update())
            connection = session.scalar(select(StorageConnection).where(StorageConnection.id == connection_id).with_for_update())
            if upload is None or upload.state != "cleanup_pending" or connection is None:
                return False
            connection.used_bytes = max(0, connection.used_bytes - expected_size)
            upload.state = "expired"
        return True
