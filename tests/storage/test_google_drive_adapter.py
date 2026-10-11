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
        return self.responses.pop(0)


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
        Response(200, {"files": [{"id": "folder-1", "parents": ["root-1"], "mimeType": FOLDER_MIME, "driveId": "drive-1"}], "nextPageToken": "next"}),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")

    listing = adapter.list_children("root-1")

    assert listing.complete is False
    params = transport.calls[1][2]["params"]
    assert params["supportsAllDrives"] == "true"
    assert params["includeItemsFromAllDrives"] == "true"
    assert params["corpora"] == "drive"
    assert params["driveId"] == "drive-1"


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
