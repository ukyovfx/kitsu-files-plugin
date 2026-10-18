"""Worker-only Google Drive credential and adapter construction.

OAuth refresh-token material stays in a protected, read-only secret directory;
only the opaque credential reference is stored in plugin metadata.
"""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

from .base import PermissionFailure, RetryableStorageFailure
from .google_drive import DriveConfig, GoogleDriveStorageAdapter, _retry_after_seconds


TOKEN_URL = "https://oauth2.googleapis.com/token"
_CREDENTIAL_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REQUIRED_SECRET_FIELDS = ("client_id", "client_secret", "refresh_token")


class DriveCredentialError(PermissionFailure):
    """Opaque fail-closed error for unavailable or invalid Drive credentials."""


def validate_credentials_directory(credentials_dir):
    path = Path(credentials_dir)
    try:
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            raise DriveCredentialError("Drive credential directory is unavailable")
        if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise DriveCredentialError("Drive credential directory permissions are too broad")
    except OSError:
        raise DriveCredentialError("Drive credential directory is unavailable") from None
    return path


class RequestsDriveTransport:
    """Small requests transport with TLS verification and redirects disabled."""

    def __init__(self, session=None, *, timeout=(10, 120)):
        if session is None:
            import requests

            session = requests.Session()
        if hasattr(session, "trust_env"):
            session.trust_env = False
        self.session = session
        self.timeout = timeout

    def request(self, method, url, **kwargs):
        return self.session.request(
            method,
            url,
            timeout=self.timeout,
            allow_redirects=False,
            **kwargs,
        )


