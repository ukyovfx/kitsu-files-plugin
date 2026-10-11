"""Deterministic fake with explicit failure injection and no external I/O."""

import hashlib
import io
import json
import os
from threading import Lock
from uuid import uuid4
from pathlib import Path

from .base import (
    AlreadyExists,
    AmbiguousOutcome,
    Listing,
    StorageCapabilities,
    StorageObject,
)


class FakeStorage:
    def __init__(self, root_id="fake-root", drive_id=None):
        self.root_id = root_id
        self.drive_id = drive_id
        self.objects = {root_id: StorageObject(root_id, None, None, None, drive_id, "folder")}
        self.reservations = {}
        self._bytes = {}
        self.lock = Lock()
        self.fail_after_create = False
        self.fail_upload_after_write = False
        self.incomplete_listings = False
        self.can_add_children = True
        self.root_ancestors = (root_id,)
        self.calls = []

    def reserve_id(self, scope_key):
        with self.lock:
            return self.reservations.setdefault(scope_key, f"fake-{uuid4()}")

    def create_folder(self, parent_id, reserved_id, correlation):
        self.calls.append(("create_folder", reserved_id))
        with self.lock:
            if reserved_id in self.objects:
                raise AlreadyExists(reserved_id)
            obj = StorageObject(reserved_id, parent_id, 0, hashlib.sha256(b"").hexdigest(), self.drive_id, "folder", name=correlation.get("display_name"))
            self.objects[reserved_id] = obj
        if self.fail_after_create:
            self.fail_after_create = False
            raise AmbiguousOutcome("provider accepted create but response was lost")
        return obj

    def upload(self, source, reserved_id, size, sha256, parent_id):
        self.calls.append(("upload", reserved_id))
        body = source.read()
        if len(body) != size or hashlib.sha256(body).hexdigest() != sha256:
            raise ValueError("source bytes do not match declared size/hash")
        with self.lock:
            if reserved_id in self.objects:
                raise AlreadyExists(reserved_id)
            obj = StorageObject(reserved_id, parent_id, size, sha256, self.drive_id, "application/octet-stream")
            self.objects[reserved_id] = obj
            self._bytes[reserved_id] = bytes(body)
        if self.fail_upload_after_write:
            self.fail_upload_after_write = False
            raise AmbiguousOutcome("provider accepted upload but response was lost")
        return obj

    def get(self, object_id):
        return self.objects.get(object_id)

    def download_to(self, object_id, destination):
        obj = self.get(object_id)
        body = self._bytes.get(object_id)
        if obj is None or obj.trashed or body is None:
            raise FileNotFoundError("Fake storage object is unavailable")
        digest = hashlib.sha256()
        count = 0
        for offset in range(0, len(body), 1024 * 1024):
            chunk = body[offset:offset + 1024 * 1024]
            destination.write(chunk)
            digest.update(chunk)
            count += len(chunk)
        return count, digest.hexdigest()

    def verify(self, object_id, size, sha256):
        obj = self.get(object_id)
        return bool(obj and not obj.trashed and obj.size == size and obj.sha256 == sha256)

    def capabilities(self, root_id):
        if root_id != self.root_id:
            raise PermissionError("Configured root does not match adapter root")
        return StorageCapabilities(self.can_add_children, self.root_id, self.drive_id, self.drive_id is not None, True, self.root_ancestors)

    def list_children(self, parent_id, page_token=None):
        items = tuple(obj for obj in self.objects.values() if obj.parent_id == parent_id)
        return Listing(items, complete=not self.incomplete_listings)

    def assert_parent_within_root(self, parent_id):
        current = parent_id
        visited = set()
        for _ in range(64):
            if current == self.root_id:
                return
            if current in visited or current not in self.objects:
                break
            visited.add(current)
            current = self.objects[current].parent_id
            if current is None:
                break
        raise PermissionError("Target parent is not beneath the allowed root")


