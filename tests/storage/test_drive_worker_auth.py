import hashlib
import io
import json
import os
from types import SimpleNamespace

import pytest

from plugin.storage.base import PermissionFailure, RetryableStorageFailure
from plugin.storage.google_drive import DriveConfig, GoogleDriveStorageAdapter, FOLDER_MIME
from plugin.storage.worker_factory import DriveCredentialError, DriveCredentialTokenProvider, build_google_drive_adapter


class Response:
    def __init__(self, status_code, data=None, headers=None):
        self.status_code = status_code
        self._data = data or {}
        self.headers = headers or {}

    def json(self):
        return self._data


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def credential_dir(tmp_path, ref="studio-drive"):
    root = tmp_path / "credentials"
    root.mkdir()
    if os.name == "posix":
        root.chmod(0o700)
    path = root / f"{ref}.json"
    path.write_text(json.dumps({
        "client_id": "synthetic-client-id",
        "client_secret": "synthetic-client-secret",
        "refresh_token": "synthetic-refresh-token",
    }))
    if __import__("os").name == "posix":
        path.chmod(0o600)
    return root, path


def test_token_provider_refreshes_and_reuses_unexpired_access_token(tmp_path):
    root, _ = credential_dir(tmp_path)
    transport = FakeTransport([Response(200, {"access_token": "synthetic-access-token", "expires_in": 3600})])
    provider = DriveCredentialTokenProvider(root, "studio-drive", transport, clock=lambda: 100.0)

    assert provider() == "synthetic-access-token"
    assert provider() == "synthetic-access-token"
    assert len(transport.calls) == 1
    method, url, data = transport.calls[0]
    assert (method, url) == ("POST", "https://oauth2.googleapis.com/token")
    assert data["data"]["grant_type"] == "refresh_token"


def test_rotated_secret_invalidates_cached_access_token(tmp_path):
    root, path = credential_dir(tmp_path)
    transport = FakeTransport([
        Response(200, {"access_token": "access-a", "expires_in": 3600}),
        Response(200, {"access_token": "access-b", "expires_in": 3600}),
    ])
    provider = DriveCredentialTokenProvider(root, "studio-drive", transport, clock=lambda: 100.0)
    assert provider() == "access-a"
    path.write_text(path.read_text().replace("synthetic-refresh-token", "rotated-refresh-token"))
    assert provider() == "access-b"
    assert len(transport.calls) == 2


def test_credential_reference_cannot_escape_secret_directory(tmp_path):
    root, _ = credential_dir(tmp_path)
    provider = DriveCredentialTokenProvider(root, "../outside", FakeTransport([]))
    with pytest.raises(DriveCredentialError):
        provider()


def test_token_errors_do_not_expose_secret_values(tmp_path):
    root, _ = credential_dir(tmp_path)
    transport = FakeTransport([Response(400, {"error": "invalid_grant", "error_description": "synthetic-refresh-token leaked"})])
    provider = DriveCredentialTokenProvider(root, "studio-drive", transport, clock=lambda: 100.0)
    with pytest.raises(PermissionFailure) as raised:
        provider()
    assert "synthetic-refresh-token" not in str(raised.value)


def test_secret_reference_is_reloaded_when_worker_permission_is_revoked(tmp_path):
    root, path = credential_dir(tmp_path)
    transport = FakeTransport([Response(200, {"access_token": "access-a", "expires_in": 3600})])
    provider = DriveCredentialTokenProvider(root, "studio-drive", transport, clock=lambda: 100.0)
    assert provider() == "access-a"
    path.unlink()
    with pytest.raises(DriveCredentialError):
        provider()


@pytest.mark.skipif(os.name != "posix", reason="POSIX credential mode check")
def test_worker_rejects_world_readable_secret_file(tmp_path):
    root, path = credential_dir(tmp_path)
    path.chmod(0o644)
    provider = DriveCredentialTokenProvider(root, "studio-drive", FakeTransport([]), clock=lambda: 100.0)

    with pytest.raises(DriveCredentialError, match="permissions"):
        provider()


@pytest.mark.skipif(os.name != "posix", reason="POSIX credential mode check")
def test_worker_rejects_group_accessible_credential_directory(tmp_path):
    root, _ = credential_dir(tmp_path)
    root.chmod(0o750)
    provider = DriveCredentialTokenProvider(root, "studio-drive", FakeTransport([]), clock=lambda: 100.0)

    with pytest.raises(DriveCredentialError, match="permissions"):
        provider()


