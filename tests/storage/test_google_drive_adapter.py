import hashlib
import io
import json

import pytest

from plugin.storage.base import AmbiguousOutcome, PermissionFailure
from plugin.storage.google_drive import DriveConfig, GoogleDriveStorageAdapter, FOLDER_MIME


class Response:
    def __init__(self, status_code, data=None, content=b""):
        self.status_code = status_code
        self._data = data or {}
        self.content = content

    def json(self):
        return self._data

    def iter_content(self, chunk_size):
        yield self.content


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if hasattr(kwargs.get("data"), "__iter__") and not isinstance(kwargs["data"], (bytes, str)):
            self.calls[-1] = (method, url, {**kwargs, "data": b"".join(kwargs["data"])})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def drive(transport, account_mode="shared_drive"):
    config = DriveConfig(
        account_mode=account_mode,
        allowed_root_id="root-1",
        drive_id="drive-1" if account_mode == "shared_drive" else None,
        owner_subject="owner@example.invalid" if account_mode == "my_drive" else None,
    )
    return GoogleDriveStorageAdapter(config, lambda: "synthetic-token", transport)


def test_shared_drive_uses_drive_scoped_listing_and_support_flags():
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(200, {"files": [{"id": "folder-1", "name": "Seq 010", "parents": ["root-1"], "mimeType": FOLDER_MIME, "driveId": "drive-1"}], "nextPageToken": "next"}),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")

    listing = adapter.list_children("root-1")

    params = transport.calls[1][2]["params"]
    assert params["supportsAllDrives"] == "true"
    assert params["includeItemsFromAllDrives"] == "true"
    assert params["corpora"] == "drive"
    assert params["driveId"] == "drive-1"
    assert listing.items[0].name == "Seq 010"
    assert listing.complete is True
    assert listing.next_page_token == "next"


def test_my_drive_owner_identity_must_match_configured_subject():
    transport = FakeTransport([Response(200, {"user": {"emailAddress": "someone-else@example.invalid"}})])
    adapter = drive(transport, "my_drive")
    with pytest.raises(PermissionFailure):
        adapter.capabilities("root-1")


def test_drive_404_is_ambiguous_not_proof_of_absence():
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(404),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")
    with pytest.raises(AmbiguousOutcome):
        adapter.get("unknown-object")


def test_reserved_id_folder_create_has_no_name_based_identity():
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(201, {"id": "provider-id", "parents": ["root-1"], "mimeType": FOLDER_MIME, "driveId": "drive-1"}),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")
    result = adapter.create_folder("root-1", "provider-id", {"scope_key": "task/uuid", "display_name": "Renamable"})
    body = transport.calls[1][2]["json"]
    assert result.object_id == "provider-id"
    assert body["id"] == "provider-id"
    assert body["appProperties"]["scope_key"] == "task/uuid"


def test_my_drive_folder_create_is_owner_scoped_and_root_anchored():
    transport = FakeTransport([
        Response(200, {"user": {"emailAddress": "owner@example.invalid"}}),
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "capabilities": {"canAddChildren": True}}),
        Response(200, {"id": "reserved-folder", "name": "Sequence 010", "parents": ["root-1"], "mimeType": FOLDER_MIME}),
    ])
    adapter = drive(transport, "my_drive")
    adapter.capabilities("root-1")

    result = adapter.create_folder("root-1", "reserved-folder", {"scope_key": "sequence:stable-uuid", "display_name": "Sequence 010"})

    assert result.object_id == "reserved-folder"
    assert transport.calls[2][2]["params"]["supportsAllDrives"] == "true"
    assert transport.calls[2][2]["json"]["parents"] == ["root-1"]
    assert transport.calls[2][2]["json"]["id"] == "reserved-folder"


