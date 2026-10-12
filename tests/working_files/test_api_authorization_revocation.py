import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import select

pytest.importorskip("zou")


def _artist_client(app, project_id, task_id):
    from zou.app import db
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import TaskPersonLink
    from zou.app.services.persons_service import create_access_token_for_raw_person

    artist = Person(
        first_name="Synthetic",
        last_name=f"Revocation{uuid4().hex[:8]}",
        email=f"revocation-{uuid4().hex}@example.invalid",
        role="user",
    )
    with app.app_context():
        db.session.add(artist)
        db.session.flush()
        db.session.add(ProjectPersonLink(project_id=project_id, person_id=artist.id, role="user"))
        db.session.add(TaskPersonLink(task_id=task_id, person_id=artist.id))
        db.session.commit()
        token = create_access_token_for_raw_person(artist)
        artist_id = artist.id
        cookie_name = app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie")
    client = app.test_client()
    client.set_cookie(cookie_name, token)
    return client, artist_id


def _csrf_headers(client):
    token = client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]
    return {"Origin": "https://kitsu.test", "X-VFX-Files-CSRF": token}


def _reserve_and_stage(client, task_id, filename, data):
    digest = hashlib.sha256(data).hexdigest()
    headers = _csrf_headers(client)
    route = f"/plugins/vfx_files/tasks/{task_id}/working-revisions"
    reserved = client.post(
        route,
        json={"base_revision_id": None, "files": [{"name": filename, "size": len(data), "sha256": digest}]},
        headers={**headers, "Idempotency-Key": f"working:{uuid4()}"},
    )
    assert reserved.status_code == 201, reserved.get_data(as_text=True)
    session_id = reserved.json["files"][0]["upload_session_id"]
    chunk_size = 4
    for index, start in enumerate(range(0, len(data), chunk_size)):
        chunk = data[start:start + chunk_size]
        part_headers = {
            **headers,
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}",
            "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
        }
        staged = client.put(f"/plugins/vfx_files/uploads/{session_id}/parts/{index}", data=chunk, headers=part_headers)
        assert staged.status_code == 200, staged.get_data(as_text=True)
    return route, reserved, headers, session_id


def _revoke_project_membership(zou_db_factory, project_id, artist_id):
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import Task, TaskPersonLink

    with zou_db_factory.begin() as session:
        task_ids = session.scalars(select(Task.id).where(Task.project_id == project_id)).all()
        session.execute(
            TaskPersonLink.__table__.delete().where(
                TaskPersonLink.task_id.in_(task_ids),
                TaskPersonLink.person_id == artist_id,
            )
        )
        session.execute(
            ProjectPersonLink.__table__.delete().where(
                ProjectPersonLink.project_id == project_id,
                ProjectPersonLink.person_id == artist_id,
            )
        )


def test_authorization_revocation_blocks_upload_finalization_before_job_creation(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory
):
    from plugin.models import Job, UploadSession
    from sqlalchemy import select

    app = authenticated_plugin_client.vfx_app
    project_id, _entity_type, _task_type, _entity, task_id, _connection, _manager_id = synthetic_upload_task
    client, artist_id = _artist_client(app, project_id, task_id)
    try:
        route, reserved, headers, session_id = _reserve_and_stage(client, task_id, "revoked.nk", b"scene")
        _revoke_project_membership(zou_db_factory, project_id, artist_id)
        finalized = client.post(f"/plugins/vfx_files/uploads/{session_id}/finalize", json={}, headers=headers)
        assert finalized.status_code == 403
        with zou_db_factory() as session:
            upload = session.get(UploadSession, session_id)
            job = session.scalar(select(Job).where(Job.idempotency_key == f"upload-finalize:{session_id}"))
            assert upload.state == "receiving"
            assert job is None
        assert client.get(route).status_code == 403
    finally:
        from zou.app.models.person import Person
        from zou.app.models.project import ProjectPersonLink
        from zou.app.models.task import TaskPersonLink

        with zou_db_factory.begin() as session:
            session.execute(TaskPersonLink.__table__.delete().where(TaskPersonLink.task_id == task_id, TaskPersonLink.person_id == artist_id))
            session.execute(ProjectPersonLink.__table__.delete().where(ProjectPersonLink.project_id == project_id, ProjectPersonLink.person_id == artist_id))
            session.execute(Person.__table__.delete().where(Person.id == artist_id))


def test_authorization_revocation_blocks_download_of_completed_working_file(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, tmp_path, monkeypatch
):
    app = authenticated_plugin_client.vfx_app
    project_id, _entity_type, _task_type, _entity, task_id, _connection, _manager_id = synthetic_upload_task
    client, artist_id = _artist_client(app, project_id, task_id)
    try:
        route, reserved, headers, session_id = _reserve_and_stage(client, task_id, "completed.nk", b"done")
        finalized = client.post(f"/plugins/vfx_files/uploads/{session_id}/finalize", json={}, headers=headers)
        assert finalized.status_code == 202, finalized.get_data(as_text=True)

        environment = os.environ.copy()
        environment.update({
            "SECRET_KEY": "vfx-files-isolated-test-secret-key-32-bytes-minimum",
            "VFX_FILES_ENABLED": "true",
            "VFX_FILES_WORKER_ENABLED": "true",
            "VFX_FILES_STORAGE_MODE": "fake",
            "VFX_FILES_STAGING_ROOT": str(tmp_path),
            "VFX_FILES_FAKE_STORAGE_ROOT": str(tmp_path / "revocation-fake-storage.json"),
            "VFX_FILES_FAKE_ROOT_ID": "synthetic-root-id",
            "VFX_FILES_TEST_PLUGIN_FOLDER": os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER", "/opt/zou/plugins"),
            "PYTHONPATH": os.environ.get("PYTHONPATH", "/opt/zou/plugins"),
        })
        monkeypatch.setenv("VFX_FILES_ENABLED", "true")
        monkeypatch.setenv("VFX_FILES_STORAGE_MODE", "fake")
        monkeypatch.setenv("VFX_FILES_FAKE_STORAGE_ROOT", str(tmp_path / "revocation-fake-storage.json"))
        monkeypatch.setenv("VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id")
        worker = subprocess.run(
            [sys.executable, "-m", "plugin.worker_runtime", "--once"],
            check=False, capture_output=True, text=True, env=environment, timeout=30,
        )
        assert worker.returncode == 0, worker.stderr
        assert json.loads(worker.stdout.splitlines()[-1])["result"] == "completed"
        revision = client.get(route)
        assert revision.status_code == 200
        file_id = revision.json["revisions"][0]["files"][0]["id"]

        _revoke_project_membership(zou_db_factory, project_id, artist_id)
        downloaded = client.get(f"/plugins/vfx_files/working-files/{file_id}/download")
        assert downloaded.status_code == 403
        assert client.get(route).status_code == 403
    finally:
        from zou.app.models.person import Person
        from zou.app.models.project import ProjectPersonLink
        from zou.app.models.task import TaskPersonLink

        with zou_db_factory.begin() as session:
            session.execute(TaskPersonLink.__table__.delete().where(TaskPersonLink.task_id == task_id, TaskPersonLink.person_id == artist_id))
            session.execute(ProjectPersonLink.__table__.delete().where(ProjectPersonLink.project_id == project_id, ProjectPersonLink.person_id == artist_id))
            session.execute(Person.__table__.delete().where(Person.id == artist_id))
