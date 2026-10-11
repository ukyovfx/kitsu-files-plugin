import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4


def _headers(client):
    token = client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]
    return {"Origin": "https://kitsu.test", "X-VFX-Files-CSRF": token}


def _worker_once(tmp_path, monkeypatch):
    monkeypatch.setenv("VFX_FILES_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_WORKER_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_STORAGE_MODE", "fake")
    monkeypatch.setenv("VFX_FILES_STAGING_ROOT", str(tmp_path))
    monkeypatch.setenv("VFX_FILES_FAKE_STORAGE_ROOT", str(tmp_path / "multi-file-storage.json"))
    monkeypatch.setenv("VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id")
    result = subprocess.run(
        [sys.executable, "-m", "plugin.worker_runtime", "--once"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["result"] == "completed"


def test_multi_file_snapshot_stays_unpublished_until_every_file_is_verified(
    authenticated_plugin_client, synthetic_upload_task, zou_app, zou_db_factory, tmp_path, monkeypatch
):
    client = authenticated_plugin_client
    _production_id, _entity_type, _task_type, _entity_id, task_id, _connection_id, _actor_id = synthetic_upload_task
    route = f"/plugins/vfx_files/tasks/{task_id}/working-revisions"
    files = [
        ("scene.blend", b"scene snapshot"),
        ("textures.json", b"texture manifest"),
    ]
    declarations = [
        {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files
    ]
    headers = _headers(client)
    reserved = client.post(
        route,
        json={"base_revision_id": None, "files": declarations},
        headers={**headers, "Idempotency-Key": f"multi-file:{uuid4()}"},
    )
    assert reserved.status_code == 201, reserved.get_data(as_text=True)
    assert reserved.json["version_label"] == "w001"

    for slot, (_name, data) in zip(reserved.json["files"], files, strict=True):
        upload_id = slot["upload_session_id"]
        for index, start in enumerate(range(0, len(data), 4)):
            chunk = data[start:start + 4]
            part_headers = {
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}",
                "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
            }
            response = client.put(
                f"/plugins/vfx_files/uploads/{upload_id}/parts/{index}",
                data=chunk,
                headers=part_headers,
            )
            assert response.status_code == 200, response.get_data(as_text=True)
        finalized = client.post(
            f"/plugins/vfx_files/uploads/{upload_id}/finalize",
            json={},
            headers=headers,
        )
        assert finalized.status_code == 202, finalized.get_data(as_text=True)

    assert client.get(route).json["latest_working_id"] is None
    _worker_once(tmp_path, monkeypatch)
    after_first_file = client.get(route)
    assert after_first_file.status_code == 200
    assert after_first_file.json["latest_working_id"] is None
    assert after_first_file.json["revisions"][0]["state"] in {"receiving", "transferring"}
    assert sum(file["state"] == "completed" for file in after_first_file.json["revisions"][0]["files"]) == 1

    # Crash the separate worker after it has written the second file to
    # FakeStorage, but before it can persist the completed job result.
    environment = os.environ.copy()
    environment.update({
        "SECRET_KEY": "vfx-files-isolated-test-secret-key-32-bytes-minimum",
        "PLUGIN_FOLDER": os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER", "/opt/zou/plugins"),
        "PYTHONPATH": os.environ.get("PYTHONPATH", "/opt/zou/plugins"),
    })
    crashed = subprocess.run(
        [sys.executable, "/tmp/vfx-kitsu-test-root/tests/e2e/crash_worker_after_provider_write.py"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert crashed.returncode == 86, crashed.stderr

    from sqlalchemy import func, select, text, update
    from plugin.models import Job, UploadSession
    with zou_app.app_context(), zou_db_factory.begin() as session:
        interrupted = session.scalar(
            select(UploadSession).where(
                UploadSession.working_revision_file_id.is_not(None),
                UploadSession.state == "transferring",
            )
        )
        assert interrupted is not None and interrupted.provider_object_id
        interrupted_upload_id = interrupted.id
        reserved_provider_id = interrupted.provider_object_id
        stranded_job = session.scalar(
            select(Job).where(Job.payload["upload_session_id"].astext == str(interrupted_upload_id))
        )
        assert stranded_job.state == "leased"
        session.execute(
            update(Job).where(Job.id == stranded_job.id).values(
                lease_expires_at=func.now() - text("interval '1 second'"),
                available_at=func.now() - text("interval '1 second'"),
            )
        )

    still_incomplete = client.get(route)
    assert still_incomplete.json["latest_working_id"] is None
    _worker_once(tmp_path, monkeypatch)
    with zou_db_factory() as session:
        recovered = session.get(UploadSession, interrupted_upload_id)
        assert recovered.state == "completed"
        assert recovered.provider_object_id == reserved_provider_id

    completed = client.get(route)
    assert completed.status_code == 200
    assert completed.json["latest_working_id"] == reserved.json["id"]
    revision = completed.json["revisions"][0]
    assert revision["state"] == "completed"
    assert revision["version_label"] == "w001"
    assert revision["manifest_sha256"]
    assert {item["name"] for item in revision["files"]} == {name for name, _ in files}
    for item, (_name, data) in zip(revision["files"], files, strict=True):
        downloaded = client.get(f"/plugins/vfx_files/working-files/{item['id']}/download")
        assert downloaded.status_code == 200
        assert downloaded.data == data
