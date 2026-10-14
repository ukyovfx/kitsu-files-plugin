"""Real Zou/PostgreSQL API plus package worker test for the pinned host suite."""

import hashlib
import io
import json
import os
import subprocess
import sys
from uuid import UUID, uuid4


def _run_worker_until_ready(ready, *, attempts=30):
    """Use separate production Worker processes until this test's row completes."""
    for _ in range(attempts):
        if ready():
            return
        result = subprocess.run(
            [sys.executable, "-m", "plugin.worker_runtime", "--once"],
            check=False, capture_output=True, text=True, env=os.environ.copy(), timeout=30,
        )
        assert result.returncode in (0, 2), "worker process failed"
        if result.stdout:
            try:
                state = json.loads(result.stdout.splitlines()[-1]).get("result")
            except (ValueError, AttributeError):
                state = None
            if state == "idle":
                break
    assert ready(), "durable job did not reach its expected persisted state"

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")


def test_task_api_to_worker_creates_immutable_package_copy_and_verified_download(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, monkeypatch, tmp_path,
):
    from sqlalchemy import select
    from plugin.models import KitsuSyncDesiredConfiguration, KitsuSyncEventOutbox, OutputPackage, OutputPackageFile, OutputPackagePointer
    from plugin.models import WorkingRevision
    from plugin.storage.fake import PersistentFakeStorage
    from zou.app.models.output_type import OutputType

    app = authenticated_plugin_client.vfx_app
    production_id, _entity_type_id, _task_type_id, entity_id, task_id, connection_id, _actor_id = synthetic_upload_task
    fake_state = tmp_path / "fake-storage.json"
    storage = PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    monkeypatch.setenv("VFX_FILES_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_WORKER_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_STORAGE_MODE", "fake")
    monkeypatch.setenv("VFX_FILES_STAGING_ROOT", str(tmp_path))
    monkeypatch.setenv("VFX_FILES_FAKE_STORAGE_ROOT", str(fake_state))
    monkeypatch.setenv("VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id")
    monkeypatch.setenv("VFX_FILES_FAKE_DRIVE_ID", "synthetic-drive-id")
    content = b"artist A working snapshot"
    digest = hashlib.sha256(content).hexdigest()
    with app.app_context():
        output_type = OutputType(name=f"VFXFilesOutput{uuid4().hex[:10]}", short_name=f"vfx{uuid4().hex[:8]}")
        with zou_db_factory.begin() as session:
            session.add(output_type)
            session.flush()
            output_type_id = output_type.id
    monkeypatch.setitem(
        app.extensions, "vfx_files_storage_adapter_factory",
        lambda _connection: PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="synthetic-drive-id"),
    )
    with zou_db_factory.begin() as session:
        desired = session.get(KitsuSyncDesiredConfiguration, production_id)
        selected = dict(desired.desired_config)
        selected["activation"] = {
            "enabled": True, "activated_at": "2026-10-13T00:00:00Z",
            "notification_epoch": 1,
        }
        desired.notification_epoch = 1
        desired.desired_config = selected
    client = authenticated_plugin_client
    origin = "https://kitsu.test"
    headers = {"Origin": origin, "X-VFX-Files-CSRF": client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]}

    # First create a real immutable Working revision through the installed API,
    # then let an independent worker process transfer and publish its file ID.
    working_route = f"/plugins/vfx_files/tasks/{task_id}/working-revisions"
    working = client.post(
        working_route,
        json={"base_revision_id": None, "files": [{"name": "plate.1001.exr", "size": len(content), "sha256": digest}]},
        headers={**headers, "Idempotency-Key": f"working:{uuid4()}"},
    )
    assert working.status_code == 201, working.get_data(as_text=True)
    upload_id = working.json["files"][0]["upload_session_id"]
    for index, start in enumerate(range(0, len(content), 4)):
        chunk = content[start:start + 4]
        part = client.put(
            f"/plugins/vfx_files/uploads/{upload_id}/parts/{index}", data=chunk,
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(content)}",
                "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
            },
        )
        assert part.status_code == 200, part.get_data(as_text=True)
    finalized = client.post(f"/plugins/vfx_files/uploads/{upload_id}/finalize", json={}, headers=headers)
    assert finalized.status_code == 202, finalized.get_data(as_text=True)
    _run_worker_until_ready(lambda: client.get(working_route).json["revisions"][0]["state"] == "completed")
    working_readback = client.get(working_route)
    assert working_readback.status_code == 200
    working_revision = working_readback.json["revisions"][0]
    assert working_revision["version_label"] == "w001"
    working_file = working_revision["files"][0]
    assert working_revision["state"] == "completed"
    assert working_file["provider_object_id"]
    storage = PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    assert storage.verify(working_file["provider_object_id"], len(content), digest)

    # Use a completed DB Working Revision as the source provenance for the
    # Output Package. This also gives the package request a verifiable w001 FK.
    with zou_db_factory.begin() as session:
        source_revision = session.get(WorkingRevision, UUID(working_revision["id"]))
        assert source_revision is not None and source_revision.state == "completed"

    options = client.get(f"/plugins/vfx_files/tasks/{task_id}/output-package-options")
    assert options.status_code == 200, options.get_data(as_text=True)
    output_options = options.json
    assert output_options["task_id"] == str(task_id)
    assert output_options["production_id"] == str(production_id)
    assert output_options["storage_root_id"] == "synthetic-root-id"
    assert str(output_type_id) in {item["id"] for item in output_options["output_types"]}
    selected_output_type_id = next(item["id"] for item in output_options["output_types"] if item["id"] == str(output_type_id))

    idempotency_key = f"output-package:{uuid4()}"
    invalid_output_type = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": str(uuid4()), "package_type": "final-exr", "frame_start": 1001,
            "frame_end": 1001, "source_working_revision_id": working_revision["id"],
            "files": [{"provider_object_id": working_file["provider_object_id"], "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": f"invalid:{uuid4()}"},
    )
    assert invalid_output_type.status_code == 400
    assert "Output Type must be an existing Kitsu record" in invalid_output_type.get_data(as_text=True)

    unrelated_bytes = b"not part of w001"
    unrelated_digest = hashlib.sha256(unrelated_bytes).hexdigest()
    storage.upload(
        io.BytesIO(unrelated_bytes), "unrelated-source", len(unrelated_bytes),
        unrelated_digest, "synthetic-root-id", file_name="unrelated.exr",
    )
    wrong_working_source = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": selected_output_type_id, "package_type": "final-exr",
            "frame_start": 1001, "frame_end": 1001,
            "source_working_revision_id": working_revision["id"],
            "files": [{"provider_object_id": "unrelated-source", "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": f"wrong-working-source:{uuid4()}"},
    )
    assert wrong_working_source.status_code == 400
    assert "must belong to the selected Working revision" in wrong_working_source.get_data(as_text=True)

    response = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": selected_output_type_id,
            "package_type": "final-exr",
            "frame_start": 1001,
            "frame_end": 1001,
            "source_working_revision_id": working_revision["id"],
            "files": [{"provider_object_id": working_file["provider_object_id"], "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert response.status_code == 202, response.get_data(as_text=True)
    reservation = response.json
    replay = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": selected_output_type_id,
            "package_type": "final-exr",
            "frame_start": 1001,
            "frame_end": 1001,
            "source_working_revision_id": working_revision["id"],
            "files": [{"provider_object_id": working_file["provider_object_id"], "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert replay.status_code == 202
    assert replay.json["id"] == reservation["id"]
    assert replay.json["version"] == reservation["version"] == 1

    _run_worker_until_ready(lambda: client.get(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages?limit=10&offset=0"
    ).json[0]["state"] == "completed")

    with zou_db_factory.begin() as session:
        package = session.get(OutputPackage, UUID(reservation["id"]))
        file_row = session.scalar(select(OutputPackageFile).where(OutputPackageFile.package_id == package.id))
        pointer = session.get(OutputPackagePointer, (package.task_id, package.output_type_id, package.package_type))
        assert package.state == "completed"
        assert package.qc_state == "passed"
        assert package.provider_folder_id
        assert str(package.source_working_revision_id) == working_revision["id"]
        assert file_row.source_provider_object_id == working_file["provider_object_id"]
        outbox = session.scalars(select(KitsuSyncEventOutbox).where(
            KitsuSyncEventOutbox.production_id == production_id,
        ).order_by(KitsuSyncEventOutbox.created_at, KitsuSyncEventOutbox.event_type)).all()
        assert {row.event_type for row in outbox} == {"output_package.created", "output_package.ready"}
        assert all(row.storage_connection_id == connection_id and row.payload["activation_epoch"] == 1 for row in outbox)
        assert all(row.state == "queued" for row in outbox)
        assert file_row.provider_object_id != file_row.source_provider_object_id
        assert file_row.sha256 == digest
        assert pointer.latest_output_id == package.id
        storage = PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="synthetic-drive-id")
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
