"""Real Zou/PostgreSQL API plus package worker test for the pinned host suite."""

from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")


def test_task_api_to_worker_creates_immutable_package_copy_and_verified_download(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, monkeypatch,
):
    from sqlalchemy import func, select
    from plugin.jobs.queue import LeasedJob
    from plugin.models import Job, OutputPackage, OutputPackageFile, OutputPackagePointer
    from plugin.packages.worker import make_output_package_handler
    from plugin.storage.fake import FakeStorage
    from zou.app.models.output_type import OutputType

    app = authenticated_plugin_client.vfx_app
    production_id, _entity_type_id, _task_type_id, entity_id, task_id, _connection_id, _actor_id = synthetic_upload_task
    content = b"synthetic-final-exr-frame"
    digest = sha256(content).hexdigest()
    storage = FakeStorage(root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    storage.create_folder(storage.root_id, "source-folder", {"display_name": "Shared Inputs"})
    storage.upload(__import__("io").BytesIO(content), "source-file", len(content), digest, "source-folder", file_name="plate.1001.exr")
    with app.app_context():
        output_type = OutputType(name=f"VFXFilesOutput{uuid4().hex[:10]}", short_name=f"vfx{uuid4().hex[:8]}")
        with zou_db_factory.begin() as session:
            session.add(output_type)
            session.flush()
            output_type_id = output_type.id
    monkeypatch.setitem(app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: storage)
    client = authenticated_plugin_client
    origin = "https://kitsu.test"
    headers = {"Origin": origin, "X-VFX-Files-CSRF": client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]}
    idempotency_key = f"output-package:{uuid4()}"
    response = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": str(output_type_id),
            "package_type": "final-exr",
            "frame_start": 1001,
            "frame_end": 1001,
            "files": [{"provider_object_id": "source-file", "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert response.status_code == 202, response.get_data(as_text=True)
    reservation = response.json
    replay = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": str(output_type_id),
            "package_type": "final-exr",
            "frame_start": 1001,
            "frame_end": 1001,
            "files": [{"provider_object_id": "source-file", "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert replay.status_code == 202
    assert replay.json["id"] == reservation["id"]
    assert replay.json["version"] == reservation["version"] == 1

    worker_id = uuid4()
    with zou_db_factory.begin() as session:
        job = session.get(Job, UUID(reservation["job_id"]))
        assert job is not None
        job.state = "leased"
        job.lease_owner = worker_id
        job.lease_generation = 1
        job.lease_expires_at = session.scalar(select(func.now())) + timedelta(minutes=2)
        leased = LeasedJob(job.id, job.job_type, dict(job.payload), worker_id, job.lease_generation, job.attempts + 1)
    handler = make_output_package_handler(zou_db_factory, lambda _connection: storage)
    result = handler(leased)
    assert result.state == "completed"
    assert handler(leased).state == "completed"  # Lost completion response replay is idempotent.

    with zou_db_factory.begin() as session:
        package = session.get(OutputPackage, UUID(reservation["id"]))
        file_row = session.scalar(select(OutputPackageFile).where(OutputPackageFile.package_id == package.id))
        pointer = session.get(OutputPackagePointer, (package.task_id, package.output_type_id, package.package_type))
        assert package.state == "completed"
        assert package.qc_state == "passed"
        assert package.provider_folder_id
        assert file_row.source_provider_object_id == "source-file"
        assert file_row.provider_object_id != file_row.source_provider_object_id
        assert file_row.sha256 == digest
        assert pointer.latest_output_id == package.id
        copied = storage.get(file_row.provider_object_id)
        assert copied.parent_id == package.provider_folder_id
        assert copied.name == "plate.1001.exr"
        assert storage.verify(copied.object_id, len(content), digest)
        package_file_id = file_row.id

    listed = client.get(f"/plugins/vfx_files/tasks/{task_id}/output-packages?limit=10&offset=0")
    assert listed.status_code == 200
    assert listed.json[0]["id"] == reservation["id"]
    assert listed.json[0]["qc_state"] == "passed"
    assert "files" not in listed.json[0]["manifest_summary"]
    file_page = client.get(f"/plugins/vfx_files/output-packages/{reservation['id']}/files?limit=1&offset=0")
    assert file_page.status_code == 200
    assert file_page.json["next_offset"] is None
    download = client.get(f"/plugins/vfx_files/output-package-files/{package_file_id}/download")
    assert download.status_code == 200
    assert download.data == content
