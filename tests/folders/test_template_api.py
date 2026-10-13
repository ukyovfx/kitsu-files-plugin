import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("sqlalchemy")


def test_template_discovery_config_and_activation_are_production_scoped(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, tmp_path, monkeypatch
):
    from sqlalchemy import select
    from plugin.models import FolderBinding, Job, KitsuSyncDesiredConfiguration, KitsuSyncEventOutbox, SharedTemplateConfiguration
    from plugin.storage.fake import PersistentFakeStorage
    from zou.app.models.project import ProjectPersonLink

    client = authenticated_plugin_client
    production_id, _entity_type, _task_type, _entity, _task_id, connection_id, actor_id = synthetic_upload_task
    app = client.vfx_app
    fake_state = tmp_path / "fake-storage.json"
    adapter = PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="fake-shared-drive")
    adapter.create_folder("synthetic-root-id", "template-001", {"display_name": "Shared Layout"})
    adapter.create_folder("template-001", "shot-role-01", {"display_name": "Shot"})
    adapter.create_folder("shot-role-01", "task-role-01", {"display_name": "Task"})
    monkeypatch.setitem(app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: adapter)

    with app.app_context():
        membership = ProjectPersonLink.query.filter_by(project_id=production_id, person_id=actor_id).one()
        membership.role = "manager"
        app.extensions["sqlalchemy"].session.commit()

    url = "https://drive.google.com/drive/folders/synthetic-root-id"
    candidates = client.get(
        f"/plugins/vfx_files/productions/{production_id}/shared-template/candidates",
        query_string={"root_url": url},
    )
    assert candidates.status_code == 200, candidates.get_data(as_text=True)
    assert candidates.json["allowed_root_id"] == "synthetic-root-id"
    assert candidates.json["drive_id"] == "fake-shared-drive"
    assert candidates.json["templates"][0]["children"][0]["folder_id"] == "shot-role-01"

    headers = {
        "Origin": "https://kitsu.test",
        "X-VFX-Files-CSRF": client.get("/plugins/vfx_files/csrf-token").json["csrf_token"],
    }
    route = f"/plugins/vfx_files/productions/{production_id}/shared-template"
    wrong_root = client.post(
        route,
        json={
            "root_url": "https://drive.google.com/drive/folders/other-root-01",
            "template_folder_id": "template-001",
            "role_mappings": {"shot": "shot-role-01", "task": "task-role-01"},
        },
        headers={**headers, "Idempotency-Key": "template-test-wrong-root"},
    )
    assert wrong_root.status_code == 403

    saved = client.post(
        route,
        json={
            "root_url": url,
            "template_folder_id": "template-001",
            "role_mappings": {"shot": "shot-role-01", "task": "task-role-01"},
            "expected_version": None,
        },
        headers={**headers, "Idempotency-Key": "template-test-create"},
    )
    assert saved.status_code == 201, saved.get_data(as_text=True)
    assert saved.json == {"id": saved.json["id"], "version": 1, "enabled": False, "template_folder_id": "template-001"}
    before_update_calls = list(adapter.calls)

    activate = client.post(
        f"{route}/activate",
        json={"expected_version": 1, "enabled": True},
        headers=headers,
    )
    assert activate.status_code == 200, activate.get_data(as_text=True)
    assert activate.json["enabled"] is True
    with zou_db_factory.begin() as session:
        desired = session.get(KitsuSyncDesiredConfiguration, production_id)
        body = dict(desired.desired_config)
        body["activation"] = {"enabled": True, "activated_at": "2026-10-13T00:00:00Z", "notification_epoch": 1}
        desired.notification_epoch = 1
        desired.desired_config = body

    provision_route = f"/plugins/vfx_files/productions/{production_id}/tasks/{_task_id}/shared-folders"
    queued = client.post(provision_route, json={}, headers=headers)
    assert queued.status_code == 202, queued.get_data(as_text=True)
    queued_again = client.post(provision_route, json={}, headers=headers)
    assert queued_again.status_code == 202
    assert queued_again.json["job_id"] == queued.json["job_id"]
    from plugin.worker_runtime import _authorize_job
    with zou_db_factory() as session:
        queued_job = session.get(Job, queued.json["job_id"])
        assert _authorize_job(queued_job.payload)

    monkeypatch.setenv("VFX_FILES_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_WORKER_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_STORAGE_MODE", "fake")
    monkeypatch.setenv("VFX_FILES_STAGING_ROOT", str(tmp_path))
    monkeypatch.setenv("VFX_FILES_FAKE_STORAGE_ROOT", str(fake_state))
    monkeypatch.setenv("VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id")
    monkeypatch.setenv("VFX_FILES_FAKE_DRIVE_ID", "fake-shared-drive")
    worker_result = subprocess.run(
        [sys.executable, "-m", "plugin.worker_runtime", "--once"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=30,
    )
    assert worker_result.returncode == 0, worker_result.stderr
    assert json.loads(worker_result.stdout.splitlines()[-1])["result"] == "completed"
    worker_storage = PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="fake-shared-drive")
    monkeypatch.setitem(app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: worker_storage)
    refreshed = client.get(provision_route)
    assert refreshed.status_code == 200
    assert refreshed.json["configured"] is True
    assert len(refreshed.json["folders"]) == 2
    assert all(folder["state"] == "ready" and folder["url"].startswith("https://drive.google.com/drive/folders/") for folder in refreshed.json["folders"])

    changed = client.post(
        route,
        json={
            "root_url": url,
            "template_folder_id": "template-001",
            "role_mappings": {"shot": "shot-role-01", "task": "task-role-01", "working": "task-role-01"},
            "expected_version": 1,
        },
        headers={**headers, "Idempotency-Key": "template-test-update"},
    )
    assert changed.status_code == 201
    assert changed.json == {"id": saved.json["id"], "version": 2, "enabled": False, "template_folder_id": "template-001"}
    stale = client.post(
        route,
        json={
            "root_url": url,
            "template_folder_id": "template-001",
            "role_mappings": {"shot": "shot-role-01", "task": "task-role-01"},
            "expected_version": 1,
        },
        headers={**headers, "Idempotency-Key": "template-test-stale-update"},
    )
    assert stale.status_code == 409
    assert adapter.calls == before_update_calls

    with zou_db_factory() as session:
        from plugin.models import Job

        job = session.get(Job, queued.json["job_id"])
        assert job is not None
        assert job.job_type == "provision_task_folders"
        assert job.state == "completed"
        assert job.payload["production_id"] == str(production_id)
        assert job.payload["task_id"] == str(_task_id)
        bindings = session.scalars(select(FolderBinding).where(FolderBinding.storage_connection_id == connection_id)).all()
        assert len(bindings) == 2
        assert all(binding.state == "ready" and binding.provider_folder_id for binding in bindings)
        events = session.scalars(select(KitsuSyncEventOutbox).where(
            KitsuSyncEventOutbox.production_id == production_id,
        )).all()
        assert len(events) == 1
        assert events[0].event_type == "folder.provisioning.completed"
        assert events[0].storage_connection_id == connection_id
        assert events[0].payload["activation_epoch"] == 1
        task_binding = next(binding for binding in bindings if binding.canonical_scope_key.endswith(":task:" + str(_task_id)))
        shot_binding = next(binding for binding in bindings if binding.canonical_scope_key.endswith(":shot:" + str(_entity)))
        assert worker_storage.get(task_binding.provider_folder_id).parent_id == shot_binding.provider_folder_id
        configs = session.scalars(
            select(SharedTemplateConfiguration).where(SharedTemplateConfiguration.production_id == production_id)
        ).all()
        assert len(configs) == 1
        assert configs[0].storage_connection_id == connection_id
        assert configs[0].allowed_root_id == "synthetic-root-id"
        assert configs[0].version == 2
        assert configs[0].enabled is False
        assert configs[0].template_tree["children"][0]["children"][0]["folder_id"] == "task-role-01"
