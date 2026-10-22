import hashlib
import io
import json

import pytest

from plugin.storage.base import AmbiguousOutcome, PermissionFailure, RetryableStorageFailure
from plugin.storage.google_drive import DriveConfig, GoogleDriveStorageAdapter, FOLDER_MIME


class Response:
    def __init__(self, status_code, data=None, content=b"", headers=None):
        self.status_code = status_code
        self._data = data or {}
        self.content = content
        self.headers = headers or {}

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
        owner_subject="google-oidc-subject-owner" if account_mode == "my_drive" else None,
    )
    return GoogleDriveStorageAdapter(config, lambda: "synthetic-token", transport)


def test_shared_drive_uses_drive_scoped_listing_and_support_flags():
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(200, {"files": [{"id": "folder-1", "name": "Seq 010", "parents": ["root-1"], "mimeType": FOLDER_MIME, "driveId": "drive-1", "modifiedTime": "2026-10-13T01:02:03.000Z", "md5Checksum": "abcd"}], "nextPageToken": "next"}),
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
    assert listing.items[0].modified_time == "2026-10-13T01:02:03.000Z"
    assert listing.items[0].md5_checksum == "abcd"
    assert "modifiedTime" in params["fields"] and "md5Checksum" in params["fields"]
    assert listing.complete is True
    assert listing.next_page_token == "next"


def test_get_preserves_resource_key_for_fail_closed_link_sharing():
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(200, {"id": "file-1", "name": "restricted.exr", "mimeType": "image/x-exr", "parents": ["root-1"], "driveId": "drive-1", "resourceKey": "synthetic-resource-key"}),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")

    result = adapter.get("file-1")

    assert result.resource_key == "synthetic-resource-key"
    assert "resourceKey" in transport.calls[1][2]["params"]["fields"]


def test_my_drive_owner_identity_must_match_configured_subject():
    transport = FakeTransport([Response(200, {"sub": "different-google-oidc-subject"})])
    adapter = drive(transport, "my_drive")
    with pytest.raises(PermissionFailure):
        adapter.capabilities("root-1")


def test_my_drive_email_or_drive_permission_id_cannot_substitute_for_oidc_subject():
    transport = FakeTransport([Response(200, {"user": {"permissionId": "google-oidc-subject-owner", "emailAddress": "owner@example.invalid"}})])
    adapter = drive(transport, "my_drive")

    with pytest.raises(PermissionFailure):
        adapter.capabilities("root-1")


def test_my_drive_root_candidates_require_verified_owner_and_are_bounded_to_top_level():
    transport = FakeTransport([
        Response(200, {"sub": "google-oidc-subject-owner"}),
        Response(200, {"files": [{
            "id": "folder-1", "name": "Show Root", "mimeType": FOLDER_MIME,
            "parents": ["root"], "trashed": False,
        }]}),
    ])
    adapter = drive(transport, "my_drive")

    listing = adapter.list_root_candidates()

    assert listing.complete is True
    assert [(item.object_id, item.name) for item in listing.items] == [("folder-1", "Show Root")]
    params = transport.calls[1][2]["params"]
    assert params["q"] == "'root' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    assert "pageSize" in params and "nextPageToken" in params["fields"]


def test_my_drive_root_listing_stops_before_drive_query_when_owner_subject_differs():
    transport = FakeTransport([Response(200, {"sub": "different-subject"})])
    adapter = drive(transport, "my_drive")

    with pytest.raises(PermissionFailure):
        adapter.list_root_candidates()
    assert len(transport.calls) == 1


def test_shared_drive_root_candidates_are_limited_to_configured_drive():
    transport = FakeTransport([Response(200, {"files": [{
        "id": "folder-1", "name": "Shared Root", "mimeType": FOLDER_MIME,
        "parents": ["drive-1"], "driveId": "drive-1",
    }]})])
    adapter = drive(transport)

    listing = adapter.list_root_candidates()

    assert [item.object_id for item in listing.items] == ["folder-1"]
    params = transport.calls[0][2]["params"]
    assert params["driveId"] == "drive-1"
    assert params["corpora"] == "drive"


def test_my_drive_candidate_verification_does_not_change_the_configured_root():
    transport = FakeTransport([
        Response(200, {"sub": "google-oidc-subject-owner"}),
        Response(200, {
            "id": "candidate-root", "name": "Candidate", "mimeType": FOLDER_MIME,
            "parents": ["parent-1"], "capabilities": {"canAddChildren": True},
        }),
    ])
    adapter = drive(transport, "my_drive")

    capabilities = adapter.verify_root_candidate("candidate-root")

    assert capabilities.root_id == "candidate-root"
    assert capabilities.root_visible is True
    assert adapter.config.allowed_root_id == "root-1"
    assert adapter._root_verified is False


def test_candidate_verification_rejects_shared_drive_root_mismatch():
    adapter = drive(FakeTransport([
        Response(200, {
            "id": "candidate-root", "mimeType": FOLDER_MIME,
            "driveId": "another-drive", "capabilities": {"canAddChildren": True},
        }),
    ]))

    with pytest.raises(PermissionFailure):
        adapter.verify_root_candidate("candidate-root")


