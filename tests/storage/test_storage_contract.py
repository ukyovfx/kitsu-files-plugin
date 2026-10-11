import hashlib
import io

import pytest

from plugin.storage.base import AlreadyExists, AmbiguousOutcome
from plugin.storage.fake import FakeStorage, MemoryStaging, PersistentFakeStorage
from plugin.uploads.staging import FileSystemStaging


def test_fake_upload_verifies_exact_size_and_hash():
    adapter = FakeStorage()
    content = b"synthetic artifact"
    digest = hashlib.sha256(content).hexdigest()
    object_id = adapter.reserve_id("task/00000000-0000-4000-8000-000000000002")

    obj = adapter.upload(io.BytesIO(content), object_id, len(content), digest, "fake-root")

    assert obj.object_id == object_id
    assert adapter.verify(object_id, len(content), digest)
    assert not adapter.verify(object_id, len(content) + 1, digest)


def test_ambiguous_create_is_reconciled_by_reserved_id():
    adapter = FakeStorage()
    adapter.fail_after_create = True
    object_id = adapter.reserve_id("scope/uuid")

    with pytest.raises(AmbiguousOutcome):
        adapter.create_folder("fake-root", object_id, {})

    existing = adapter.get(object_id)
    assert existing is not None
    with pytest.raises(AlreadyExists):
        adapter.create_folder("fake-root", object_id, {})


def test_staging_keys_are_immutable_and_exact():
    staging = MemoryStaging()
    key = "upload/00000000-0000-4000-8000-000000000001/0"
    staging.put(key, b"fixture", hashlib.sha256(b"fixture").hexdigest())
    assert staging.open(key).read() == b"fixture"
    with pytest.raises(ValueError):
        staging.put(key, b"changed", hashlib.sha256(b"changed").hexdigest())
    staging.delete_exact(key)
    assert key not in staging._data


def test_filesystem_staging_rejects_traversal_and_cleans_only_one_session(tmp_path):
    staging = FileSystemStaging(tmp_path)
    first_id = "00000000-0000-4000-8000-000000000001"
    other_id = "00000000-0000-4000-8000-000000000002"
    first_key = f"upload/{first_id}/0"
    other_key = f"upload/{other_id}/0"
    staging.put(first_key, b"first", hashlib.sha256(b"first").hexdigest())
    staging.put(other_key, b"other", hashlib.sha256(b"other").hexdigest())

    staging.delete_session_chunks(first_id, [first_key])

    assert not (tmp_path / first_key).exists()
    assert (tmp_path / other_key).read_bytes() == b"other"
    with pytest.raises(ValueError):
        staging.open("../../outside")


def test_persistent_fake_storage_readback_survives_worker_process_restart(tmp_path):
    state_path = tmp_path / "fake-provider" / "state.json"
    first_process = PersistentFakeStorage(state_path, root_id="test-root")
    provider_id = first_process.reserve_id("upload:stable-kitsu-upload-id")
    data = b"verified fake provider bytes"
    digest = hashlib.sha256(data).hexdigest()
    first_process.fail_upload_after_write = True
    with pytest.raises(AmbiguousOutcome):
        first_process.upload(io.BytesIO(data), provider_id, len(data), digest, "test-root")

    second_process = PersistentFakeStorage(state_path, root_id="test-root")
    assert second_process.get(provider_id).size == len(data)
    assert second_process.verify(provider_id, len(data), digest)
    assert second_process.reserve_id("upload:stable-kitsu-upload-id") == provider_id
