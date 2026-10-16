import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4


def _headers(client):
    token = client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]
    return {"Origin": "https://kitsu.test", "X-VFX-Files-CSRF": token}


def _upload_snapshot(client, task_id, data, *, base_revision_id, name):
    digest = hashlib.sha256(data).hexdigest()
    headers = _headers(client)
    route = f"/plugins/vfx_files/tasks/{task_id}/working-revisions"
    reserved = client.post(
        route,
        json={
            "base_revision_id": base_revision_id,
            "files": [{"name": name, "size": len(data), "sha256": digest}],
        },
        headers={**headers, "Idempotency-Key": f"working:{uuid4()}"},
    )
    assert reserved.status_code == 201, reserved.get_data(as_text=True)
    upload_id = reserved.json["files"][0]["upload_session_id"]
    max_chunk_bytes = 4  # synthetic_upload_task configures the pinned host limit to four bytes
    for index, start in enumerate(range(0, len(data), max_chunk_bytes)):
        chunk = data[start:start + max_chunk_bytes]
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
    return reserved.json, finalized.json


def _run_worker(tmp_path, monkeypatch):
    fake_state = tmp_path / "multi-artist-fake-storage.json"
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


def test_artist_b_downloads_w001_and_publishes_w002_without_replacing_history(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, tmp_path, monkeypatch
):
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import TaskPersonLink
    from zou.app.services.persons_service import create_access_token_for_raw_person

    artist_a = authenticated_plugin_client
    production_id, _entity_type, _task_type, _entity_id, task_id, _connection_id, _artist_a_id = synthetic_upload_task
    app = artist_a.vfx_app
    monkeypatch.setitem(app.config, "VFX_FILES_FAKE_STORAGE_ROOT", str(tmp_path / "multi-artist-fake-storage.json"))
    monkeypatch.setitem(app.config, "VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id")
    monkeypatch.setitem(app.config, "VFX_FILES_FAKE_DRIVE_ID", "synthetic-drive-id")
    artist_b_id = None
    try:
        with app.app_context():
            artist_b = Person(
                first_name="Synthetic",
                last_name=f"Artist {uuid4().hex[:8]}",
                email=f"artist-{uuid4().hex}@example.invalid",
                role="user",
            )
            with zou_db_factory.begin() as session:
                session.add(artist_b)
                session.flush()
                artist_b_id = artist_b.id
                session.add_all([
                    ProjectPersonLink(project_id=production_id, person_id=artist_b_id, role="user"),
                    TaskPersonLink(task_id=task_id, person_id=artist_b_id),
                ])
        with app.app_context():
            artist_b = app.extensions["sqlalchemy"].session.get(Person, artist_b_id)
            token = create_access_token_for_raw_person(artist_b)
        artist_b_client = app.test_client()
        artist_b_client.set_cookie(
            app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"),
            token,
        )

        first_bytes = b"Artist A approved working base"
        first, _ = _upload_snapshot(
            artist_a,
            task_id,
            first_bytes,
            base_revision_id=None,
            name="comp-w001.nk",
        )
        assert first["version_label"] == "w001"
        _run_worker(tmp_path, monkeypatch)
        first_file_id = first["files"][0]["id"]

        artist_b_download = artist_b_client.get(
            f"/plugins/vfx_files/working-files/{first_file_id}/download"
        )
        assert artist_b_download.status_code == 200
        assert artist_b_download.data == first_bytes

        second_bytes = b"Artist B revised working snapshot"
        second, _ = _upload_snapshot(
            artist_b_client,
            task_id,
            second_bytes,
            base_revision_id=first["id"],
            name="comp-w002.nk",
        )
        assert second["version_label"] == "w002"
        _run_worker(tmp_path, monkeypatch)

        revisions_response = artist_b_client.get(
            f"/plugins/vfx_files/tasks/{task_id}/working-revisions"
        )
        assert revisions_response.status_code == 200
        assert revisions_response.json["latest_working_id"] == second["id"]
        versions = {revision["version_label"]: revision for revision in revisions_response.json["revisions"]}
        assert versions["w001"]["state"] == "completed"
        assert versions["w002"]["state"] == "completed"
        assert versions["w001"]["files"][0]["provider_object_id"] != versions["w002"]["files"][0]["provider_object_id"]

        for label, payload in (("w001", first_bytes), ("w002", second_bytes)):
            file_id = versions[label]["files"][0]["id"]
            downloaded = artist_b_client.get(f"/plugins/vfx_files/working-files/{file_id}/download")
            assert downloaded.status_code == 200
            assert downloaded.data == payload
    finally:
        if artist_b_id is not None:
            with zou_db_factory.begin() as session:
                session.query(TaskPersonLink).filter_by(task_id=task_id, person_id=artist_b_id).delete()
                session.query(ProjectPersonLink).filter_by(project_id=production_id, person_id=artist_b_id).delete()
                session.query(Person).filter_by(id=artist_b_id).delete()
