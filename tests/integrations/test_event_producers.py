from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from zou.app.models.asset_instance import AssetInstance  # noqa: F401 - resolves Entity's forward relationship
from zou.app.models.person import Person  # noqa: F401 - resolves Task's forward relationship

from plugin.integrations import event_producers
from plugin.integrations.v1 import validate_event_batch


def _scope(*, enabled=True, selected=True):
    production_id, connection_id = uuid4(), uuid4()
    desired = SimpleNamespace(
        notification_epoch=7,
        desired_config={
            "connection_id": str(connection_id),
            "activation": {"enabled": enabled, "notification_epoch": 7},
            "drive": {
                "account_mode": "my_drive", "owner_subject": "synthetic-owner",
                "root_id": "fake-root", "root_state": "verified",
            },
        },
    )
    connection = SimpleNamespace(
        id=connection_id if selected else uuid4(), production_id=production_id,
        enabled=True, provider="google_drive", allowed_root_id="fake-root",
        account_mode="my_drive", owner_subject="synthetic-owner", drive_id=None,
    )

    class Session:
        def get(self, model, identity):
            return desired if model.__name__ == "KitsuSyncDesiredConfiguration" else None

        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            return desired if model.__name__ == "KitsuSyncDesiredConfiguration" else connection

    return production_id, connection_id, Session()


def test_event_append_is_suppressed_without_activation_or_selected_connection(monkeypatch):
    saved = []
    monkeypatch.setattr(event_producers, "append_event_batch", lambda *_: saved.append(True))
    event = {
        "contract_version": "1.0", "producer_id": "vfx-files-plugin",
        "event_id": str(uuid4()), "event_type": "folder.provisioning.completed",
        "occurred_at": "2026-10-13T00:00:00Z", "production_id": str(uuid4()),
        "activation_epoch": 1,
        "folder": {"object_type": "task", "object_id": str(uuid4()), "drive_file_id": "id", "binding_state": "ready"},
    }

    production_id, connection_id, session = _scope(enabled=False)
    event["production_id"] = str(production_id)
    assert not event_producers.append_eligible_event(session, event, selected_connection_id=connection_id)
    assert saved == []

    production_id, connection_id, session = _scope()
    assert not event_producers.append_new_task_folder_event(
        session, production_id=production_id, task_id=uuid4(),
        selected_connection_id=connection_id, expected_activation_epoch=None,
    )
    assert saved == []

    production_id, connection_id, session = _scope()
    cross_production_id = uuid4()
    event["production_id"] = str(cross_production_id)
    assert not event_producers.append_eligible_event(
        session, event, selected_connection_id=connection_id,
    )
    assert saved == []

    production_id, connection_id, session = _scope(selected=False)
    event["production_id"] = str(production_id)
    assert not event_producers.append_eligible_event(session, event, selected_connection_id=connection_id)
    assert saved == []


def test_output_package_event_uses_selected_connection_epoch_and_contract_shape(monkeypatch):
    production_id, connection_id, scope_session = _scope()
    task_id, entity_id, task_type_id, package_id, actor_id = (uuid4() for _ in range(5))
    package = SimpleNamespace(
        id=package_id, production_id=production_id, task_id=task_id, entity_id=entity_id,
        output_type_id=uuid4(), package_type="tracking", version=3,
        manifest_sha256="a" * 64, created_by=actor_id,
        event_activation_epoch=7,
        created_at=datetime(2026, 10, 13, tzinfo=timezone.utc), completed_at=None,
    )
    file_row = SimpleNamespace(
        ordinal=0, provider_object_id=None, source_provider_object_id="fake-object-01",
        sha256="b" * 64, byte_count=99,
    )
    task = SimpleNamespace(id=task_id, project_id=production_id, task_type_id=task_type_id)
    entity = SimpleNamespace(id=entity_id, project_id=production_id)
    task_type = SimpleNamespace(name="Synthetic Task Type")
    person = SimpleNamespace(id=actor_id)
    captured = []

    class Session:
        def get(self, model, identity):
            if model.__name__ == "KitsuSyncDesiredConfiguration":
                return scope_session.get(model, identity)
            return {
                "Task": task, "Entity": entity, "TaskType": task_type, "Person": person,
            }.get(model.__name__)

        def scalar(self, statement):
            return scope_session.scalar(statement)

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [file_row])

    def capture(_repository, batch, **_kwargs):
        captured.extend(validate_event_batch(batch))

    monkeypatch.setattr(event_producers, "append_event_batch", capture)
    assert event_producers.append_output_package_event(
        Session(), package, event_type="output_package.created",
        selected_connection_id=connection_id, expected_activation_epoch=7,
    )
    assert len(captured) == 1
    event = captured[0]
    assert event["activation_epoch"] == 7
    assert event["source"] == {
        "task_id": str(task_id), "task_type_id": str(task_type_id),
        "task_type_name": "Synthetic Task Type",
    }
    assert event["output_package"]["objects"] == []


