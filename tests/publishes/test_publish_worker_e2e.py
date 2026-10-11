import hashlib
import json
import os
import subprocess
import sys
from uuid import UUID, uuid4

from sqlalchemy import select


def _headers(client):
    token = client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]
    return {"Origin": "https://kitsu.test", "X-VFX-Files-CSRF": token}


def _run_worker(tmp_path, state_path):
    environment = os.environ.copy()
    environment.update({
        "SECRET_KEY": "vfx-files-isolated-test-secret-key-32-bytes-minimum",
        "VFX_FILES_ENABLED": "true",
        "VFX_FILES_WORKER_ENABLED": "true",
        "VFX_FILES_STORAGE_MODE": "fake",
        "VFX_FILES_STAGING_ROOT": str(tmp_path),
        "VFX_FILES_FAKE_STORAGE_ROOT": str(state_path),
        "VFX_FILES_FAKE_ROOT_ID": "synthetic-root-id",
        "VFX_FILES_TEST_PLUGIN_FOLDER": "/opt/zou/plugins",
        "PYTHONPATH": "/opt/zou/plugins",
    })
    result = subprocess.run(
        [sys.executable, "-m", "vfx_files.worker_runtime", "--once"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["result"] == "completed"


def test_working_file_publish_runs_api_database_worker_fake_storage_and_native_zou_link(
    authenticated_plugin_client, synthetic_upload_task, zou_app, zou_db_factory, tmp_path, monkeypatch
):
    from plugin.models import (
        FolderBinding,
        Job,
        NativeCreateEvent,
        OutputFamilyMapping,
        Publish,
        PublishCheckpoint,
        PublishPointer,
        ReconciliationFinding,
        StorageConnection,
        WorkingRevision,
        WorkingRevisionFile,
    )
    from plugin.storage.fake import PersistentFakeStorage
    from zou.app.models.entity import Entity
    from zou.app.models.file_status import FileStatus
    from zou.app.models.output_file import OutputFile
    from zou.app.models.output_type import OutputType
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import Task
    from zou.app.models.task import TaskPersonLink
    from zou.app.models.task_type import TaskType
    from zou.app.models.working_file import WorkingFile

    client = authenticated_plugin_client
    app = client.vfx_app
    production_id, _entity_type_id, task_type_id, _entity_id, task_id, connection_id, _actor_id = synthetic_upload_task
    state_path = tmp_path / "publish-storage.json"
    # Flask's app object is shared across the pinned-host test session. Keep
    # this workflow's FakeStorage selection scoped to this test so later API
    # download tests cannot read a stale provider root.
    monkeypatch.setitem(app.config, "VFX_FILES_STORAGE_MODE", "fake")
    monkeypatch.setitem(app.config, "VFX_FILES_FAKE_STORAGE_ROOT", str(state_path))
    monkeypatch.setitem(app.config, "VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id")
    monkeypatch.setitem(app.config, "VFX_FILES_STAGING_ROOT", str(tmp_path))
    storage = PersistentFakeStorage(state_path, root_id="synthetic-root-id")
    destination_id = storage.reserve_id(f"publish-folder:{uuid4()}")
    storage.create_folder("synthetic-root-id", destination_id, {"display_name": "synthetic-publish"})
    family_key = f"synthetic-{uuid4()}"
    output_type_name = f"VFXOutput{uuid4().hex[:10]}"
    status_name = f"VFXStatus{uuid4().hex[:10]}"
    output_type = OutputType(name=output_type_name, short_name=output_type_name[:20])
    file_status = FileStatus(name=status_name, color="#33AA77")

    with app.app_context(), zou_db_factory.begin() as session:
        connection = session.get(StorageConnection, connection_id)
        session.add(FolderBinding(
            storage_connection_id=connection_id,
            allowed_root_id=connection.allowed_root_id,
            canonical_scope_key=f"production:{production_id}:publish:{family_key}",
            parent_provider_id=connection.allowed_root_id,
            provider_folder_id=destination_id,
            state="ready",
        ))
        session.add_all([output_type, file_status])
        session.flush()

    data = b"verified Working File for formal Publish"
    digest = hashlib.sha256(data).hexdigest()
    headers = _headers(client)
    route = f"/plugins/vfx_files/tasks/{task_id}/working-revisions"
    reserved = client.post(
        route,
        json={"base_revision_id": None, "files": [{"name": "comp.exr", "size": len(data), "sha256": digest}]},
        headers={**headers, "Idempotency-Key": f"publish-source:{uuid4()}"},
    )
    assert reserved.status_code == 201, reserved.get_data(as_text=True)
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
        assert response.status_code == 200
    assert client.post(f"/plugins/vfx_files/uploads/{upload_id}/finalize", json={}, headers=headers).status_code == 202
    _run_worker(tmp_path, state_path)
    source = client.get(route).json["revisions"][0]["files"][0]
    assert source["state"] == "completed"
    with app.app_context(), zou_db_factory() as session:
        source_row = session.get(WorkingRevisionFile, UUID(source["id"]))
        revision_row = session.get(WorkingRevision, source_row.revision_id)
        task_row = session.get(Task, task_id)
        assert source_row.state == "completed" and source_row.provider_object_id
        assert source_row.provider_size == len(data) and source_row.provider_sha256 == digest
        assert revision_row.state == "completed" and revision_row.production_id == production_id
        assert revision_row.task_id == task_id and task_row.project_id == production_id
        assert task_row.task_type_id == task_type_id

    family_response = client.post(
        f"/plugins/vfx_files/productions/{production_id}/output-families",
        json={
            "family_key": family_key,
            "output_type_id": str(output_type.id),
            "task_type_id": str(task_type_id),
            "file_status_id": str(file_status.id),
            "name": "Comp",
            "representation": "main",
            "extension": "exr",
            "publish_folder_id": destination_id,
        },
        headers=headers,
    )
    assert family_response.status_code == 201, family_response.get_data(as_text=True)
    family_id = family_response.json["id"]
    activated = client.post(
        f"/plugins/vfx_files/productions/{production_id}/output-families/{family_id}/activate",
        json={},
        headers=headers,
    )
    assert activated.status_code == 200, activated.get_data(as_text=True)
    publication = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/publishes",
        json={"output_family_id": family_id, "source_revision_file_id": source["id"]},
        headers={**headers, "Idempotency-Key": f"publish-request:{uuid4()}"},
    )
    assert publication.status_code == 202, publication.get_data(as_text=True)
    publish_id = UUID(publication.json["id"])
    _run_worker(tmp_path, state_path)

    with app.app_context(), zou_db_factory() as session:
        publish = session.get(Publish, publish_id)
        pointer = session.get(PublishPointer, (task_id, UUID(family_id)))
        native = session.get(OutputFile, publish.native_output_file_id)
        native_source = session.get(WorkingFile, native.source_file_id)
        assert publish.state == "completed"
        assert publish.version == 1 and publish.qc_state == "passed"
        assert publish.manifest_sha256 and publish.native_revision == native.revision
        assert pointer.latest_published_id == publish.id and pointer.latest_approved_id is None
        assert native.path == f"https://drive.google.com/file/d/{publish.provider_artifact_id}/view"
        assert native_source.path.endswith(f"/{source['provider_object_id']}/view")
        assert native.data["vfx_files_publish_id"] == str(publish_id)
        assert session.scalar(select(PublishCheckpoint).where(
            PublishCheckpoint.publish_id == publish_id,
            PublishCheckpoint.checkpoint == "completed",
        )) is not None
        assert session.scalar(select(Job).where(
            Job.idempotency_key == f"publish-artifact:{publish_id}", Job.state == "completed",
        )) is not None
        source_row = session.get(WorkingRevisionFile, UUID(source["id"]))
        task = session.get(Task, task_id)
        task_type = session.get(TaskType, task_type_id)
        assert source_row is not None and task is not None and task_type is not None
        manifest = json.loads(publish.manifest_bytes)
        assert manifest["source_revision_file_id"] == str(source_row.id)
        assert manifest["artifact"]["provider_object_id"] == publish.provider_artifact_id

    stored = PersistentFakeStorage(state_path, root_id="synthetic-root-id")
    assert stored.verify(publish.provider_artifact_id, len(data), digest)
    assert stored.get(publish.provider_artifact_id).parent_id == destination_id

    reviewer = Person(
        first_name="Synthetic",
        last_name=f"Reviewer{uuid4().hex[:8]}",
        email=f"publish-reviewer-{uuid4().hex}@example.invalid",
        role="user",
    )
    from zou.app.services.persons_service import create_access_token_for_raw_person
    with app.app_context(), zou_db_factory.begin() as session:
        session.add(reviewer)
        session.flush()
        reviewer_id = reviewer.id
        session.add(ProjectPersonLink(project_id=production_id, person_id=reviewer_id, role="supervisor"))
        session.add(TaskPersonLink(task_id=task_id, person_id=reviewer_id))
    with zou_db_factory() as session:
        assert session.get(Person, reviewer_id) is not None
        assert session.scalar(select(TaskPersonLink).where(TaskPersonLink.task_id == task_id, TaskPersonLink.person_id == reviewer_id)) is not None
        assert session.scalar(select(ProjectPersonLink).where(ProjectPersonLink.project_id == production_id, ProjectPersonLink.person_id == reviewer_id)) is not None
    reviewer_client = app.test_client()
    with app.app_context():
        reviewer_token = create_access_token_for_raw_person(db_person := Person.query.filter_by(id=reviewer_id).one())
        reviewer_client.set_cookie(app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), reviewer_token)
    reviewer_headers = _headers(reviewer_client)
    assert reviewer_id != _actor_id

    self_approval = client.post(
        f"/plugins/vfx_files/publishes/{publish_id}/approvals",
        json={"policy_id": "synthetic-supervisor-approval-v1", "policy_sha256": hashlib.sha256(b"synthetic-policy-v1").hexdigest()},
        headers={**headers, "Idempotency-Key": f"self-approve:{uuid4()}"},
    )
    assert self_approval.status_code == 403

    approved = reviewer_client.post(
        f"/plugins/vfx_files/publishes/{publish_id}/approvals",
        json={"policy_id": "synthetic-supervisor-approval-v1", "policy_sha256": hashlib.sha256(b"synthetic-policy-v1").hexdigest()},
        headers={**reviewer_headers, "Idempotency-Key": f"approve:{uuid4()}"},
    )
    assert approved.status_code == 201, approved.get_data(as_text=True)
    assert approved.json["latest_approved_id"] == str(publish_id)
    handoff = client.post(
        f"/plugins/vfx_files/publishes/{publish_id}/handoffs",
        json={"recipient_id": str(reviewer_id)},
        headers={**headers, "Idempotency-Key": f"handoff:{uuid4()}"},
    )
    assert handoff.status_code == 201, handoff.get_data(as_text=True)
    response = reviewer_client.post(
        f"/plugins/vfx_files/handoffs/{handoff.json['id']}/respond",
        json={"target": "accepted", "feedback": ""},
        headers={**reviewer_headers, "Idempotency-Key": f"handoff-response:{uuid4()}"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.json["state"] == "accepted"
