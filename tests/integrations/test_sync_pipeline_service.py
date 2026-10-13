import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType
from uuid import uuid4

import pytest


@pytest.fixture
def sync_db(monkeypatch):
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Uuid, create_engine
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

    class Base(DeclarativeBase):
        pass

    class Desired(Base):
        __tablename__ = "desired"
        production_id: Mapped[object] = mapped_column(Uuid, primary_key=True)
        config_revision: Mapped[int] = mapped_column(Integer)
        notification_epoch: Mapped[int] = mapped_column(Integer)
        desired_config: Mapped[dict] = mapped_column(JSON)
        updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    class Idempotency(Base):
        __tablename__ = "idempotency"
        id: Mapped[object] = mapped_column(Uuid, primary_key=True, default=uuid4)
        production_id: Mapped[object] = mapped_column(Uuid)
        actor_id: Mapped[object] = mapped_column(Uuid)
        idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
        operation: Mapped[str] = mapped_column(String(128))
        request_sha256: Mapped[str] = mapped_column(String(64))
        response_status: Mapped[int] = mapped_column(Integer)
        response_etag: Mapped[str | None] = mapped_column(String(32), nullable=True)
        response_body: Mapped[dict] = mapped_column(JSON)

    class Snapshot(Base):
        __tablename__ = "snapshot"
        id: Mapped[object] = mapped_column(Uuid, primary_key=True)
        production_id: Mapped[object] = mapped_column(Uuid)
        config_revision: Mapped[int] = mapped_column(Integer)
        source_etag: Mapped[str] = mapped_column(String(200))
        page_count: Mapped[int] = mapped_column(Integer)
        object_count: Mapped[int] = mapped_column(Integer)
        hierarchy_sha256: Mapped[str] = mapped_column(String(64))
        state: Mapped[str] = mapped_column(String(24))
        expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    class Page(Base):
        __tablename__ = "page"
        snapshot_id: Mapped[object] = mapped_column(Uuid, primary_key=True)
        page_index: Mapped[int] = mapped_column(Integer, primary_key=True)
        page_sha256: Mapped[str] = mapped_column(String(64))
        objects: Mapped[list] = mapped_column(JSON)

    class Connection(Base):
        __tablename__ = "connection"
        id: Mapped[object] = mapped_column(Uuid, primary_key=True)
        production_id: Mapped[object] = mapped_column(Uuid)
        provider: Mapped[str] = mapped_column(String(32), default="google_drive")
        account_mode: Mapped[str] = mapped_column(String(24))
        owner_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
        drive_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
        allowed_root_id: Mapped[str] = mapped_column(String(255))
        credential_ref: Mapped[str] = mapped_column(String(255), default="fake")
        enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    class FolderBinding(Base):
        __tablename__ = "binding"
        id: Mapped[object] = mapped_column(Uuid, primary_key=True, default=uuid4)
        storage_connection_id: Mapped[object] = mapped_column(Uuid)
        allowed_root_id: Mapped[str] = mapped_column(String(255))
        canonical_scope_key: Mapped[str] = mapped_column(String(512))
        provider_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
        state: Mapped[str] = mapped_column(String(32))

    class Preview(Base):
        __tablename__ = "preview"
        id: Mapped[object] = mapped_column(Uuid, primary_key=True)
        production_id: Mapped[object] = mapped_column(Uuid)
        snapshot_id: Mapped[object] = mapped_column(Uuid)
        template_configuration_id: Mapped[object] = mapped_column(Uuid)
        template_version: Mapped[int] = mapped_column(Integer)
        config_revision: Mapped[int] = mapped_column(Integer)
        activation_epoch: Mapped[int] = mapped_column(Integer)
        storage_connection_id: Mapped[object] = mapped_column(Uuid)
        root_id: Mapped[str] = mapped_column(String(255))
        summary: Mapped[dict] = mapped_column(JSON)
        changes: Mapped[list] = mapped_column(JSON)
        state: Mapped[str] = mapped_column(String(24))
        expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
        created_by: Mapped[object] = mapped_column(Uuid)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    class Template(Base):
        __tablename__ = "template"
        id: Mapped[object] = mapped_column(Uuid, primary_key=True)
        production_id: Mapped[object] = mapped_column(Uuid)
        storage_connection_id: Mapped[object] = mapped_column(Uuid)
        allowed_root_id: Mapped[str] = mapped_column(String(255))
        role_mappings: Mapped[dict] = mapped_column(JSON)
        version: Mapped[int] = mapped_column(Integer)
        enabled: Mapped[bool] = mapped_column(Boolean)

    class Job(Base):
        __tablename__ = "job"
        id: Mapped[object] = mapped_column(Uuid, primary_key=True)
        job_type: Mapped[str] = mapped_column(String(64))
        idempotency_key: Mapped[str] = mapped_column(String(512), unique=True)
        payload: Mapped[dict] = mapped_column(JSON)
        state: Mapped[str] = mapped_column(String(32))
        attempts: Mapped[int] = mapped_column(Integer, default=0)
        max_attempts: Mapped[int] = mapped_column(Integer, default=5)
        available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
        lease_owner: Mapped[object | None] = mapped_column(Uuid, nullable=True)
        lease_generation: Mapped[int] = mapped_column(Integer, default=0)
        lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
        updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    module = ModuleType("plugin.models")
    for name, model in {
        "KitsuSyncDesiredConfiguration": Desired,
        "KitsuSyncManagementIdempotency": Idempotency,
        "KitsuSyncHierarchySnapshot": Snapshot,
        "KitsuSyncHierarchySnapshotPage": Page,
        "KitsuSyncSyncPreview": Preview,
        "StorageConnection": Connection,
        "FolderBinding": FolderBinding,
        "SharedTemplateConfiguration": Template,
        "Job": Job,
    }.items():
        setattr(module, name, model)
    monkeypatch.setitem(sys.modules, "plugin.models", module)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    return factory, Desired, Idempotency, Snapshot, Page, Connection, FolderBinding, Preview, Template, Job


