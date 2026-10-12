import os
import hashlib
from uuid import uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("flask_jwt_extended")

@pytest.fixture
def authenticated_plugin_client(zou_app):
    from zou.app.models.person import Person
    from zou.app.services.persons_service import create_access_token_for_raw_person

    plugin_folder = os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER")
    if not plugin_folder:
        pytest.skip("Set VFX_FILES_TEST_PLUGIN_FOLDER to the installed disposable plugin folder")
    app = zou_app
    app.config.update(
        TESTING=True,
        SECRET_KEY="vfx-files-isolated-test-secret-key-32-bytes-minimum",
        VFX_FILES_ENABLED=True,
        VFX_FILES_KITSU_ORIGIN="https://kitsu.test",
    )
    with app.app_context():
        people = Person.query.order_by(Person.id).all()
        person = next(
            (item for role in ("admin", "manager") for item in people if getattr(item.role, "code", item.role) == role),
            people[0] if people else None,
        )
        token = create_access_token_for_raw_person(person)
        cookie_name = app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie")
        token_locations = app.config.get("JWT_TOKEN_LOCATION")
    client = app.test_client()
    client.set_cookie(cookie_name, token)
    client.vfx_files_token = token
    client.vfx_files_token_locations = token_locations
    client.vfx_app = app
    return client


@pytest.fixture
def synthetic_upload_task(authenticated_plugin_client, zou_db_factory, tmp_path):
    from zou.app.models.entity import Entity
    from zou.app.models.entity_type import EntityType
    from zou.app.models.person import Person
    from zou.app.models.project import Project, ProjectPersonLink
    from zou.app.models.task import Task
    from zou.app.models.task_type import TaskType
    from plugin.models import Job, StorageConnection, UploadPart, UploadSession
    from sqlalchemy import select

    app = authenticated_plugin_client.vfx_app
    with app.app_context():
        people = Person.query.order_by(Person.id).all()
        actor = next(
            (item for role in ("admin", "manager") for item in people if getattr(item.role, "code", item.role) == role),
            people[0] if people else None,
        )
        actor_id = actor.id
        entity_type = EntityType(name=f"VFX{uuid4().hex[:8]}")
        task_type = TaskType(name=f"VFXTask{uuid4().hex[:8]}", for_entity="Shot")
        with zou_db_factory.begin() as session:
            actor = session.get(Person, actor_id)
            project = Project(name=f"VFX Files Test {uuid4()}", team=[actor])
            session.add_all([project, entity_type, task_type])
            session.flush()
            manager_membership = session.get(ProjectPersonLink, (project.id, actor.id))
            if manager_membership is not None:
                manager_membership.role = "manager"
            entity = Entity(name=f"SHOT{uuid4().hex[:8]}", project_id=project.id, entity_type_id=entity_type.id)
            session.add(entity)
            session.flush()
            task = Task(name="main", project_id=project.id, task_type_id=task_type.id, entity_id=entity.id)
            connection = StorageConnection(
                production_id=project.id,
                provider="google_drive",
                account_mode="shared_drive",
                drive_id="synthetic-drive-id",
                allowed_root_id="synthetic-root-id",
                credential_ref="synthetic-reference-only",
                max_file_bytes=1024,
                quota_bytes=4096,
                enabled=True,
            )
            session.add_all([task, connection])
            session.flush()
            values = (project.id, entity_type.id, task_type.id, entity.id, task.id, connection.id, actor.id)

    app.config.update(VFX_FILES_STAGING_ROOT=str(tmp_path), VFX_FILES_MAX_CHUNK_BYTES=4)
    yield values

    project_id, entity_type_id, task_type_id, entity_id, task_id, connection_id, _actor_id = values
    with zou_db_factory.begin() as session:
        uploads = session.scalars(select(UploadSession).where(UploadSession.task_id == task_id)).all()
        upload_ids = [row.id for row in uploads]
        if upload_ids:
            session.execute(UploadPart.__table__.delete().where(UploadPart.session_id.in_(upload_ids)))
            for upload_id in upload_ids:
                job = session.scalar(select(Job).where(Job.idempotency_key == f"upload-finalize:{upload_id}"))
                if job:
                    session.delete(job)
            session.execute(UploadSession.__table__.delete().where(UploadSession.id.in_(upload_ids)))
        session.execute(StorageConnection.__table__.delete().where(StorageConnection.id == connection_id))
        session.execute(Task.__table__.delete().where(Task.id == task_id))
        session.execute(Entity.__table__.delete().where(Entity.id == entity_id))
        project = session.get(Project, project_id)
        if project is not None:
            project.team.clear()
            session.flush()
        session.execute(Project.__table__.delete().where(Project.id == project_id))
        session.execute(TaskType.__table__.delete().where(TaskType.id == task_type_id))
        session.execute(EntityType.__table__.delete().where(EntityType.id == entity_type_id))