class DriveCredentialTokenProvider:
    """Resolve one credential reference and refresh short-lived access tokens."""

    def __init__(self, credentials_dir, credential_ref, transport, *, clock=time.monotonic):
        self.credentials_dir = Path(credentials_dir)
        self.credential_ref = credential_ref
        self.transport = transport
        self.clock = clock
        self._lock = threading.RLock()
        self._secret_fingerprint = None
        self._access_token = None
        self._expires_at = 0.0

    def __call__(self):
        with self._lock:
            secret_bytes = self._read_secret()
            fingerprint = sha256(secret_bytes).digest()
            if fingerprint != self._secret_fingerprint:
                self._secret_fingerprint = fingerprint
                self._access_token = None
                self._expires_at = 0.0
            if self._access_token and self.clock() < self._expires_at:
                return self._access_token
            return self._refresh(json.loads(secret_bytes))

    def refresh_after_unauthorized(self):
        with self._lock:
            secret_bytes = self._read_secret()
            fingerprint = sha256(secret_bytes).digest()
            self._secret_fingerprint = fingerprint
            self._access_token = None
            self._expires_at = 0.0
            return self._refresh(json.loads(secret_bytes))

    def _read_secret(self):
        if not isinstance(self.credential_ref, str) or not _CREDENTIAL_REF.fullmatch(self.credential_ref):
            raise DriveCredentialError("Drive credential reference is invalid")
        try:
            if not self.credentials_dir.is_absolute():
                raise DriveCredentialError("Drive credential directory must be absolute")
            if self.credentials_dir.is_symlink() or not self.credentials_dir.is_dir():
                raise DriveCredentialError("Drive credential directory is unavailable")
            if os.name == "posix" and stat.S_IMODE(self.credentials_dir.stat().st_mode) & 0o077:
                raise DriveCredentialError("Drive credential directory permissions are too broad")
            secret_path = self.credentials_dir / f"{self.credential_ref}.json"
            if secret_path.parent.resolve(strict=True) != self.credentials_dir.resolve(strict=True):
                raise DriveCredentialError("Drive credential reference is invalid")
            if secret_path.is_symlink():
                raise DriveCredentialError("Drive credential is unavailable")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(secret_path, flags)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise DriveCredentialError("Drive credential is unavailable")
                if os.name == "posix":
                    if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid():
                        raise DriveCredentialError("Drive credential permissions are too broad")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    content = stream.read(16 * 1024 + 1)
            finally:
                os.close(descriptor)
            if len(content) > 16 * 1024:
                raise DriveCredentialError("Drive credential is invalid")
            secret = json.loads(content)
            if not isinstance(secret, dict) or any(
                not isinstance(secret.get(field), str) or not secret[field]
                for field in _REQUIRED_SECRET_FIELDS
            ):
                raise DriveCredentialError("Drive credential is incomplete")
            return content
        except DriveCredentialError:
            raise
        except (OSError, ValueError, TypeError):
            raise DriveCredentialError("Drive credential is unavailable or invalid") from None

    def _refresh(self, secret):
        form = {field: secret[field] for field in _REQUIRED_SECRET_FIELDS}
        form["grant_type"] = "refresh_token"
        try:
            response = self.transport.request(
                "POST",
                TOKEN_URL,
                data=form,
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception:
            raise RetryableStorageFailure(30, "Google OAuth token service is temporarily unavailable") from None
        if response.status_code == 429 or 500 <= response.status_code <= 599:
            raise RetryableStorageFailure(_retry_after_seconds(response), "Google OAuth token service requested a retry")
        if response.status_code in (400, 401, 403):
            raise DriveCredentialError("Google Drive credential was rejected")
        if response.status_code != 200:
            raise DriveCredentialError("Google OAuth token response is invalid")
        try:
            data = response.json()
            access_token = data["access_token"]
            expires_value = data["expires_in"]
            if isinstance(expires_value, bool) or not isinstance(expires_value, (int, float)):
                raise ValueError
            expires_in = int(expires_value)
        except (ValueError, TypeError, KeyError):
            raise DriveCredentialError("Google OAuth token response is invalid") from None
        if not isinstance(access_token, str) or not access_token or expires_in <= 0:
            raise DriveCredentialError("Google OAuth token response is invalid")
        self._access_token = access_token
        self._expires_at = self.clock() + max(0, expires_in - min(60, expires_in // 10))
        return access_token


def build_google_drive_adapter(connection, credentials_dir, *, transport=None):
    if not getattr(connection, "enabled", True) or connection.provider != "google_drive":
        raise DriveCredentialError("Google Drive storage connection is unavailable")
    if not connection.allowed_root_id or not connection.credential_ref:
        raise DriveCredentialError("Google Drive storage connection is incomplete")
    credentials_dir = validate_credentials_directory(credentials_dir)
    adapter_transport = transport or RequestsDriveTransport()
    token_provider = DriveCredentialTokenProvider(
        credentials_dir,
        connection.credential_ref,
        adapter_transport,
    )
    config = DriveConfig(
        account_mode=connection.account_mode,
        allowed_root_id=connection.allowed_root_id,
        drive_id=connection.drive_id,
        owner_subject=connection.owner_subject,
    )
    return GoogleDriveStorageAdapter(config, token_provider, adapter_transport)


def make_worker_storage_adapter_factory(
    storage_mode,
    *,
    fake_storage_path=None,
    fake_root_id="synthetic-root-id",
    fake_drive_id=None,
    credentials_dir=None,
    transport=None,
    session_factory=None,
    settings=None,
    extensions=None,
    allow_legacy_credentials=False,
):
    """Build an adapter factory that selects credentials and root per DB connection."""
    if storage_mode == "fake":
        if not fake_storage_path:
            raise DriveCredentialError("FakeStorage root is not configured")

        def fake_for_connection(connection):
            if not connection.enabled or connection.provider != "google_drive":
                raise DriveCredentialError("Storage connection is unavailable")
            if connection.allowed_root_id != fake_root_id:
                raise DriveCredentialError("FakeStorage root does not match the Production root")
            from .fake import PersistentFakeStorage

            return PersistentFakeStorage(fake_storage_path, root_id=fake_root_id, drive_id=fake_drive_id)

        return fake_for_connection
    if storage_mode == "google_drive":
        if session_factory is not None and settings is not None and extensions is not None:
            def database_or_legacy(connection):
                if not connection.enabled or not connection.allowed_root_id or connection.allowed_root_id.startswith("__pending__:"):
                    raise DriveCredentialError("Google Drive storage connection is unavailable")
                from sqlalchemy import select
                from ..models import DriveCredential
                with session_factory() as session:
                    credential = session.scalar(select(DriveCredential).where(
                        DriveCredential.connection_id == connection.id,
                        DriveCredential.production_id == connection.production_id,
                    ))
                    if credential is not None:
                        if credential.state != "active":
                            raise DriveCredentialError("Google Drive credential is unavailable")
                        from ..integrations.oauth import build_database_drive_adapter
                        return build_database_drive_adapter(
                            connection, session_factory, settings, extensions, transport=transport,
                        )
                if not allow_legacy_credentials or not credentials_dir:
                    raise DriveCredentialError("Google Drive credentials are not configured")
                return build_google_drive_adapter(connection, credentials_dir, transport=transport)
            return database_or_legacy
        if not credentials_dir:
            raise DriveCredentialError("Drive credential directory must be an absolute configured path")
        validate_credentials_directory(credentials_dir)
        adapter_transport = transport or RequestsDriveTransport()
        return lambda connection: build_google_drive_adapter(connection, credentials_dir, transport=adapter_transport)
    raise DriveCredentialError("No supported Worker storage mode is configured")