def test_worker_adapter_factory_uses_database_root_and_drive_scope(tmp_path):
    root, _ = credential_dir(tmp_path, "prod-a")
    transport = FakeTransport([])
    connection = SimpleNamespace(
        provider="google_drive", credential_ref="prod-a", account_mode="shared_drive",
        allowed_root_id="root-prod-a", drive_id="drive-prod-a", owner_subject=None,
    )
    adapter = build_google_drive_adapter(connection, root, transport=transport)
    assert adapter.config == DriveConfig("shared_drive", "root-prod-a", "drive-prod-a", None)
    assert adapter.token_provider.credentials_dir == root
    assert adapter.token_provider.credential_ref == "prod-a"


def test_my_drive_owner_uses_google_oidc_subject_from_authenticated_userinfo(tmp_path):
    root, _ = credential_dir(tmp_path, "prod-my-drive")
    transport = FakeTransport([
        Response(200, {"access_token": "synthetic-access", "expires_in": 3600}),
        Response(200, {"sub": "google-oidc-subject-123"}),
        Response(200, {"id": "root-my-drive", "mimeType": FOLDER_MIME, "capabilities": {"canAddChildren": True}}),
    ])
    connection = SimpleNamespace(
        enabled=True, provider="google_drive", credential_ref="prod-my-drive", account_mode="my_drive",
        allowed_root_id="root-my-drive", drive_id=None, owner_subject="google-oidc-subject-123",
    )
    adapter = build_google_drive_adapter(connection, root, transport=transport)

    capabilities = adapter.capabilities("root-my-drive")

    assert capabilities.root_id == "root-my-drive"
    assert transport.calls[1][0:2] == ("GET", "https://openidconnect.googleapis.com/v1/userinfo")
    assert transport.calls[1][2]["headers"]["Authorization"] == "Bearer synthetic-access"
    assert transport.calls[2][0:2] == ("GET", "https://www.googleapis.com/drive/v3/files/root-my-drive")


def test_my_drive_oidc_subject_mismatch_fails_before_root_metadata_read(tmp_path):
    root, _ = credential_dir(tmp_path, "prod-my-drive")
    transport = FakeTransport([
        Response(200, {"access_token": "synthetic-access", "expires_in": 3600}),
        Response(200, {"sub": "different-google-oidc-subject"}),
    ])
    connection = SimpleNamespace(
        enabled=True, provider="google_drive", credential_ref="prod-my-drive", account_mode="my_drive",
        allowed_root_id="root-my-drive", drive_id=None, owner_subject="google-oidc-subject-123",
    )
    adapter = build_google_drive_adapter(connection, root, transport=transport)

    with pytest.raises(PermissionFailure):
        adapter.capabilities("root-my-drive")
    assert len(transport.calls) == 2


def test_worker_adapter_factory_keeps_production_roots_and_credentials_separate(tmp_path):
    from plugin.storage.worker_factory import make_worker_storage_adapter_factory

    root, _ = credential_dir(tmp_path, "prod-a")
    other = tmp_path / "credentials" / "prod-b.json"
    other.write_text(json.dumps({"client_id": "client-b", "client_secret": "secret-b", "refresh_token": "refresh-b"}))
    if __import__("os").name == "posix":
        other.chmod(0o600)
    transport = FakeTransport([
        Response(200, {"access_token": "prod-a-access", "expires_in": 3600}),
        Response(200, {"access_token": "prod-b-access", "expires_in": 3600}),
    ])
    factory = make_worker_storage_adapter_factory("google_drive", credentials_dir=root, transport=transport)
    production_a = SimpleNamespace(
        enabled=True, provider="google_drive", credential_ref="prod-a", account_mode="shared_drive",
        allowed_root_id="root-a", drive_id="drive-a", owner_subject=None,
    )
    production_b = SimpleNamespace(
        enabled=True, provider="google_drive", credential_ref="prod-b", account_mode="shared_drive",
        allowed_root_id="root-b", drive_id="drive-b", owner_subject=None,
    )
    adapter_a, adapter_b = factory(production_a), factory(production_b)

    assert adapter_a.config.allowed_root_id == "root-a"
    assert adapter_b.config.allowed_root_id == "root-b"
    assert adapter_a.token_provider() == "prod-a-access"
    assert adapter_b.token_provider() == "prod-b-access"
    assert transport.calls[0][2]["data"]["refresh_token"] == "synthetic-refresh-token"
    assert transport.calls[1][2]["data"]["refresh_token"] == "refresh-b"


def test_disabled_storage_connection_cannot_build_google_adapter(tmp_path):
    root, _ = credential_dir(tmp_path)
    connection = SimpleNamespace(
        enabled=False, provider="google_drive", credential_ref="studio-drive", account_mode="my_drive",
        allowed_root_id="root", drive_id=None, owner_subject="artist@example.invalid",
    )
    with pytest.raises(PermissionFailure):
        build_google_drive_adapter(connection, root, transport=FakeTransport([]))


