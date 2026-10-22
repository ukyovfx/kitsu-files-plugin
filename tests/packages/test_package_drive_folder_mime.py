"""Output import must accept Google Drive's folder MIME type as well as FakeStorage's."""

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import io
from uuid import UUID, uuid4

import pytest

from plugin.storage.base import FOLDER_MIME_TYPES, StorageObject, is_folder
from plugin.storage.fake import FakeStorage

pytest.importorskip("sqlalchemy")

DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


def test_is_folder_accepts_fake_and_drive_values_only():
    assert set(FOLDER_MIME_TYPES) == {"folder", DRIVE_FOLDER_MIME}
    assert is_folder(StorageObject("a", None, None, None, mime_type="folder"))
    assert is_folder(StorageObject("a", None, None, None, mime_type=DRIVE_FOLDER_MIME))
    assert not is_folder(StorageObject("a", "p", 1, None, mime_type="application/octet-stream"))
    assert not is_folder(StorageObject("a", "p", 1, None, mime_type=None))
    assert not is_folder(None)


class DriveShapedStorage(FakeStorage):
    """FakeStorage whose folders carry Google Drive's real MIME type."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        root = self.objects[self.root_id]
        self.objects[self.root_id] = replace(root, mime_type=DRIVE_FOLDER_MIME)

    def create_folder(self, parent_id, reserved_id, correlation):
        try:
            super().create_folder(parent_id, reserved_id, correlation)
        finally:
            created = self.objects.get(reserved_id)
            if created is not None:
                self.objects[reserved_id] = replace(created, mime_type=DRIVE_FOLDER_MIME)
        return self.objects[reserved_id]  # like the Drive adapter, return the provider-shaped object


@pytest.fixture
def drive_ctx(authenticated_plugin_client, synthetic_upload_task, zou_db_factory, monkeypatch):
    from sqlalchemy import text
    from plugin.models import KitsuSyncDesiredConfiguration
    from zou.app.models.output_type import OutputType

    app = authenticated_plugin_client.vfx_app
    production_id, _et, _tt, entity_id, task_id, connection_id, admin_id = synthetic_upload_task
    storage = DriveShapedStorage(root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    storage.create_folder(storage.root_id, "source-folder", {"display_name": "Shared Inputs"})
    monkeypatch.setitem(app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: storage)
    with app.app_context(), zou_db_factory.begin() as session:
        output_type = OutputType(name=f"VFXMime{uuid4().hex[:10]}", short_name=f"mim{uuid4().hex[:8]}")
        session.add(output_type)
        session.flush()
        output_type_id = output_type.id
        desired = session.get(KitsuSyncDesiredConfiguration, production_id)
        selected = dict(desired.desired_config)
        selected["activation"] = {"enabled": True, "activated_at": "2026-10-13T00:00:00Z", "notification_epoch": 1}
        desired.notification_epoch = 1
        desired.desired_config = selected
    yield dict(app=app, client=authenticated_plugin_client, db=zou_db_factory, storage=storage,
               production_id=production_id, entity_id=entity_id, task_id=task_id, connection_id=connection_id,
               admin_id=admin_id, output_type_id=output_type_id)
    with zou_db_factory.begin() as session:  # never leave claimable jobs for other tests' workers
        session.execute(text(
            "UPDATE plugin_vfx_files_job SET state = 'completed', lease_owner = NULL, lease_expires_at = NULL "
            "WHERE payload->>'task_id' = :t AND state IN ('queued','retry','leased')"), {"t": str(task_id)})


def _source(storage, name, content):
    object_id = f"src-{uuid4().hex}"
    storage.upload(io.BytesIO(content), object_id, len(content), sha256(content).hexdigest(), "source-folder", file_name=name)
    return object_id


def test_package_verification_completes_when_the_provider_reports_drive_folder_mime(drive_ctx):
    from sqlalchemy import func, select
    from plugin.jobs.queue import LeasedJob
    from plugin.models import Job, OutputPackage
    from plugin.packages.service import reserve_output_package
    from plugin.packages.worker import make_output_package_handler

    c = drive_ctx
    content = b"exr-frame-bytes"
    source_id = _source(c["storage"], "plate.1001.exr", content)
    reservation = reserve_output_package(
        c["db"], task_id=c["task_id"], production_id=c["production_id"], entity_id=c["entity_id"],
        output_type_id=c["output_type_id"], package_type="final-exr", actor_id=c["admin_id"],
        idempotency_key=f"k-{uuid4()}", storage_connection_id=c["connection_id"],
        files=[{"source_provider_object_id": source_id, "file_name": "plate.1001.exr", "byte_count": len(content),
                "sha256": sha256(content).hexdigest(), "frame_number": 1001, "file_format": "exr",
                "color_space": "ACEScg", "is_proxy": False}],
        frame_start=1001, frame_end=1001, color_space="ACEScg",
    )
    worker_id = uuid4()
    with c["db"].begin() as session:
        job = session.get(Job, reservation.job_id)
        job.state, job.lease_owner, job.lease_generation = "leased", worker_id, 1
        job.lease_expires_at = session.scalar(select(func.now())) + timedelta(minutes=2)
        leased = LeasedJob(job.id, job.job_type, dict(job.payload), worker_id, 1, 1)
    result = make_output_package_handler(c["db"], lambda _c: c["storage"])(leased)
    assert (result.state, result.error_code) == ("completed", None)
    with c["db"].begin() as session:
        package = session.get(OutputPackage, reservation.package_id)
        assert package.state == "completed"
        assert c["storage"].get(package.provider_folder_id).mime_type == DRIVE_FOLDER_MIME


def test_registering_a_drive_folder_as_an_output_file_is_rejected(drive_ctx):
    c = drive_ctx
    client = c["client"]
    csrf = client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]
    response = client.post(
        f"/plugins/vfx_files/tasks/{c['task_id']}/output-packages",
        json={"output_type_id": str(c["output_type_id"]), "package_type": "tracking-data",
              "files": [{"provider_object_id": "source-folder"}]},
        headers={"Origin": "https://kitsu.test", "X-VFX-Files-CSRF": csrf, "Idempotency-Key": f"k-{uuid4()}"},
    )
    assert response.status_code == 404  # a folder is not a file, whatever MIME type the provider uses
