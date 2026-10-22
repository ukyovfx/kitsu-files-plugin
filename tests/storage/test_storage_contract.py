import hashlib
import io
from dataclasses import replace

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


def test_fake_storage_downloads_exact_bytes_by_provider_identity():
    adapter = FakeStorage()
    content = b"immutable working file"
    digest = hashlib.sha256(content).hexdigest()
    object_id = adapter.reserve_id("working-file")
    adapter.upload(io.BytesIO(content), object_id, len(content), digest, "fake-root")
    target = io.BytesIO()

    count, downloaded_hash = adapter.download_to(object_id, target)

    assert target.getvalue() == content
    assert (count, downloaded_hash) == (len(content), digest)
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


def test_fake_folder_names_are_available_for_template_discovery():
    adapter = FakeStorage()
    folder_id = adapter.reserve_id("template-folder")
    adapter.create_folder("fake-root", folder_id, {"display_name": "Sequence A"})

    discovered = adapter.list_children("fake-root").items[0]

    assert discovered.object_id == folder_id
    assert discovered.name == "Sequence A"


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


def test_persistent_fake_storage_folder_metadata_survives_worker_restart(tmp_path):
    state_path = tmp_path / "fake-provider" / "folders.json"
    first_process = PersistentFakeStorage(state_path, root_id="test-root")
    first_process.create_folder("test-root", "stable-folder-id", {"display_name": "shot"})

    second_process = PersistentFakeStorage(state_path, root_id="test-root")

    folder = second_process.get("stable-folder-id")
    assert folder is not None and folder.mime_type == "folder"
    assert folder.name == "shot"


def test_drive_sync_metadata_survives_fake_storage_restart(tmp_path):
    state_path = tmp_path / "fake-provider" / "metadata.json"
    first_process = PersistentFakeStorage(state_path, root_id="test-root")
    first_process.create_folder("test-root", "stable-folder-id", {"display_name": "shot"})
    first_process.objects["stable-folder-id"] = replace(
        first_process.objects["stable-folder-id"],
        modified_time="2026-10-13T01:02:03Z",
        md5_checksum="0123456789abcdef0123456789abcdef",
    )
    first_process._persist()

    second_process = PersistentFakeStorage(state_path, root_id="test-root")
    listing = second_process.list_children("test-root")

    assert listing.items[0].modified_time == "2026-10-13T01:02:03Z"
    assert listing.items[0].md5_checksum == "0123456789abcdef0123456789abcdef"


def test_fake_delete_is_exact_idempotent_and_never_recursive(tmp_path):
    import io as _io
    from plugin.storage.base import PermissionFailure
    from plugin.storage.fake import FakeStorage, PersistentFakeStorage

    for storage in (FakeStorage(root_id="root"), PersistentFakeStorage(tmp_path / "state.json", root_id="root")):
        body = b"abc"
        digest = hashlib.sha256(body).hexdigest()
        storage.create_folder("root", "folder", {"display_name": "f"})
        storage.upload(_io.BytesIO(body), "file", 3, digest, "folder", file_name="a.exr")
        with pytest.raises(PermissionFailure):
            storage.delete("root")
        with pytest.raises(PermissionFailure):
            storage.delete("folder")  # non-empty folders are refused, never emptied
        assert storage.get("file") is not None
        assert storage.delete("file") is True and storage.get("file") is None
        assert storage.delete("file") is False
        assert storage.delete("folder") is True and storage.get("root") is not None
    reloaded = PersistentFakeStorage(tmp_path / "state.json", root_id="root")
    assert reloaded.get("file") is None and reloaded.get("folder") is None
