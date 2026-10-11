"""Durable reservation-first provisioning. Display names never form identity."""

from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from ..storage.base import AmbiguousOutcome, StorageAdapter, StorageObject


@dataclass
class FolderReservation:
    storage_connection_id: str
    allowed_root_id: str
    canonical_scope_key: str
    parent_id: str
    provider_folder_id: str | None = None
    state: str = "reserved"


class FolderStore(Protocol):
    def reserve_binding(self, storage_connection_id: str, allowed_root_id: str, canonical_scope_key: str, parent_id: str): ...
    def set_provider_id(self, reservation: FolderReservation, provider_id: str) -> bool: ...
    def mark_ready(self, reservation: FolderReservation, provider_id: str) -> None: ...
    def mark_reconcile(self, reservation: FolderReservation) -> None: ...


class FolderProvisioningError(Exception):
    pass


def _matches(obj: StorageObject | None, provider_id: str, parent_id: str) -> bool:
    return bool(obj and obj.object_id == provider_id and obj.parent_id == parent_id and obj.mime_type == "folder" and not obj.trashed)


def provision_folder(
    adapter: StorageAdapter,
    store: FolderStore,
    *,
    storage_connection_id: str,
    allowed_root_id: str,
    canonical_scope_key: str,
    parent_id: str,
    display_name: str,
) -> FolderReservation:
    """Reserve identity in local storage before create; reconcile by exact provider ID."""
    capabilities = adapter.capabilities(allowed_root_id)
    if not capabilities.root_visible or not capabilities.can_add_children:
        raise FolderProvisioningError("Allowed storage root is not writable")
    adapter.assert_parent_within_root(parent_id)
    binding, is_new = store.reserve_binding(storage_connection_id, allowed_root_id, canonical_scope_key, parent_id)
    if binding.parent_id != parent_id:
        store.mark_reconcile(binding)
        raise FolderProvisioningError("Reserved folder parent changed")
    if binding.state == "ready":
        existing = adapter.get(binding.provider_folder_id)
        if not _matches(existing, binding.provider_folder_id, parent_id):
            store.mark_reconcile(binding)
            raise FolderProvisioningError("Bound folder is missing or has changed parent/type")
        return binding

    if not is_new:
        raise FolderProvisioningError("Folder reservation is owned by another worker")
    if binding.provider_folder_id is None:
        reserved_id = adapter.reserve_id(canonical_scope_key)
        if not store.set_provider_id(binding, reserved_id):
            raise FolderProvisioningError("Folder reservation lost its owner")
        binding.provider_folder_id = reserved_id

    try:
        obj = adapter.create_folder(parent_id, binding.provider_folder_id, {"scope_key": canonical_scope_key, "display_name": display_name})
    except AmbiguousOutcome:
        try:
            obj = adapter.get(binding.provider_folder_id)
        except AmbiguousOutcome:
            store.mark_reconcile(binding)
            binding.state = "reconcile_required"
            return binding
        if not _matches(obj, binding.provider_folder_id, parent_id):
            store.mark_reconcile(binding)
            binding.state = "reconcile_required"
            return binding
    if not _matches(obj, binding.provider_folder_id, parent_id):
        store.mark_reconcile(binding)
        raise FolderProvisioningError("Provider returned a folder with unexpected identity or ancestry")
    store.mark_ready(binding, binding.provider_folder_id)
    binding.state = "ready"
    return binding


class MemoryFolderStore:
    """Thread-safe repository double for deterministic concurrency tests."""

    def __init__(self):
        self._rows = {}
        self._lock = Lock()

    def reserve_binding(self, storage_connection_id, allowed_root_id, canonical_scope_key, parent_id):
        key = (storage_connection_id, allowed_root_id, canonical_scope_key)
        with self._lock:
            row = self._rows.get(key)
            if row is not None:
                return row, False
            row = FolderReservation(*key, parent_id)
            self._rows[key] = row
            return row, True

    def set_provider_id(self, reservation, provider_id):
        with self._lock:
            key = (reservation.storage_connection_id, reservation.allowed_root_id, reservation.canonical_scope_key)
            row = self._rows[key]
            if row.provider_folder_id not in (None, provider_id):
                return False
            row.provider_folder_id = provider_id
            row.state = "creating"
            return True

    def mark_ready(self, reservation, provider_id):
        with self._lock:
            reservation.provider_folder_id = provider_id
            reservation.state = "ready"

    def mark_reconcile(self, reservation):
        with self._lock:
            reservation.state = "reconcile_required"


class PostgresFolderStore:
    """Plugin table repository; unique key and row locks serialize reservations."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def reserve_binding(self, storage_connection_id, allowed_root_id, canonical_scope_key, parent_id):
        from uuid import uuid4

        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert

        from ..models import FolderBinding

        row_id = uuid4()
        with self.session_factory.begin() as session:
            inserted_id = session.execute(
                insert(FolderBinding)
                .values(
                    id=row_id,
                    storage_connection_id=storage_connection_id,
                    allowed_root_id=allowed_root_id,
                    canonical_scope_key=canonical_scope_key,
                    parent_provider_id=parent_id,
                    state="reserved",
                )
                .on_conflict_do_nothing(constraint="plugin_vfx_files_folder_identity_uc")
                .returning(FolderBinding.id)
            ).scalar_one_or_none()
            is_new = inserted_id is not None
            row = session.scalar(
                select(FolderBinding)
                .where(
                    FolderBinding.storage_connection_id == storage_connection_id,
                    FolderBinding.allowed_root_id == allowed_root_id,
                    FolderBinding.canonical_scope_key == canonical_scope_key,
                )
                .with_for_update()
            )
            if row is None:
                raise FolderProvisioningError("Folder reservation was not persisted")
            reservation = FolderReservation(
                str(row.storage_connection_id),
                row.allowed_root_id,
                row.canonical_scope_key,
                row.parent_provider_id,
                row.provider_folder_id,
                row.state,
            )
            reservation._row_id = row.id
            return reservation, is_new

    def set_provider_id(self, reservation, provider_id):
        from sqlalchemy import update

        from ..models import FolderBinding

        with self.session_factory.begin() as session:
            result = session.execute(
                update(FolderBinding)
                .where(FolderBinding.id == reservation._row_id, FolderBinding.state == "reserved", FolderBinding.provider_folder_id.is_(None))
                .values(provider_folder_id=provider_id, state="creating")
            )
            return result.rowcount == 1

    def mark_ready(self, reservation, provider_id):
        from sqlalchemy import update

        from ..models import FolderBinding

        with self.session_factory.begin() as session:
            result = session.execute(
                update(FolderBinding)
                .where(FolderBinding.id == reservation._row_id, FolderBinding.provider_folder_id == provider_id)
                .values(state="ready")
            )
            if result.rowcount != 1:
                raise FolderProvisioningError("Folder binding identity changed before completion")

    def mark_reconcile(self, reservation):
        from sqlalchemy import update

        from ..models import FolderBinding

        with self.session_factory.begin() as session:
            session.execute(
                update(FolderBinding)
                .where(FolderBinding.id == reservation._row_id)
                .values(state="reconcile_required")
            )