PRODUCTION = "10000000-0000-4000-8000-000000000001"
ACTOR = "20000000-0000-4000-8000-000000000002"
SHOT = "40000000-0000-4000-8000-000000000001"
TASK = "50000000-0000-4000-8000-000000000002"
TASK_TYPE = "50000000-0000-4000-8000-000000000010"


def seed_sync_state(sync_db):
    import hashlib
    from uuid import UUID

    from plugin.integrations.v1 import canonical_json

    factory, Desired, _Idempotency, Snapshot, Page, Connection, _Binding, _Preview, Template, _Job = sync_db
    objects = [
        {"object_type": "production", "id": PRODUCTION, "parent_id": None, "name": "Synthetic", "included_in_sync": True},
        {"object_type": "shot", "id": SHOT, "parent_id": PRODUCTION, "name": "sc01_sh005", "included_in_sync": True},
        {"object_type": "task", "id": TASK, "parent_id": SHOT, "name": "Compositing", "task_type_id": TASK_TYPE, "included_in_sync": True},
        {"object_type": "task_type", "id": TASK_TYPE, "parent_id": None, "name": "Compositing", "included_in_sync": False},
    ]
    digest = hashlib.sha256(canonical_json(objects)).hexdigest()
    snapshot_id, connection_id, template_id = uuid4(), uuid4(), uuid4()
    config = {
        "contract_version": "1.0", "connection_id": str(connection_id),
        "drive": {"account_mode": "my_drive", "root_id": "fake-root", "root_state": "verified", "owner_subject": "synthetic-owner"},
        "template_selection": {"selections": [
            {"scope": "shot", "template_id": "vfx-files.shot.default", "revision": 1, "overrides": {}},
            {"scope": "task", "template_id": "vfx-files.task.default", "revision": 1, "overrides": {}},
        ]},
        "sync_policy": {"mode": "new_production", "scope": {"episodes": True, "sequences": True, "shots": True, "assets": True, "tasks": True}, "notify_new_deliveries": False},
        "activation": {"enabled": True, "activated_at": "2026-10-13T00:00:00Z", "notification_epoch": 3},
    }
    with factory.begin() as session:
        session.add(Desired(production_id=UUID(PRODUCTION), config_revision=4, notification_epoch=3, desired_config=config))
        session.add(Connection(id=connection_id, production_id=UUID(PRODUCTION), account_mode="my_drive", owner_subject="synthetic-owner", allowed_root_id="fake-root", enabled=True))
        session.add(Template(id=template_id, production_id=UUID(PRODUCTION), storage_connection_id=connection_id, allowed_root_id="fake-root", role_mappings={"shot": "shot-node", "task": "task-node"}, version=2, enabled=True))
        session.add(Snapshot(id=snapshot_id, production_id=UUID(PRODUCTION), config_revision=4, source_etag=digest, page_count=1, object_count=len(objects), hierarchy_sha256=digest, state="complete", expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        session.add(Page(snapshot_id=snapshot_id, page_index=0, page_sha256=digest, objects=objects))
    return {"snapshot_id": snapshot_id, "connection_id": connection_id, "template_id": template_id, "digest": digest, "objects": objects}


def test_snapshot_ids_are_immutable_but_equal_content_may_have_distinct_ids(sync_db):
    from datetime import datetime, timedelta, timezone
    from uuid import UUID, uuid4

    from plugin.integrations.snapshots import start_snapshot

    factory, Desired, _Idempotency, _Snapshot, _Page, _Connection, _Binding, _Preview, _Template, _Job = sync_db
    with factory.begin() as session:
        session.add(Desired(production_id=UUID(PRODUCTION), config_revision=4, notification_epoch=0, desired_config={}))
    args = {"production_id": UUID(PRODUCTION), "config_revision": 4, "source_etag": "a" * 64,
            "page_count": 1, "object_count": 1, "snapshot_sha256": "b" * 64,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5)}
    first, second = uuid4(), uuid4()
    with factory.begin() as session:
        one = start_snapshot(session, snapshot_id=first, **args)
        two = start_snapshot(session, snapshot_id=second, **args)
        assert one.id != two.id and one.hierarchy_sha256 == two.hierarchy_sha256
        with pytest.raises(Exception, match="already bound to different content"):
            start_snapshot(session, snapshot_id=first, **{**args, "snapshot_sha256": "c" * 64})


def test_sync_preview_apply_status_retry_and_idempotency(sync_db):
    from uuid import UUID

    from plugin.integrations.sync import (
        SyncOperationError,
        apply_sync_preview,
        create_sync_preview,
        enqueue_sync_job_command,
        get_sync_job,
    )

    factory, *_ = sync_db
    data = seed_sync_state(sync_db)
    preview_request = {"contract_version": "1.0", "config_revision": 4, "snapshot_id": str(data["snapshot_id"])}
    with factory() as session:
        preview, status, _etag = create_sync_preview(
            session, production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
            payload=preview_request, idempotency_key="sync-preview-key-0001",
        )
        assert status == 200 and preview["state"] == "preview_ready"
        assert preview["summary"]["create"] == 2
        assert [row["object_type"] for row in preview["changes"] if row["action"] == "create"] == ["shot", "task"]
        replay, _status, _etag = create_sync_preview(
            session, production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
            payload=preview_request, idempotency_key="sync-preview-key-0001",
        )
        assert replay == preview
        assert session.query(sync_db[-1]).count() == 0  # Preview never implicitly queues Apply.
    apply_request = {"contract_version": "1.0", "preview_id": preview["preview_id"], "config_revision": 4}
    with factory() as session:
        job_body, status, _etag = apply_sync_preview(
            session, production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
            payload=apply_request, idempotency_key="sync-apply-key-0001",
        )
        assert status == 202 and job_body["state"] == "queued"
        assert job_body["progress"] == {"completed": 0, "total": 1}
        replay, _, _ = apply_sync_preview(
            session, production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
            payload=apply_request, idempotency_key="sync-apply-key-0001",
        )
        assert replay == job_body
        from plugin.integrations.snapshots import SnapshotConflict
        with pytest.raises(SnapshotConflict):
            apply_sync_preview(
                session, production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
                payload={**apply_request, "config_revision": 5},
                idempotency_key="sync-apply-key-0001",
            )
        job = get_sync_job(session, job_id=UUID(job_body["id"]), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR))
        assert job["state"] == "queued"
        with pytest.raises(SyncOperationError) as denied:
            get_sync_job(session, job_id=UUID(job_body["id"]), production_id=UUID(PRODUCTION), actor_id=UUID("20000000-0000-4000-8000-000000000099"))
        assert denied.value.status == 403
    with factory.begin() as session:
        original = session.get(sync_db[-1], UUID(job_body["id"]))
        original.state = "failed"
    with factory() as session:
        retried, status, _etag = enqueue_sync_job_command(
            session, job_id=UUID(job_body["id"]), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
            command={"contract_version": "1.0", "reason_code": "provider_recovered"},
            idempotency_key="sync-retry-key-0001", operation="retry",
        )
        assert status == 202 and retried["state"] == "queued"
        again, _, _ = enqueue_sync_job_command(
            session, job_id=UUID(job_body["id"]), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
            command={"contract_version": "1.0", "reason_code": "provider_recovered"},
            idempotency_key="sync-retry-key-0001", operation="retry",
        )
        assert again == retried


