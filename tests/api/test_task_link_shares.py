from uuid import uuid4

from plugin.storage.base import StorageObject
from plugin.storage.fake import FakeStorage


def _headers(client):
    csrf = client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]
    return {"Origin": "https://kitsu.test", "X-VFX-Files-CSRF": csrf}


def _user_client(app, person):
    from zou.app.services.persons_service import create_access_token_for_raw_person

    token = create_access_token_for_raw_person(person)
    client = app.test_client()
    client.set_cookie(app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), token)
    return client


def test_task_link_share_api_uses_current_tasks_fake_storage_and_stays_default_off(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, monkeypatch,
):
    from sqlalchemy import select
    from zou.app.models.entity import Entity
    from zou.app.models.person import Person
    from zou.app.models.project import Project, ProjectPersonLink
    from zou.app.models.task import Task, TaskPersonLink
    from zou.app.models.task_type import TaskType
    from plugin.models import (
        DriveCredential, KitsuSyncEventOutbox, LinkShare, LinkShareTarget,
        ProductionDriveAuthority, StorageConnection,
    )

    app = authenticated_plugin_client.vfx_app
    production_id, _entity_type_id, task_type_id, _source_entity_id, source_task_id, connection_id, _admin_id = synthetic_upload_task
    with app.app_context(), zou_db_factory.begin() as session:
        # Use an ordinary Task-assigned artist so recipient authorization is
        # exercised instead of the manager bypass used by other route tests.
        actor = Person(first_name=f"Share{uuid4().hex[:8]}", last_name="Artist", role="user", active=True)
        session.add(actor)
        session.flush()
        actor_id = actor.id
        membership = ProjectPersonLink(project_id=production_id, person_id=actor_id, role="user")
        session.add(membership)
        session.add(TaskPersonLink(task_id=source_task_id, person_id=actor_id))

        project = session.get(Project, production_id)
        source_type = session.get(TaskType, task_type_id)
        project.task_types.append(source_type)
        recipient_entity = Entity(
            name=f"SHOT{uuid4().hex[:8]}", project_id=production_id,
            entity_type_id=_entity_type_id,
        )
        session.add(recipient_entity)
        session.flush()
        recipient_task = Task(
            name="main", project_id=production_id, task_type_id=task_type_id,
            entity_id=recipient_entity.id,
        )
        session.add(recipient_task)
        session.flush()
        recipient_task_id = recipient_task.id
        session.add_all([
            DriveCredential(
                connection_id=connection_id, production_id=production_id,
                account_mode="shared_drive", account_subject="synthetic-drive-user",
                owner_subject=None, drive_id="synthetic-drive-id", key_id="test-key",
                nonce=b"n" * 12, ciphertext=b"synthetic-encrypted-placeholder", state="active",
            ),
            ProductionDriveAuthority(
                production_id=production_id, owner="plugin", state="configured",
                config_revision=1, activation_epoch=7, connection_id=connection_id,
                root_id="synthetic-root-id", desired_config={"activation": {"enabled": True}},
                activation_enabled=True, activation_transition_pending=False, updated_by=actor_id,
            ),
        ])
        session.flush()

    client = _user_client(app, actor)
    fake = FakeStorage(root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    object_id = "synthetic-drive-file-01"
    fake.objects[object_id] = StorageObject(
        object_id=object_id, parent_id="synthetic-root-id", size=42,
        sha256=None, drive_id="synthetic-drive-id", mime_type="image/x-exr",
        name="plate.exr", modified_time="2026-10-17T01:02:03Z",
    )
    forbidden_operations = []
    for method_name in ("copy_object", "move_object", "update_permissions", "set_permissions"):
        setattr(fake, method_name, lambda *args, _name=method_name, **kwargs: forbidden_operations.append(_name))
    monkeypatch.setitem(
        app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: fake,
    )
    monkeypatch.setitem(app.config, "VFX_FILES_KITSUSYNC_EVENT_CONTRACT_VERSION", "1.0")

    source_read = client.get(f"/plugins/vfx_files/tasks/{source_task_id}/link-shares")
    assert source_read.status_code == 200, source_read.get_data(as_text=True)
    assert source_read.json == {"task_id": str(source_task_id), "shares": []}

    candidates_url = f"/plugins/vfx_files/tasks/{source_task_id}/share-candidates"
    candidates_before_assignment = client.get(candidates_url)
    assert candidates_before_assignment.status_code == 200, candidates_before_assignment.get_data(as_text=True)
    assert candidates_before_assignment.json["recipients"] == []

    mutation_headers = {
        **_headers(client), "Content-Type": "application/json",
        "Idempotency-Key": f"share-api-test:{uuid4()}",
    }
    unassigned = client.post(
        f"/plugins/vfx_files/tasks/{source_task_id}/link-shares",
        json={"object_id": object_id, "recipient_task_ids": [str(recipient_task_id)]},
        headers=mutation_headers,
    )
    assert unassigned.status_code == 403, unassigned.get_data(as_text=True)

    with app.app_context(), zou_db_factory.begin() as session:
        session.add(TaskPersonLink(task_id=recipient_task_id, person_id=actor_id))
    candidates = client.get(candidates_url)
    assert candidates.status_code == 200, candidates.get_data(as_text=True)
    assert [row["task_id"] for row in candidates.json["recipients"]] == [str(recipient_task_id)]

    # A valid Task ID from another Production is still rejected.
    with app.app_context(), zou_db_factory.begin() as session:
        foreign_project = Project(name=f"Foreign {uuid4()}")
        session.add(foreign_project)
        session.flush()
        foreign_entity = Entity(
            name=f"SH{uuid4().hex[:8]}", project_id=foreign_project.id,
            entity_type_id=_entity_type_id,
        )
        session.add(foreign_entity)
        session.flush()
        foreign_task = Task(
            name="main", project_id=foreign_project.id, task_type_id=task_type_id,
            entity_id=foreign_entity.id,
        )
        session.add(foreign_task)
        session.flush()
        foreign_task_id = foreign_task.id
    cross_production = client.post(
        f"/plugins/vfx_files/tasks/{source_task_id}/link-shares",
        json={"object_id": object_id, "recipient_task_ids": [str(foreign_task_id)]},
        headers={**_headers(client), "Content-Type": "application/json", "Idempotency-Key": f"share-api-cross:{uuid4()}"},
    )
    assert cross_production.status_code == 403, cross_production.get_data(as_text=True)

    created = client.post(
        f"/plugins/vfx_files/tasks/{source_task_id}/link-shares",
        json={"object_id": object_id, "recipient_task_ids": [str(recipient_task_id)]},
        headers={**_headers(client), "Content-Type": "application/json", "Idempotency-Key": f"share-api-success:{uuid4()}"},
    )
    assert created.status_code == 202, created.get_data(as_text=True)
    assert len(created.json["targets"]) == 1
    assert created.json["targets"][0]["recipient_task_id"] == str(recipient_task_id)
    assert created.json["targets"][0]["notification_status"] == "blocked_receiver_version"

    source_history = client.get(f"/plugins/vfx_files/tasks/{source_task_id}/link-shares")
    recipient_history = client.get(f"/plugins/vfx_files/tasks/{recipient_task_id}/received-link-shares")
    assert source_history.status_code == 200, source_history.get_data(as_text=True)
    assert recipient_history.status_code == 200, recipient_history.get_data(as_text=True)
    source_item = source_history.json["shares"][0]
    received_item = recipient_history.json["received_files"][0]
    assert source_item["object"]["object_id"] == object_id
    assert source_item["object"]["url"] == f"https://drive.google.com/file/d/{object_id}/view"
    assert received_item["object"]["object_id"] == source_item["object"]["object_id"]
    assert received_item["object"]["url"] == source_item["object"]["url"]
    assert received_item["notification_status"] == "blocked_receiver_version"

    with app.app_context(), zou_db_factory() as session:
        targets = session.scalars(select(LinkShareTarget).where(LinkShareTarget.recipient_task_id == recipient_task_id)).all()
        shares = session.scalars(select(LinkShare).where(LinkShare.source_task_id == source_task_id)).all()
        outbox = session.scalars(select(KitsuSyncEventOutbox).where(
            KitsuSyncEventOutbox.production_id == production_id,
            KitsuSyncEventOutbox.event_type == "file_share.created",
        )).all()
        assert len(targets) == len(shares) == len(outbox) == 1
        assert outbox[0].payload["contract_version"] == "1.1"
        assert outbox[0].state == "queued"
    assert fake.calls == []
    assert forbidden_operations == []
    assert set(fake.objects) == {"synthetic-root-id", object_id}
