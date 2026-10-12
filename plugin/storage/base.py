from dataclasses import dataclass
from typing import BinaryIO, Mapping, Protocol


class StorageError(Exception):
    pass


class PermissionFailure(StorageError):
    pass


class RetryableStorageFailure(StorageError):
    def __init__(self, retry_after_seconds: int = 30, message: str = "Storage provider is temporarily unavailable"):
        if isinstance(retry_after_seconds, bool) or not 1 <= int(retry_after_seconds) <= 3600:
            raise ValueError("Storage retry delay is outside safe bounds")
        self.retry_after_seconds = int(retry_after_seconds)
        super().__init__(message)


class AmbiguousOutcome(StorageError):
    pass


class AlreadyExists(StorageError):
    pass


@dataclass(frozen=True)
class StorageObject:
    object_id: str
    parent_id: str | None
    size: int | None
    sha256: str | None
    drive_id: str | None = None
    mime_type: str | None = None
    trashed: bool = False
    name: str | None = None


@dataclass(frozen=True)
class Listing:
    items: tuple[StorageObject, ...]
    complete: bool
    next_page_token: str | None = None


@dataclass(frozen=True)
class StorageCapabilities:
    can_add_children: bool
    root_id: str
    drive_id: str | None
    shared_drive: bool
    root_visible: bool
    root_ancestors: tuple[str, ...]


class StorageAdapter(Protocol):
    def reserve_id(self, scope_key: str) -> str: ...
    def create_folder(self, parent_id: str, reserved_id: str, correlation: Mapping[str, str]) -> StorageObject: ...
    def upload(self, source: BinaryIO, reserved_id: str, size: int, sha256: str, parent_id: str) -> StorageObject: ...
    def get(self, object_id: str) -> StorageObject | None: ...
    def download_to(self, object_id: str, destination: BinaryIO) -> tuple[int, str]: ...
    def verify(self, object_id: str, size: int, sha256: str) -> bool: ...
    def capabilities(self, root_id: str) -> StorageCapabilities: ...
    def list_children(self, parent_id: str, page_token: str | None = None) -> Listing: ...
    def assert_parent_within_root(self, parent_id: str) -> None: ...


def resolve_storage_adapter(adapter_or_factory, connection):
    """Resolve a per-Production adapter while retaining fixed-adapter test support."""
    if callable(getattr(adapter_or_factory, "capabilities", None)):
        return adapter_or_factory
    return adapter_or_factory(connection)