def test_ready_package_event_only_references_verified_package_objects(monkeypatch):

    production_id, connection_id, scope_session = _scope()
    task_id, entity_id, task_type_id, package_id, actor_id = (uuid4() for _ in range(5))
    package = SimpleNamespace(
        id=package_id, production_id=production_id, task_id=task_id, entity_id=entity_id,
        package_type="tracking", version=3, manifest_sha256="a" * 64, created_by=actor_id,
        event_activation_epoch=7, completed_at=datetime.now(timezone.utc),
    )
    file_row = SimpleNamespace(
        ordinal=0, provider_object_id="verified-package-object", source_provider_object_id="source-object",
        sha256="b" * 64, byte_count=99,
    )
    task = SimpleNamespace(id=task_id, project_id=production_id, task_type_id=task_type_id)
    entity = SimpleNamespace(id=entity_id, project_id=production_id)
    task_type = SimpleNamespace(name="Synthetic Task Type")
    person = SimpleNamespace(id=actor_id)
    captured = []

    class Session:
        def get(self, model, identity):
            if model.__name__ == "KitsuSyncDesiredConfiguration":
                return scope_session.get(model, identity)
            return {"Task": task, "Entity": entity, "TaskType": task_type, "Person": person}.get(model.__name__)

        def scalar(self, statement):
            return scope_session.scalar(statement)

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [file_row])

    monkeypatch.setattr(
        event_producers, "append_event_batch",
        lambda _repo, batch, **_kwargs: captured.extend(validate_event_batch(batch)),
    )
    assert event_producers.append_output_package_event(
        Session(), package, event_type="output_package.ready",
        selected_connection_id=connection_id, expected_activation_epoch=7,
    )
    assert captured[0]["output_package"]["objects"] == [{
        "drive_file_id": "verified-package-object", "classification": "tracking",
        "size_bytes": 99, "sha256": "b" * 64,
    }]


def test_package_event_is_suppressed_when_selected_root_differs(monkeypatch):
    production_id, connection_id, scope_session = _scope()
    package = SimpleNamespace(
        id=uuid4(), production_id=production_id, task_id=uuid4(), entity_id=uuid4(),
        package_type="tracking", version=1, manifest_sha256="a" * 64,
        created_by=uuid4(), created_at=datetime.now(timezone.utc), event_activation_epoch=7,
    )
    emitted = []
    monkeypatch.setattr(event_producers, "append_event_batch", lambda *_: emitted.append(True))

    class Session:
        def get(self, model, identity):
            return scope_session.get(model, identity)

        def scalar(self, statement):
            return scope_session.scalar(statement)

    assert not event_producers.append_output_package_event(
        Session(), package, event_type="output_package.created",
        selected_connection_id=uuid4(),
    )
    assert emitted == []


def test_old_package_epoch_is_not_backfilled_after_reactivation(monkeypatch):
    production_id, connection_id, scope_session = _scope()
    package = SimpleNamespace(
        id=uuid4(), production_id=production_id, task_id=uuid4(), entity_id=uuid4(),
        package_type="tracking", version=1, manifest_sha256="a" * 64,
        created_by=uuid4(), created_at=datetime.now(timezone.utc), event_activation_epoch=6,
    )
    emitted = []
    monkeypatch.setattr(event_producers, "append_event_batch", lambda *_: emitted.append(True))

    assert not event_producers.append_output_package_event(
        scope_session, package, event_type="output_package.created",
        selected_connection_id=connection_id, expected_activation_epoch=6,
    )
    assert emitted == []