def test_drive_adapter_retries_once_after_expired_access_token():
    class RefreshingToken:
        def __init__(self):
            self.values = iter(("expired-access", "fresh-access"))

        def __call__(self):
            return next(self.values)

        def refresh_after_unauthorized(self):
            return next(self.values)

    transport = FakeTransport([Response(401), Response(200, {"ids": ["reserved-id"]})])
    adapter = GoogleDriveStorageAdapter(DriveConfig("shared_drive", "root-1", "drive-1", None), RefreshingToken(), transport)

    assert adapter.reserve_id("task:uuid") == "reserved-id"
    assert [call[2]["headers"]["Authorization"] for call in transport.calls] == [
        "Bearer expired-access", "Bearer fresh-access"
    ]


def test_expired_google_access_token_is_refreshed_once_with_isolated_oauth_transport(tmp_path):
    root, _ = credential_dir(tmp_path)
    oauth = FakeTransport([
        Response(200, {"access_token": "access-before-expiry", "expires_in": 3600}),
        Response(200, {"access_token": "access-after-refresh", "expires_in": 3600}),
    ])
    drive_http = FakeTransport([Response(401), Response(200, {"ids": ["reserved-id"]})])
    provider = DriveCredentialTokenProvider(root, "studio-drive", oauth, clock=lambda: 100.0)
    adapter = GoogleDriveStorageAdapter(
        DriveConfig("shared_drive", "root-1", "drive-1", None), provider, drive_http
    )

    assert adapter.reserve_id("task:uuid") == "reserved-id"
    assert [call[2]["headers"]["Authorization"] for call in drive_http.calls] == [
        "Bearer access-before-expiry", "Bearer access-after-refresh"
    ]
    assert len(oauth.calls) == 2


def test_expired_token_during_streamed_upload_refreshes_then_requeues_without_replaying_body():
    class RefreshableToken:
        def __init__(self):
            self.refreshed = False

        def __call__(self):
            return "expired-access" if not self.refreshed else "fresh-access"

        def refresh_after_unauthorized(self):
            self.refreshed = True
            return "fresh-access"

    content = b"synthetic upload bytes"
    transport = FakeTransport([
        Response(200, {"id": "root-1", "mimeType": FOLDER_MIME, "driveId": "drive-1", "capabilities": {"canAddChildren": True}}),
        Response(401),
    ])
    adapter = GoogleDriveStorageAdapter(
        DriveConfig("shared_drive", "root-1", "drive-1", None), RefreshableToken(), transport
    )
    adapter.capabilities("root-1")

    with pytest.raises(RetryableStorageFailure) as raised:
        adapter.upload(io.BytesIO(content), "stable-provider-id", len(content), hashlib.sha256(content).hexdigest(), "root-1")

    assert raised.value.retry_after_seconds == 1
    assert len(transport.calls) == 2
    assert transport.calls[1][2]["headers"]["Authorization"] == "Bearer expired-access"
    assert content in transport.calls[1][2]["data"]


def test_retryable_provider_response_uses_retry_after_without_leaking_body(tmp_path):
    root, _ = credential_dir(tmp_path)
    oauth = FakeTransport([Response(200, {"access_token": "synthetic-access-token", "expires_in": 3600})])
    provider = DriveCredentialTokenProvider(root, "studio-drive", oauth, clock=lambda: 100.0)
    drive_http = FakeTransport([Response(429, {"error": "sensitive-provider-detail"}, {"Retry-After": "17"})])
    adapter = GoogleDriveStorageAdapter(DriveConfig("shared_drive", "root-1", "drive-1", None), provider, drive_http)

    with pytest.raises(RetryableStorageFailure) as raised:
        adapter.reserve_id("task:uuid")
    assert raised.value.retry_after_seconds == 17
    assert "sensitive-provider-detail" not in str(raised.value)


@pytest.mark.parametrize("status_code", [429, 503])
def test_transient_oauth_service_responses_become_durable_worker_retries(tmp_path, status_code):
    root, _ = credential_dir(tmp_path)
    transport = FakeTransport([Response(status_code, headers={"Retry-After": "23"})])
    provider = DriveCredentialTokenProvider(root, "studio-drive", transport, clock=lambda: 100.0)

    with pytest.raises(RetryableStorageFailure) as raised:
        provider()
    assert raised.value.retry_after_seconds == 23
    assert "synthetic-client-secret" not in str(raised.value)


def test_requests_transport_disables_redirects_and_sets_a_timeout():
    from plugin.storage.worker_factory import RequestsDriveTransport

    class Session:
        trust_env = True

        def request(self, method, url, **kwargs):
            return method, url, kwargs

    session = Session()
    result = RequestsDriveTransport(session, timeout=(3, 9)).request("GET", "https://www.googleapis.com/drive/v3/about")
    assert session.trust_env is False
    assert result[2]["allow_redirects"] is False
    assert result[2]["timeout"] == (3, 9)