def test_sync_preview_is_read_only_and_allowed_before_activation(sync_db):
    from uuid import UUID

    from plugin.integrations.sync import SyncOperationError, apply_sync_preview, create_sync_preview

    factory, Desired, *_ = sync_db
    data = seed_sync_state(sync_db)
    with factory.begin() as session:
        desired = session.get(Desired, UUID(PRODUCTION))
        desired.desired_config = dict(desired.desired_config)
        desired.desired_config["activation"] = {
            "enabled": False, "activated_at": None, "notification_epoch": 4,
        }
    preview, status, _ = create_sync_preview(
        factory(), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
        payload={
            "contract_version": "1.0", "config_revision": 4,
            "snapshot_id": str(data["snapshot_id"]),
        },
        idempotency_key="disabled-preview-key-001",
    )
    assert status == 200 and preview["state"] == "preview_ready"
    with factory() as session:
        with pytest.raises(SyncOperationError) as rejected:
            apply_sync_preview(
                session, production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
                payload={
                    "contract_version": "1.0", "config_revision": 4,
                    "preview_id": preview["preview_id"],
                },
                idempotency_key="disabled-apply-key-001",
            )
        assert rejected.value.code == "connection_not_ready"


def test_reconcile_command_only_queues_for_ambiguous_job(sync_db):
    from uuid import UUID

    from plugin.integrations.sync import SyncOperationError, enqueue_sync_job_command

    factory, *_ = sync_db
    data = seed_sync_state(sync_db)
    preview, _, _ = create_preview_for_worker(sync_db, data)
    job_body, _, _ = apply_preview_for_worker(sync_db, preview)
    with factory.begin() as session:
        job = session.get(sync_db[-1], UUID(job_body["id"]))
        job.state = "reconcile_required"
        job.last_error_code = "folder_outcome_requires_reconciliation"
    with factory() as session:
        reconciled, status, _ = enqueue_sync_job_command(
            session, job_id=UUID(job_body["id"]), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
            command={"contract_version": "1.0", "reason_code": "provider_readback"},
            idempotency_key="sync-reconcile-key-001", operation="reconcile",
        )
        assert status == 202 and reconciled["state"] == "queued"
        with pytest.raises(SyncOperationError) as unsafe_retry:
            enqueue_sync_job_command(
                session, job_id=UUID(job_body["id"]), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
                command={"contract_version": "1.0"},
                idempotency_key="sync-reconcile-key-002", operation="retry",
            )
        assert unsafe_retry.value.code == "reconcile_required"
        with pytest.raises(SyncOperationError) as wrong_state:
            enqueue_sync_job_command(
                session, job_id=UUID(reconciled["id"]), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
                command={"contract_version": "1.0"},
                idempotency_key="sync-reconcile-key-003", operation="reconcile",
            )
        assert wrong_state.value.code == "invalid_event"