class PersistentFakeStorage(FakeStorage):
    """Disk-backed FakeStorage for disposable process-level integration tests."""

    def __init__(self, state_path, *, root_id="fake-root", drive_id=None):
        self.state_path = Path(state_path).resolve()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir = self.state_path.parent / (self.state_path.stem + "-objects")
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(root_id=root_id, drive_id=drive_id)
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.reservations.update(data.get("reservations", {}))
            for value in data.get("objects", []):
                item = StorageObject(**value)
                self.objects[item.object_id] = item
                if item.size is not None:
                    blob = self._blob_path(item.object_id)
                    if not blob.exists() or blob.stat().st_size != item.size:
                        raise ValueError("Persistent fake storage object bytes are missing")
                    if hashlib.sha256(blob.read_bytes()).hexdigest() != item.sha256:
                        raise ValueError("Persistent fake storage object checksum mismatch")
        else:
            self._persist()

    def _blob_path(self, object_id):
        safe_name = hashlib.sha256(object_id.encode("utf-8")).hexdigest()
        return self.blob_dir / safe_name

    def _persist(self):
        data = {
            "reservations": self.reservations,
            "objects": [
                {
                    "object_id": item.object_id,
                    "parent_id": item.parent_id,
                    "size": item.size,
                    "sha256": item.sha256,
                    "drive_id": item.drive_id,
                    "mime_type": item.mime_type,
                    "trashed": item.trashed,
                    "name": item.name,
                }
                for item in self.objects.values()
            ],
        }
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(temp_path, self.state_path)

    def reserve_id(self, scope_key):
        value = super().reserve_id(scope_key)
        self._persist()
        return value

    def create_folder(self, parent_id, reserved_id, correlation):
        try:
            return super().create_folder(parent_id, reserved_id, correlation)
        finally:
            item = self.objects.get(reserved_id)
            if item is not None and item.mime_type == "folder":
                blob = self._blob_path(reserved_id)
                if not blob.exists():
                    self._write_blob(reserved_id, b"")
            self._persist()

    def upload(self, source, reserved_id, size, sha256, parent_id):
        body = source.read()
        existing = self.get(reserved_id)
        if existing is not None:
            if self.verify(reserved_id, size, sha256) and existing.parent_id == parent_id:
                return existing
            raise AlreadyExists(reserved_id)
        try:
            result = super().upload(io.BytesIO(body), reserved_id, size, sha256, parent_id)
        except AmbiguousOutcome:
            obj = self.get(reserved_id)
            if obj is not None and obj.size == size:
                self._write_blob(reserved_id, body)
            self._persist()
            raise
        self._write_blob(reserved_id, body)
        self._persist()
        return result

    def _write_blob(self, object_id, body):
        path = self._blob_path(object_id)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_bytes(body)
        os.replace(temp_path, path)

    def get(self, object_id):
        return self.objects.get(object_id)

    def download_to(self, object_id, destination):
        obj = self.get(object_id)
        path = self._blob_path(object_id)
        if obj is None or obj.trashed or not path.is_file():
            raise FileNotFoundError("Fake storage object is unavailable")
        digest = hashlib.sha256()
        count = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
                digest.update(chunk)
                count += len(chunk)
        return count, digest.hexdigest()

    def verify(self, object_id, size, sha256):
        obj = self.get(object_id)
        if not obj or obj.size != size or obj.sha256 != sha256:
            return False
        path = self._blob_path(object_id)
        return path.exists() and path.stat().st_size == size and hashlib.sha256(path.read_bytes()).hexdigest() == sha256


class MemoryStaging:
    """Synthetic durable-key store used by service tests."""

    def __init__(self):
        self._data = {}
        self._lock = Lock()

    def put(self, key, data, expected_sha256=None):
        if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("staging hash mismatch")
        with self._lock:
            current = self._data.get(key)
            if current is not None and current != data:
                raise ValueError("staging key is immutable")
            self._data[key] = bytes(data)

    def open(self, key):
        with self._lock:
            return io.BytesIO(self._data[key])

    def delete_exact(self, key):
        with self._lock:
            self._data.pop(key, None)

    def delete_session_chunks(self, session_id, recorded_keys):
        prefix = f"upload/{session_id}/"
        with self._lock:
            for key in recorded_keys:
                if not key.startswith(prefix):
                    raise ValueError("Cleanup key is outside the claimed upload session")
                self._data.pop(key, None)
            for key in tuple(self._data):
                tail = key[len(prefix):] if key.startswith(prefix) else None
                if tail is not None and tail.isdecimal():
                    self._data.pop(key, None)
