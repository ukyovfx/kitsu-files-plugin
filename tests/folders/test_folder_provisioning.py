from concurrent.futures import ThreadPoolExecutor
import hashlib
from uuid import uuid4
import pytest

from plugin.folders.service import FolderProvisioningError, MemoryFolderStore, PostgresFolderStore, provision_folder
from plugin.storage.fake import FakeStorage


def provision(adapter, store, label="Shot A"):
    return provision_folder(
        adapter,
        store,
        storage_connection_id="connection-1",
        allowed_root_id="fake-root",
        canonical_scope_key="production/00000000-0000-4000-8000-000000000001/task/00000000-0000-4000-8000-000000000002",
        parent_id="fake-root",
        display_name=label,
    )


def test_folder_reservation_serializes_concurrent_create():
    adapter = FakeStorage()
    store = MemoryFolderStore()

    def run(_):
        try:
            return provision(adapter, store)
        except FolderProvisioningError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, range(16)))

    assert len([call for call in adapter.calls if call[0] == "create_folder"]) == 1


def test_lost_create_response_uses_reserved_drive_id():
    adapter = FakeStorage()
    adapter.fail_after_create = True
    store = MemoryFolderStore()

    binding = provision(adapter, store)

    assert binding.state == "ready"
    assert adapter.get(binding.provider_folder_id).parent_id == "fake-root"
    assert len([call for call in adapter.calls if call[0] == "create_folder"]) == 1


def test_incomplete_listing_does_not_prove_absence():
    adapter = FakeStorage()
    adapter.incomplete_listings = True
    store = MemoryFolderStore()

    binding = provision(adapter, store)

    assert binding.state == "ready"
    assert not adapter.list_children("fake-root").complete
    assert len([call for call in adapter.calls if call[0] == "create_folder"]) == 1


def test_folder_identity_uses_stable_kitsu_scope_key_not_display_name():
    adapter = FakeStorage()
    store = MemoryFolderStore()

    first = provision(adapter, store, "Shot A")
    second = provision(adapter, store, "Renamed Shot")

    assert second.provider_folder_id == first.provider_folder_id
    assert len([call for call in adapter.calls if call[0] == "create_folder"]) == 1


def test_postgres_binding_recovers_lost_drive_create_response(zou_db_factory):
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import select

    from plugin.models import FolderBinding, StorageConnection

    connection_id = uuid4()
    production_id = uuid4()
    task_id = uuid4()
    with zou_db_factory.begin() as session:
        session.add(StorageConnection(
            id=connection_id,
            production_id=production_id,
            provider="google_drive",
            account_mode="shared_drive",
            drive_id="synthetic-shared-drive-id",
            allowed_root_id="fake-root",
            credential_ref="synthetic-reference-only",
            enabled=True,
        ))

    adapter = FakeStorage(root_id="fake-root", drive_id="synthetic-shared-drive-id")
    adapter.fail_after_create = True
    store = PostgresFolderStore(zou_db_factory)
    arguments = dict(
        storage_connection_id=str(connection_id),
        allowed_root_id="fake-root",
        canonical_scope_key=f"production/{production_id}/task/{task_id}",
        parent_id="fake-root",
        display_name="SHOT010",
    )
    try:
        first = provision_folder(adapter, store, **arguments)
        recovered = provision_folder(adapter, store, **{**arguments, "display_name": "SHOT010_RENAMED"})

        assert first.state == recovered.state == "ready"
        assert first.provider_folder_id == recovered.provider_folder_id
        assert adapter.verify(first.provider_folder_id, 0, hashlib.sha256(b"").hexdigest())
        assert [call[0] for call in adapter.calls].count("create_folder") == 1
        with zou_db_factory() as session:
            bindings = session.scalars(select(FolderBinding).where(FolderBinding.storage_connection_id == connection_id)).all()
            assert len(bindings) == 1
            assert bindings[0].state == "ready"
            assert bindings[0].provider_folder_id == first.provider_folder_id
            assert bindings[0].canonical_scope_key == arguments["canonical_scope_key"]
    finally:
        with zou_db_factory.begin() as session:
            session.execute(FolderBinding.__table__.delete().where(FolderBinding.storage_connection_id == connection_id))
            session.execute(StorageConnection.__table__.delete().where(StorageConnection.id == connection_id))