def create_preview_for_worker(sync_db, data):
    from uuid import UUID

    from plugin.integrations.sync import create_sync_preview

    return create_sync_preview(
        sync_db[0](), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
        payload={"contract_version": "1.0", "config_revision": 4, "snapshot_id": str(data["snapshot_id"])},
        idempotency_key="sync-reconcile-preview-001",
    )


def apply_preview_for_worker(sync_db, preview):
    from uuid import UUID

    from plugin.integrations.sync import apply_sync_preview

    return apply_sync_preview(
        sync_db[0](), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
        payload={"contract_version": "1.0", "preview_id": preview["preview_id"], "config_revision": 4},
        idempotency_key="sync-reconcile-apply-001",
    )


def test_sync_preview_rejects_stale_config_and_job_commands_are_explicit(sync_db):
    from uuid import UUID

    from plugin.integrations.sync import SyncOperationError, create_sync_preview

    factory, Desired, *_ = sync_db
    data = seed_sync_state(sync_db)
    with factory.begin() as session:
        desired = session.get(Desired, UUID(PRODUCTION))
        desired.config_revision = 5
    with factory() as session:
        with pytest.raises(SyncOperationError) as stale:
            create_sync_preview(
                session, production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
                payload={"contract_version": "1.0", "config_revision": 4, "snapshot_id": str(data["snapshot_id"])},
                idempotency_key="stale-preview-key-001",
            )
        assert stale.value.code == "stale_config"