def test_upload_routes_write_verified_bytes_and_reuse_finalize_job(authenticated_plugin_client, synthetic_upload_task, zou_db_factory, tmp_path):
    client = authenticated_plugin_client
    production_id, _entity_type, _task_type, _entity, task_id, _connection, _actor = synthetic_upload_task
    data = b"abcdefgh"
    token = client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]
    origin = "https://kitsu.test"
    headers = {"Origin": origin, "X-VFX-Files-CSRF": token}
    route = f"/plugins/vfx_files/tasks/{task_id}/uploads"
    no_csrf = client.post(route, json={"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}, headers={"Origin": origin})
    wrong_origin = client.post(route, json={"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}, headers={**headers, "Origin": "https://attacker.test"})
    assert no_csrf.status_code == 403
    assert wrong_origin.status_code == 403

    created = client.post(
        route,
        json={"size": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        headers=headers,
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    session_id = created.json["id"]

    for index, chunk in enumerate((data[:4], data[4:])):
        start = index * 4
        part_headers = {
            **headers,
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}",
            "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
        }
        first = client.put(f"/plugins/vfx_files/uploads/{session_id}/parts/{index}", data=chunk, headers=part_headers)
        replay = client.put(f"/plugins/vfx_files/uploads/{session_id}/parts/{index}", data=chunk, headers=part_headers)
        assert first.status_code == 200, first.get_data(as_text=True)
        assert replay.json["sha256"] == first.json["sha256"]

    first_finalize = client.post(f"/plugins/vfx_files/uploads/{session_id}/finalize", json={}, headers=headers)
    replay_finalize = client.post(f"/plugins/vfx_files/uploads/{session_id}/finalize", json={}, headers=headers)
    assert first_finalize.status_code == 202
    assert replay_finalize.json["job_id"] == first_finalize.json["job_id"]
    assert client.get(f"/plugins/vfx_files/uploads/{session_id}").json["state"] == "staged"
    assert (tmp_path / "upload" / session_id / "0").read_bytes() + (tmp_path / "upload" / session_id / "1").read_bytes() == data
    assert client.get(f"/plugins/vfx_files/tasks/{task_id}/uploads").json[0]["expected_sha256"] == hashlib.sha256(data).hexdigest()
    overview = client.get(f"/plugins/vfx_files/overview?production_id={production_id}")
    assert overview.status_code == 200
    assert overview.json["tasks"][0]["id"] == str(task_id)
    from plugin.models import Job, UploadPart, UploadSession
    from sqlalchemy import select
    with zou_db_factory() as session:
        stored = session.get(UploadSession, session_id)
        parts = session.scalars(select(UploadPart).where(UploadPart.session_id == session_id).order_by(UploadPart.part_index)).all()
        job = session.get(Job, first_finalize.json["job_id"])
        assert stored.state == "staged"
        assert [part.state for part in parts] == ["staged", "staged"]
        assert job.job_type == "publish_source" and job.state == "queued"


def test_official_plugin_api_accepts_same_origin_kitsu_cookie(authenticated_plugin_client):
    # Zou's Flask app exposes the plugin route without the /api prefix; the
    # official deployment proxy supplies /api to this same origin namespace.
    response = authenticated_plugin_client.get("/plugins/vfx_files/health")
    assert response.status_code == 200, {"status": response.status_code, "body": response.json, "token_locations": authenticated_plugin_client.vfx_files_token_locations}
    assert response.json["enabled"] is True


def test_cookie_mutation_requires_plugin_csrf_and_same_origin(authenticated_plugin_client):
    denied = authenticated_plugin_client.post(
        "/plugins/vfx_files/mutation-probe",
        json={"probe": True},
        headers={"Origin": "https://kitsu.test"},
    )
    assert denied.status_code == 403

    token_response = authenticated_plugin_client.get("/plugins/vfx_files/csrf-token")
    assert token_response.status_code == 200
    token = token_response.json["csrf_token"]
    accepted = authenticated_plugin_client.post(
        "/plugins/vfx_files/mutation-probe",
        json={"probe": True},
        headers={"Origin": "https://kitsu.test", "X-VFX-Files-CSRF": token},
    )
    assert accepted.status_code == 202

    cross_origin = authenticated_plugin_client.post(
        "/plugins/vfx_files/mutation-probe",
        json={"probe": True},
        headers={"Origin": "https://attacker.test", "X-VFX-Files-CSRF": token},
    )
    assert cross_origin.status_code == 403


def test_environment_worker_and_staging_settings_enable_production_uploads(
    authenticated_plugin_client, synthetic_upload_task, tmp_path, monkeypatch
):
    client = authenticated_plugin_client
    production_id, _entity_type, _task_type, _entity, task_id, _connection, _actor = synthetic_upload_task
    app = client.vfx_app
    app.config.pop("VFX_FILES_WORKER_ENABLED", None)
    app.config.pop("VFX_FILES_STAGING_ROOT", None)
    monkeypatch.setenv("VFX_FILES_WORKER_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_STAGING_ROOT", str(tmp_path))

    overview = client.get(f"/plugins/vfx_files/overview?production_id={production_id}")
    assert overview.status_code == 200
    assert overview.json["upload_ready"] is True

    token = client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]
    created = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/uploads",
        json={"size": 4, "sha256": hashlib.sha256(b"test").hexdigest(), "file_name": "test.mov"},
        headers={"Origin": "https://kitsu.test", "X-VFX-Files-CSRF": token},
    )
    assert created.status_code == 201
    assert created.json["id"]
