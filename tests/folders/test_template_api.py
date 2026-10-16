import pytest

pytest.importorskip("sqlalchemy")


def test_kitsusync_owned_production_rejects_legacy_files_configuration_mutations(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, monkeypatch,
):
    from sqlalchemy import select
    from plugin.models import Job, SharedTemplateConfiguration

    client = authenticated_plugin_client
    production_id, _entity_type, _task_type, _entity, task_id, _connection_id, _actor_id = synthetic_upload_task
    app = client.vfx_app
    with app.app_context():
        class NoDriveCalls:
            calls = []
        adapter = NoDriveCalls()
        monkeypatch.setitem(app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: adapter)

    headers = {
        "Origin": "https://kitsu.test",
        "X-VFX-Files-CSRF": client.get("/plugins/vfx_files/csrf-token").json["csrf_token"],
        "Idempotency-Key": "legacy-route-mutation-test",
    }
    base = f"/plugins/vfx_files/productions/{production_id}"

    assert client.get(f"{base}/shared-template/candidates", query_string={
        "root_url": "https://drive.google.com/drive/folders/synthetic-root-id",
    }).status_code == 409
    assert client.get(f"{base}/shared-template").status_code == 409
    assert client.post(f"{base}/shared-template", json={
        "root_url": "https://drive.google.com/drive/folders/synthetic-root-id",
        "template_folder_id": "template-001", "role_mappings": {"shot": "shot", "task": "task"},
    }, headers=headers).status_code == 409
    assert client.post(f"{base}/shared-template/activate", json={
        "expected_version": 1, "enabled": True,
    }, headers=headers).status_code == 409
    assert client.post(f"{base}/tasks/{task_id}/shared-folders", json={}, headers=headers).status_code == 409
    assert adapter.calls == []

    with zou_db_factory() as session:
        assert session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == production_id,
        )) is None
        jobs = session.scalars(select(Job).where(Job.job_type == "provision_task_folders")).all()
        assert not any(job.payload.get("production_id") == str(production_id) for job in jobs)


def test_kitsusync_managed_task_folders_are_read_from_verified_selected_bindings(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, tmp_path, monkeypatch,
):
    from plugin.models import FolderBinding
    from plugin.storage.fake import PersistentFakeStorage

    client = authenticated_plugin_client
    production_id, _entity_type, _task_type, _entity, task_id, connection_id, _actor_id = synthetic_upload_task
    app = client.vfx_app
    adapter = PersistentFakeStorage(tmp_path / "storage.json", root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    adapter.create_folder("synthetic-root-id", "task-folder", {"display_name": "main"})
    with app.app_context(), zou_db_factory.begin() as session:
        session.add(FolderBinding(
            storage_connection_id=connection_id,
            allowed_root_id="synthetic-root-id",
            canonical_scope_key=f"production:{production_id}:task:{task_id}",
            parent_provider_id="synthetic-root-id",
            provider_folder_id="task-folder",
            state="ready",
        ))
        session.add(FolderBinding(
            storage_connection_id=connection_id,
            allowed_root_id="different-root",
            canonical_scope_key=f"production:{production_id}:task:{task_id}",
            parent_provider_id="outside-root",
            provider_folder_id="unrelated-folder",
            state="ready",
        ))
    monkeypatch.setitem(app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: adapter)

    response = client.get(f"/plugins/vfx_files/productions/{production_id}/tasks/{task_id}/shared-folders")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.json["managed_by_kitsusync"] is True
    assert response.json["provider_mode"] == "synthetic"
    assert response.json["folders"] == [{
        "scope_key": f"production:{production_id}:task:{task_id}",
        "name": "main",
        "state": "ready",
        "provider_folder_id": None,
        "url": None,
    }]


def test_queued_legacy_folder_job_is_fenced_before_storage_access(
    synthetic_upload_task, zou_db_factory,
):
    from types import SimpleNamespace
    from plugin.folders.worker import make_task_folder_handler

    production_id, _entity_type, _task_type, _entity, task_id, _connection_id, _actor_id = synthetic_upload_task
    class NoDriveCalls:
        calls = []

    adapter = NoDriveCalls()
    handler = make_task_folder_handler(zou_db_factory, adapter)
    result = handler(SimpleNamespace(payload={
        "production_id": str(production_id), "task_id": str(task_id),
        "configuration_id": "00000000-0000-0000-0000-000000000001", "configuration_version": 1,
    }))
    assert result.state == "blocked_authorization"
    assert result.error_code == "kitsusync_configuration_authority"
    assert adapter.calls == []