def test_shared_drive_create_and_list_keep_drive_scope_flags():
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(201, {"id": "stable-folder", "parents": ["root-1"], "mimeType": FOLDER_MIME, "driveId": "drive-1"}),
        Response(200, {"files": []}),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")
    adapter.create_folder("root-1", "stable-folder", {"scope_key": "shot:stable-uuid", "display_name": "SH010"})
    adapter.list_children("root-1")

    assert transport.calls[1][2]["params"]["supportsAllDrives"] == "true"
    assert transport.calls[2][2]["params"]["supportsAllDrives"] == "true"
    assert transport.calls[2][2]["params"]["includeItemsFromAllDrives"] == "true"
    assert transport.calls[2][2]["params"]["driveId"] == "drive-1"


def test_root_write_capability_denies_folder_and_upload_before_provider_io():
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": False}}),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")

    with pytest.raises(PermissionFailure):
        adapter.create_folder("root-1", "reserved-folder", {"display_name": "Do not create"})
    with pytest.raises(PermissionFailure):
        adapter.upload(io.BytesIO(b"x"), "reserved-file", 1, hashlib.sha256(b"x").hexdigest(), "root-1")
    assert len(transport.calls) == 1


def test_upload_download_and_sha256_readback_are_byte_exact_on_shared_drive():
    content = b"synthetic shared drive publish bytes"
    digest = hashlib.sha256(content).hexdigest()
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(201, {"id": "artifact-1", "parents": ["root-1"], "mimeType": "application/octet-stream", "driveId": "drive-1", "size": len(content)}),
        Response(200, {"id": "artifact-1", "parents": ["root-1"], "mimeType": "application/octet-stream", "driveId": "drive-1", "size": len(content)}),
        Response(200, content=content),
        Response(200, {"id": "artifact-1", "parents": ["root-1"], "mimeType": "application/octet-stream", "driveId": "drive-1", "size": len(content)}),
        Response(200, content=content),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")

    artifact = adapter.upload(io.BytesIO(content), "artifact-1", len(content), digest, "root-1")
    assert artifact.object_id == "artifact-1"
    assert adapter.verify("artifact-1", len(content), digest)
    downloaded = io.BytesIO()
    assert adapter.download_to("artifact-1", downloaded) == (len(content), digest)
    assert downloaded.getvalue() == content
    upload_call = transport.calls[1]
    assert upload_call[2]["params"]["supportsAllDrives"] == "true"
    assert content in upload_call[2]["data"]


def test_lost_upload_response_retries_same_reserved_id_and_verifies_existing_bytes():
    content = b"retry-safe bytes"
    digest = hashlib.sha256(content).hexdigest()
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        TimeoutError("provider accepted upload but response was lost"),
        Response(409),
        Response(200, {"id": "artifact-1", "parents": ["root-1"], "mimeType": "application/octet-stream", "driveId": "drive-1", "size": len(content)}),
        Response(200, {"id": "artifact-1", "parents": ["root-1"], "mimeType": "application/octet-stream", "driveId": "drive-1", "size": len(content)}),
        Response(200, content=content),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")
    with pytest.raises(AmbiguousOutcome):
        adapter.upload(io.BytesIO(content), "artifact-1", len(content), digest, "root-1")

    recovered = adapter.upload(io.BytesIO(content), "artifact-1", len(content), digest, "root-1")

    assert recovered.object_id == "artifact-1"
    upload_ids = [call[2]["data"].split(b'"id": "', 1)[1].split(b'"', 1)[0] for call in transport.calls if call[0] == "POST"]
    assert upload_ids == [b"artifact-1", b"artifact-1"]


def test_access_revocation_is_permission_failure_for_upload_list_and_download():
    content = b"read restricted"
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(403),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")
    with pytest.raises(PermissionFailure):
        adapter.upload(io.BytesIO(content), "reserved-file", len(content), hashlib.sha256(content).hexdigest(), "root-1")

    transport.responses = [Response(403)]
    with pytest.raises(PermissionFailure):
        adapter.list_children("root-1")

    transport.responses = [
        Response(200, {"id": "artifact-1", "parents": ["root-1"], "mimeType": "application/octet-stream", "driveId": "drive-1", "size": len(content)}),
        Response(403),
    ]
    with pytest.raises(PermissionFailure):
        adapter.download_to("artifact-1", io.BytesIO())