def test_drive_404_is_ambiguous_not_proof_of_absence():
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(404),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")
    with pytest.raises(AmbiguousOutcome):
        adapter.get("unknown-object")


def test_drive_missing_size_is_unknown_not_a_zero_byte_file():
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(200, {"id": "object-1", "parents": ["root-1"], "mimeType": "application/octet-stream"}),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")
    assert adapter.get("object-1").size is None


def test_drive_403_does_not_retry_with_a_refreshed_token():
    class Refreshable:
        def __call__(self):
            return "synthetic-access-token"

        def refresh_after_unauthorized(self):
            raise AssertionError("403 permission revocation must not be retried")

    transport = FakeTransport([Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}), Response(403)])
    adapter = GoogleDriveStorageAdapter(
        DriveConfig("shared_drive", "root-1", "drive-1", None), Refreshable(), transport
    )
    adapter.capabilities("root-1")

    with pytest.raises(PermissionFailure):
        adapter.get("revoked-object")
    assert len(transport.calls) == 2


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_retry_after_on_provider_transient_is_surfaced_to_durable_worker(status_code):
    transport = FakeTransport([Response(status_code, headers={"Retry-After": "11"})])
    adapter = drive(transport)

    with pytest.raises(RetryableStorageFailure) as raised:
        adapter.reserve_id("publish:stable-id")

    assert raised.value.retry_after_seconds == 11


def test_malformed_non_finite_retry_after_uses_safe_default():
    transport = FakeTransport([Response(429, headers={"Retry-After": "Infinity"})])
    adapter = drive(transport)

    with pytest.raises(RetryableStorageFailure) as raised:
        adapter.reserve_id("publish:stable-id")

    assert raised.value.retry_after_seconds == 30


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
        Response(200, {"sub": "google-oidc-subject-owner"}),
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


def test_package_copy_upload_preserves_provider_filename_and_reserved_id():
    content = b"immutable package copy"
    digest = hashlib.sha256(content).hexdigest()
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(200, {"id": "package-folder", "name": "Package", "parents": ["root-1"], "mimeType": FOLDER_MIME, "driveId": "drive-1"}),
        Response(201, {"id": "target-file", "name": "plate.1001.exr", "parents": ["package-folder"], "mimeType": "application/octet-stream", "driveId": "drive-1", "size": len(content)}),
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")
    adapter.upload(io.BytesIO(content), "target-file", len(content), digest, "package-folder", file_name="plate.1001.exr")
    metadata = json.loads(transport.calls[2][2]["data"].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
    assert metadata == {"id": "target-file", "parents": ["package-folder"], "name": "plate.1001.exr"}


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


def _verified_adapter(responses):
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        *responses,
    ])
    adapter = drive(transport)
    adapter.capabilities("root-1")
    return adapter, transport


def test_delete_removes_exactly_one_object_by_id_with_shared_drive_support():
    adapter, transport = _verified_adapter([Response(204)])
    assert adapter.delete("file-9") is True
    method, url, kwargs = transport.calls[-1]
    assert method == "DELETE" and url.endswith("/files/file-9")
    assert kwargs["params"] == {"supportsAllDrives": "true"}
    assert kwargs.get("json") is None and kwargs.get("data") is None


def test_delete_404_is_reported_absent_and_get_404_is_object_not_found():
    from plugin.storage.base import ObjectNotFound

    adapter, _ = _verified_adapter([Response(404), Response(404)])
    assert adapter.delete("gone") is False
    with pytest.raises(ObjectNotFound):
        adapter.get("gone")
    assert issubclass(ObjectNotFound, AmbiguousOutcome)  # existing callers keep fail-closed behaviour


@pytest.mark.parametrize("status", [401, 403])
def test_delete_permission_failures_are_not_success_and_not_retried_blindly(status):
    adapter, transport = _verified_adapter([Response(status)])
    with pytest.raises(PermissionFailure):
        adapter.delete("file-9")
    assert [call[0] for call in transport.calls].count("DELETE") == 1


def test_delete_transport_and_server_errors_are_never_success():
    adapter, _ = _verified_adapter([RuntimeError("timeout"), Response(500), Response(418)])
    with pytest.raises(AmbiguousOutcome):
        adapter.delete("file-9")
    with pytest.raises(RetryableStorageFailure):
        adapter.delete("file-9")
    with pytest.raises(AmbiguousOutcome):
        adapter.delete("file-9")


def test_delete_refuses_the_allowed_root_and_unverified_roots_before_any_provider_io():
    adapter, transport = _verified_adapter([])
    calls = len(transport.calls)
    with pytest.raises(PermissionFailure):
        adapter.delete("root-1")
    with pytest.raises(PermissionFailure):
        adapter.delete("")
    assert len(transport.calls) == calls
    unverified = drive(FakeTransport([]))
    with pytest.raises(PermissionFailure):
        unverified.delete("file-9")
