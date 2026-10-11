from concurrent.futures import ThreadPoolExecutor
import hashlib
from uuid import uuid4
import pytest

from plugin.folders.service import FolderProvisioningError, MemoryFolderStore, PostgresFolderStore, provision_folder
from plugin.storage.base import AmbiguousOutcome
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


def test_worker_restart_after_provider_create_reconciles_reserved_folder_id():
    class CrashAfterProviderWriteStore(MemoryFolderStore):
        crash = True

        def mark_ready(self, reservation, provider_id):
            if self.crash:
                self.crash = False
                raise RuntimeError("simulated worker exit after provider create")
            return super().mark_ready(reservation, provider_id)

    adapter = FakeStorage()
    store = CrashAfterProviderWriteStore()
    with pytest.raises(RuntimeError, match="simulated worker exit"):
        provision(adapter, store)

    recovered = provision(adapter, store)

    assert recovered.state == "ready"
    assert recovered.provider_folder_id in adapter.objects
    assert [call[0] for call in adapter.calls].count("create_folder") == 1


def test_incomplete_listing_does_not_prove_absence():
    adapter = FakeStorage()
    adapter.incomplete_listings = True
    store = MemoryFolderStore()

    with pytest.raises(AmbiguousOutcome, match="complete child listing"):
        provision(adapter, store)

    assert not adapter.list_children("fake-root").complete
    assert len([call for call in adapter.calls if call[0] == "create_folder"]) == 0

    adapter.incomplete_listings = False
    recovered = provision(adapter, store)
    assert recovered.state == "ready"
    assert len([call for call in adapter.calls if call[0] == "create_folder"]) == 1


def test_unique_existing_folder_is_adopted_by_stable_binding():
    from plugin.storage.base import StorageObject

    adapter = FakeStorage()
    adapter.objects["existing-shot"] = StorageObject(
        "existing-shot", "fake-root", 0, hashlib.sha256(b"").hexdigest(), None,
        "folder", name="Shot A",
    )
    store = MemoryFolderStore()

    binding = provision(adapter, store)

    assert binding.state == "ready"
    assert binding.provider_folder_id == "existing-shot"
    assert not [call for call in adapter.calls if call[0] == "create_folder"]


def test_duplicate_existing_names_fail_closed_instead_of_guessing():
    from plugin.storage.base import StorageObject

    adapter = FakeStorage()
    for folder_id in ("existing-shot-a", "existing-shot-b"):
        adapter.objects[folder_id] = StorageObject(
            folder_id, "fake-root", 0, hashlib.sha256(b"").hexdigest(), None,
            "folder", name="Shot A",
        )
    store = MemoryFolderStore()

    with pytest.raises(FolderProvisioningError, match="Multiple existing folders"):
        provision(adapter, store)

    assert not [call for call in adapter.calls if call[0] == "create_folder"]


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


def test_postgres_folder_binding_cannot_adopt_one_provider_id_for_two_kitsu_scopes(
    synthetic_upload_task, zou_db_factory
):
    pytest.importorskip("sqlalchemy")
    from sqlalchemy.exc import IntegrityError

    from plugin.models import FolderBinding

    _production_id, _entity_type, _task_type, _entity, _task, connection_id, _actor_id = synthetic_upload_task
    with pytest.raises(IntegrityError):
        with zou_db_factory.begin() as session:
            session.add_all([
                FolderBinding(
                    storage_connection_id=connection_id,
                    allowed_root_id="synthetic-root-id",
                    canonical_scope_key="production:one:shot:uuid-a",
                    parent_provider_id="synthetic-root-id",
                    provider_folder_id="same-provider-folder-id",
                    state="ready",
                ),
                FolderBinding(
                    storage_connection_id=connection_id,
                    allowed_root_id="synthetic-root-id",
                    canonical_scope_key="production:one:shot:uuid-b",
                    parent_provider_id="synthetic-root-id",
                    provider_folder_id="same-provider-folder-id",
                    state="ready",
                ),
            ])
            session.flush()
