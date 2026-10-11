import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4


def _headers(client):
    token = client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]
    return {"Origin": "https://kitsu.test", "X-VFX-Files-CSRF": token}


def test_working_snapshot_api_runs_real_worker_and_advances_latest(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, tmp_path, monkeypatch
):
    client = authenticated_plugin_client
    production_id, _entity_type, _task_type, _entity, task_id, _connection, _actor = synthetic_upload_task
    data = b"artist A working snapshot"
    digest = hashlib.sha256(data).hexdigest()
    headers = _headers(client)
    route = f"/plugins/vfx_files/tasks/{task_id}/working-revisions"

    denied_no_csrf = client.post(route, json={"files": []}, headers={"Origin": "https://kitsu.test"})
    denied_cross_origin = client.post(
        route,
        json={"files": []},
        headers={**headers, "Origin": "https://attacker.test"},
    )
    assert denied_no_csrf.status_code == 403
    assert denied_cross_origin.status_code == 403

    reserved = client.post(
        route,
        json={"base_revision_id": None, "files": [{"name": "comp.nk", "size": len(data), "sha256": digest}]},
        headers={**headers, "Idempotency-Key": f"working:{uuid4()}"},
    )
    assert reserved.status_code == 201, reserved.get_data(as_text=True)
    assert reserved.json["version_label"] == "w001"
    assert reserved.json["files"][0]["upload_session_id"]
    upload_id = reserved.json["files"][0]["upload_session_id"]

    for index, start in enumerate(range(0, len(data), 4)):
        chunk = data[start:start + 4]
        part_headers = {
            **headers,
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}",
            "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
        }
        response = client.put(f"/plugins/vfx_files/uploads/{upload_id}/parts/{index}", data=chunk, headers=part_headers)
        assert response.status_code == 200, response.get_data(as_text=True)

    finalized = client.post(f"/plugins/vfx_files/uploads/{upload_id}/finalize", json={}, headers=headers)
    assert finalized.status_code == 202, finalized.get_data(as_text=True)
    assert client.get(route).json["latest_working_id"] is None

    fake_state = tmp_path / "fake-storage.json"
    monkeypatch.setenv("VFX_FILES_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_WORKER_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_STORAGE_MODE", "fake")
    monkeypatch.setenv("VFX_FILES_STAGING_ROOT", str(tmp_path))
    monkeypatch.setenv("VFX_FILES_FAKE_STORAGE_ROOT", str(fake_state))
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

    revisions = client.get(route)
    assert revisions.status_code == 200
    assert revisions.json["latest_working_id"] == reserved.json["id"]
    completed = revisions.json["revisions"][0]
    assert completed["state"] == "completed"
    assert completed["version_label"] == "w001"
    assert completed["manifest_sha256"]
    assert completed["files"][0]["provider_object_id"]
    assert completed["files"][0]["sha256"] == digest

    from plugin.storage.fake import PersistentFakeStorage

    storage = PersistentFakeStorage(fake_state, root_id="synthetic-root-id")
    provider_id = completed["files"][0]["provider_object_id"]
    assert storage.verify(provider_id, len(data), digest)
    downloaded = client.get(
        f"/plugins/vfx_files/working-files/{completed['files'][0]['id']}/download"
    )
    assert downloaded.status_code == 200
    assert downloaded.data == data

    duplicate_worker = subprocess.run(
        [sys.executable, "-m", "plugin.worker_runtime", "--once"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=30,
    )
    assert duplicate_worker.returncode == 0, duplicate_worker.stderr
    assert json.loads(duplicate_worker.stdout.splitlines()[-1])["result"] == "idle"
    storage_after_retry = PersistentFakeStorage(fake_state, root_id="synthetic-root-id")
    assert sum(1 for item in storage_after_retry.objects.values() if item.size is not None) == 1
    assert client.get(route).json["latest_working_id"] == reserved.json["id"]

    stale_base = client.post(
        route,
        json={"base_revision_id": None, "files": [{"name": "stale.nk", "size": 3, "sha256": hashlib.sha256(b"old").hexdigest()}]},
        headers={**headers, "Idempotency-Key": f"working:{uuid4()}"},
    )
    assert stale_base.status_code == 409
    assert client.get(route).json["latest_working_id"] == reserved.json["id"]
