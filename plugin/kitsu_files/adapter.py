"""Thin adapter over the verified Zou 1.0.94/1.0.96 OutputFile service."""

from contextlib import contextmanager
from dataclasses import dataclass
import os

from sqlalchemy import text


class NativeCreateAmbiguous(Exception):
    """A native call began but no authoritative result was returned."""


@dataclass(frozen=True)
class NativeOutputLink:
    output_file_id: str
    native_revision: int


@contextmanager
def _native_revision_lock(key: str):
    from zou.app import db

    lock_key = f"vfx-files-output:{key}"
    with db.engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {"key": lock_key})
        connection.commit()
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": lock_key})
            connection.commit()


class ZouOutputFileAdapter:
    def __init__(self, create_revision=None, lock_factory=_native_revision_lock):
        self.create_revision = create_revision
        self.lock_factory = lock_factory

    def create_native_output(self, permit, *, entity_id: str) -> NativeOutputLink:
        mapping = permit.family_mapping
        lock_key = ":".join((str(entity_id), mapping["output_type_id"], mapping["task_type_id"], mapping["representation"], mapping["name"]))
        with self.lock_factory(lock_key):
            try:
                created = self._create_revision(
                    entity_id=entity_id,
                    working_file_id=str(permit.source_working_file_id) if permit.source_working_file_id else None,
                    output_type_id=mapping["output_type_id"],
                    person_id=str(permit.actor_id),
                    task_type_id=mapping["task_type_id"],
                    revision=0,
                    representation=mapping["representation"],
                    name=mapping["name"],
                    extension=mapping["extension"],
                    file_status_id=mapping["file_status_id"],
                )
                if (
                    os.environ.get("VFX_FILES_STORAGE_MODE") == "fake"
                    and os.environ.get("VFX_FILES_FAKE_CRASH_AFTER_NATIVE_OUTPUT_PUBLISH_ID") == str(permit.publish_id)
                ):
                    os._exit(87)
            except Exception as exc:
                # The caller has already consumed the database permit. Never retry here.
                raise NativeCreateAmbiguous("Zou OutputFile create outcome requires reconciliation") from exc
        if not created or not created.get("id") or int(created.get("revision", 0)) < 1:
            raise NativeCreateAmbiguous("Zou OutputFile response did not confirm its ID and revision")
        return NativeOutputLink(str(created["id"]), int(created["revision"]))

    def _create_revision(self, **kwargs):
        if self.create_revision is not None:
            return self.create_revision(**kwargs)
        from zou.app.services.files_service import create_new_output_revision

        return create_new_output_revision(**kwargs)