def test_sync_worker_lost_lease_fails_before_fake_storage_mutation(sync_db, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from uuid import UUID, uuid4

    from plugin.integrations import worker as sync_worker
    from plugin.jobs.queue import LeasedJob
    from plugin.storage.fake import FakeStorage

    factory, *_ = sync_db
    data = seed_sync_state(sync_db)
    preview, _, _ = create_preview_for_worker(sync_db, data)
    job_body, _, _ = apply_preview_for_worker(sync_db, preview)
    recorded_worker_id, stale_worker_id = uuid4(), uuid4()
    with factory.begin() as session:
        job = session.get(sync_db[-1], UUID(job_body["id"]))
        job.state = "leased"
        job.lease_owner = recorded_worker_id
        job.lease_generation = 2
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
    with factory() as session:
        leased_payload = dict(session.get(sync_db[-1], UUID(job_body["id"])).payload)
    stale_lease = LeasedJob(UUID(job_body["id"]), "sync_hierarchy", leased_payload, stale_worker_id, 1, 1)
    storage = FakeStorage(root_id="fake-root")
    monkeypatch.setattr(sync_worker, "_authorized_manager", lambda *_: True)
    monkeypatch.setattr(sync_worker, "_snapshot_task_matches_kitsu", lambda *_: True)

    result = sync_worker.make_hierarchy_sync_handler(factory, storage)(stale_lease)

    assert result.state == "retry" and result.error_code == "sync_lease_lost"
    assert [item.object_id for item in storage.objects.values() if item.mime_type == "folder"] == ["fake-root"]


def test_sync_apply_rejects_template_revision_changed_after_preview(sync_db):
    from uuid import UUID

    from plugin.integrations.sync import (
        SyncOperationError,
        apply_sync_preview,
        create_sync_preview,
    )

    factory, *_ = sync_db
    data = seed_sync_state(sync_db)
    preview, _, _ = create_sync_preview(
        factory(), production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
        payload={"contract_version": "1.0", "config_revision": 4, "snapshot_id": str(data["snapshot_id"])},
        idempotency_key="template-preview-key-001",
    )
    with factory.begin() as session:
        template = session.get(sync_db[-2], data["template_id"])
        template.version = 3
    with factory() as session:
        with pytest.raises(SyncOperationError) as stale:
            apply_sync_preview(
                session, production_id=UUID(PRODUCTION), actor_id=UUID(ACTOR),
                payload={"contract_version": "1.0", "preview_id": preview["preview_id"], "config_revision": 4},
                idempotency_key="template-apply-key-001",
            )
        assert stale.value.code == "stale_config"


def test_hierarchy_diff_never_provisions_a_child_of_an_excluded_parent():
    from plugin.integrations.sync import _scope_included

    production = {"object_type": "production", "id": PRODUCTION, "parent_id": None, "name": "P", "included_in_sync": True}
    shot = {"object_type": "shot", "id": SHOT, "parent_id": PRODUCTION, "name": "S", "included_in_sync": False}
    task = {"object_type": "task", "id": TASK, "parent_id": SHOT, "name": "T", "task_type_id": TASK_TYPE, "included_in_sync": True}
    desired = {"template_selection": {"selections": [{"scope": "shot"}]}, "sync_policy": {"scope": {"shots": True, "tasks": True}}}
    objects_by_id = {row["id"]: row for row in (production, shot, task)}
    assert _scope_included(production, desired, objects_by_id)
    assert not _scope_included(task, desired, objects_by_id)


def test_preview_does_not_claim_entity_creation_without_a_provisionable_task():
    from uuid import UUID

    from plugin.integrations.sync import build_sync_diff

    production = {"object_type": "production", "id": PRODUCTION, "parent_id": None, "name": "P", "included_in_sync": True}
    shot = {"object_type": "shot", "id": SHOT, "parent_id": PRODUCTION, "name": "S", "included_in_sync": True}
    task = {"object_type": "task", "id": TASK, "parent_id": SHOT, "name": "T", "task_type_id": TASK_TYPE, "included_in_sync": True}
    desired = {
        "template_selection": {"selections": [
            {"scope": "shot", "template_id": "vfx-files.shot.default", "revision": 1, "overrides": {}},
            {"scope": "task", "template_id": "vfx-files.task.default", "revision": 1, "overrides": {}},
        ]},
        "sync_policy": {"scope": {"shots": True, "tasks": True, "task_type_ids": ["40000000-0000-4000-8000-000000000099"]}},
    }
    summary, changes = build_sync_diff(
        production_id=UUID(PRODUCTION), root_id="root", desired=desired,
        objects=[production, shot, task], bindings=[], role_mappings={"shot": "shot-role", "task": "task-role"},
    )
    shot_change = next(item for item in changes if item["object_id"] == SHOT)
    task_change = next(item for item in changes if item["object_id"] == TASK)
    assert shot_change["action"] == "warning"
    assert "no selected compatible Task" in shot_change["reason"]
    assert task_change["action"] == "exclude"
    assert summary["create"] == 0


@pytest.mark.parametrize(
    ("selection", "error_code"),
    [
        ({"scope": "shot", "template_id": "unpublished-template", "revision": 1, "overrides": {}}, "invalid_event"),
        ({"scope": "shot", "template_id": "vfx-files.shot.default", "revision": 2, "overrides": {}}, "stale_config"),
        ({"scope": "shot", "template_id": "vfx-files.shot.default", "revision": 1, "overrides": {"shot": "new-name"}}, "invalid_event"),
    ],
)
def test_sync_preview_fails_closed_on_unresolved_template_selection(selection, error_code):
    from plugin.integrations.sync import SyncOperationError, selected_template_scopes

    with pytest.raises(SyncOperationError) as rejected:
        selected_template_scopes({"template_selection": {"selections": [selection]}})
    assert rejected.value.code == error_code


def test_explicit_apply_reaches_fenced_worker_and_fake_storage(sync_db, monkeypatch):
    from uuid import UUID, uuid4

    from plugin.folders.service import MemoryFolderStore, provision_folder
    from plugin.integrations import worker as sync_worker
    from plugin.integrations.sync import apply_sync_preview, create_sync_preview
    from plugin.jobs.queue import LeasedJob
    from plugin.storage.fake import FakeStorage

    factory, *_ = sync_db
    data = seed_sync_state(sync_db)
    actor_id = UUID(ACTOR)
    preview, _, _ = create_sync_preview(
        factory(), production_id=UUID(PRODUCTION), actor_id=actor_id,
        payload={"contract_version": "1.0", "config_revision": 4, "snapshot_id": str(data["snapshot_id"])},
        idempotency_key="worker-preview-key-0001",
    )
    job_body, _, _ = apply_sync_preview(
        factory(), production_id=UUID(PRODUCTION), actor_id=actor_id,
        payload={"contract_version": "1.0", "preview_id": preview["preview_id"], "config_revision": 4},
        idempotency_key="worker-apply-key-0001",
    )
    worker_id = uuid4()
    with factory.begin() as session:
        job = session.get(sync_db[-1], UUID(job_body["id"]))
        job.state = "leased"
        job.lease_owner = worker_id
        job.lease_generation = 1
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
    with factory() as session:
        leased_payload = dict(session.get(sync_db[-1], UUID(job_body["id"])).payload)
    leased = LeasedJob(UUID(job_body["id"]), "sync_hierarchy", leased_payload, worker_id, 1, 1)
    storage = FakeStorage(root_id="fake-root")
    folder_store = MemoryFolderStore()
    monkeypatch.setattr(sync_worker, "_authorized_manager", lambda *_: True)
    monkeypatch.setattr(sync_worker, "_snapshot_task_matches_kitsu", lambda *_: True)

    def fake_task_provision(session_factory, adapter, *, production_id, task_id, configuration_id, configuration_version, before_external):
        parent = "fake-root"
        for kind, object_id, name in (("shot", SHOT, "sc01_sh005"), ("task", TASK, "Compositing")):
            if not before_external():
                raise RuntimeError("sync fence lost")
            result = provision_folder(
                adapter, folder_store, storage_connection_id=str(data["connection_id"]),
                allowed_root_id="fake-root", canonical_scope_key=f"production:{PRODUCTION}:{kind}:{object_id}",
                parent_id=parent, display_name=name,
            )
            parent = result.provider_folder_id

    monkeypatch.setattr(sync_worker, "provision_task_folders", fake_task_provision)
    handler = sync_worker.make_hierarchy_sync_handler(factory, storage)
    result = handler(leased)
    assert result.state == "completed"
    assert len([obj for obj in storage.objects.values() if obj.mime_type == "folder"]) == 3
    with factory() as session:
        persisted_job = session.get(sync_db[-1], UUID(job_body["id"]))
        assert persisted_job.payload["progress"] == {"completed": 1, "total": 1}
