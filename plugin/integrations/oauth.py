"""Plugin-owned confidential OAuth flow and encrypted Drive grant storage.

Plaintext refresh tokens and PKCE verifiers are confined to process memory.
Database ciphertext is authenticated with identity-bound AES-GCM AAD; key files
are provisioned separately from PostgreSQL and the application image.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
OAUTH_INTENT_TTL = timedelta(minutes=10)
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"


class OAuthOperationError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class CredentialKeyring:
    active_key_id: str
    keys: dict[str, bytes]

    def __post_init__(self):
        if not _KEY_ID.fullmatch(self.active_key_id) or self.active_key_id not in self.keys:
            raise ValueError("Credential keyring active key is invalid")
        if any(not _KEY_ID.fullmatch(k) or not isinstance(v, bytes) or len(v) != 32 for k, v in self.keys.items()):
            raise ValueError("Credential keyring must contain 256-bit keys")

    @classmethod
    def from_directory(cls, directory: str, active_key_id: str):
        path = Path(directory)
        try:
            # ACL inspection is platform-specific; fail closed rather than
            # accepting an unverified Windows ACL as an owner-only key store.
            if os.name != "posix":
                raise ValueError
            if not path.is_absolute() or path.is_symlink() or not path.is_dir():
                raise ValueError
            info = path.stat()
            if os.name == "posix" and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid()):
                raise ValueError
            keys = {}
            for key_path in path.glob("*.key"):
                key_id = key_path.stem
                if not _KEY_ID.fullmatch(key_id) or key_path.is_symlink():
                    raise ValueError
                fd = os.open(key_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    key_info = os.fstat(fd)
                    if not stat.S_ISREG(key_info.st_mode):
                        raise ValueError
                    if os.name == "posix" and (stat.S_IMODE(key_info.st_mode) & 0o077 or key_info.st_uid != os.geteuid()):
                        raise ValueError
                    with os.fdopen(fd, "rb", closefd=False) as stream:
                        key = stream.read(33)
                finally:
                    os.close(fd)
                if len(key) != 32:
                    raise ValueError
                keys[key_id] = key
            return cls(active_key_id, keys)
        except (OSError, ValueError, TypeError):
            raise OAuthOperationError(503, "credential_store_unavailable") from None

    def encrypt(self, value: dict, aad: bytes):
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.keys[self.active_key_id]).encrypt(nonce, _json_bytes(value), aad)
        return self.active_key_id, nonce, ciphertext

    def decrypt(self, key_id: str, nonce: bytes, ciphertext: bytes, aad: bytes):
        try:
            key = self.keys[key_id]
            value = json.loads(AESGCM(key).decrypt(nonce, ciphertext, aad))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except Exception:
            raise OAuthOperationError(503, "credential_store_unavailable") from None


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _aad(kind: str, production_id, connection_id, identity="") -> bytes:
    return f"vfx-files:{kind}:1:{production_id}:{connection_id}:{identity}".encode("utf-8")


def _utc(value: datetime) -> datetime:
    """Normalize database datetimes to aware UTC before comparisons.

    PostgreSQL preserves ``timezone=True`` while SQLite drops tzinfo. Keeping
    this normalization at the boundary makes the same expiry logic testable
    against both dialects without depending on host-local time.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _setting(settings, name):
    return settings.get(name) or os.environ.get(name)


def _fixed_https_uri(value, *, exact_path=None):
    if not isinstance(value, str) or len(value) > 2048:
        raise OAuthOperationError(503, "oauth_not_configured")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise OAuthOperationError(503, "oauth_not_configured") from None
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username or parsed.password or port
        or parsed.query or parsed.fragment or parsed.hostname != parsed.hostname.lower()
        or parsed.netloc != parsed.hostname
        or (exact_path is not None and parsed.path != exact_path)
    ):
        raise OAuthOperationError(503, "oauth_not_configured")
    return urlunsplit(("https", parsed.netloc, parsed.path, "", ""))


def validate_google_callback_query(args):
    """Accept only Google OAuth callback parameters with no duplicates."""
    allowed = {"state", "code", "error", "error_description", "error_uri", "scope", "authuser", "prompt"}
    if set(args.keys()) - allowed or any(len(args.getlist(key)) != 1 for key in args.keys()):
        raise OAuthOperationError(400, "authorization_invalid")
    state = args.get("state", "")
    code = args.get("code")
    error = args.get("error")
    if not isinstance(state, str) or not 32 <= len(state) <= 256:
        raise OAuthOperationError(400, "authorization_invalid")
    if (code is None) == (error is None):
        raise OAuthOperationError(400, "authorization_invalid")
    # Google returns error_description/error_uri only with an error. Their
    # values are deliberately ignored and never reflected to the caller.
    if error is None and ("error_description" in args or "error_uri" in args):
        raise OAuthOperationError(400, "authorization_invalid")
    if code is not None and (not isinstance(code, str) or not 1 <= len(code) <= 4096):
        raise OAuthOperationError(400, "authorization_invalid")
    if error is not None and (not isinstance(error, str) or not 1 <= len(error) <= 256):
        raise OAuthOperationError(400, "authorization_invalid")
    return state, code, error


def _oauth_settings(settings):
    client_id = _setting(settings, "VFX_FILES_GOOGLE_CLIENT_ID")
    client_secret = _setting(settings, "VFX_FILES_GOOGLE_CLIENT_SECRET")
    callback = _fixed_https_uri(_setting(settings, "VFX_FILES_GOOGLE_OAUTH_REDIRECT_URI"), exact_path="/api/plugins/vfx_files/oauth/google/callback")
    if not all(isinstance(value, str) and value and len(value) <= 2048 for value in (client_id, client_secret)):
        raise OAuthOperationError(503, "oauth_not_configured")
    return_uris = {}
    for surface, setting in (
        ("kitsusync", "VFX_FILES_KITSUSYNC_RETURN_URI"),
        ("kitsu_plugin", "VFX_FILES_KITSU_PLUGIN_RETURN_URI"),
    ):
        value = _setting(settings, setting)
        if value:
            return_uris[surface] = _fixed_https_uri(value)
    if not return_uris:
        raise OAuthOperationError(503, "oauth_not_configured")
    return client_id, client_secret, callback, return_uris


def _oauth_return_uri(settings, surface):
    if surface not in {"kitsusync", "kitsu_plugin"}:
        raise OAuthOperationError(400, "authorization_invalid")
    return_uri = _oauth_settings(settings)[3].get(surface)
    if return_uri is None:
        raise OAuthOperationError(503, "oauth_not_configured")
    return return_uri


def _keyring(settings, app_extensions):
    supplied = app_extensions.get("vfx_files_credential_keyring")
    if supplied is not None:
        return supplied
    directory = _setting(settings, "VFX_FILES_CREDENTIAL_KEY_DIR")
    active = _setting(settings, "VFX_FILES_CREDENTIAL_ACTIVE_KEY_ID")
    if not directory or not active:
        raise OAuthOperationError(503, "credential_store_unavailable")
    return CredentialKeyring.from_directory(directory, active)


def _advisory_lock(session, key):
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


def start_authorization(session, *, payload, actor_id, idempotency_key, settings, extensions, now=None):
    from sqlalchemy import select
    from ..models import (
        DriveAuthorizationIntent,
        DriveCredential,
        KitsuSyncDesiredConfiguration,
        ProductionDriveAuthority,
        StorageConnection,
    )

    if not isinstance(payload, dict) or set(payload) - {"contract_version", "production_id", "account_mode", "connection_id", "return_surface"}:
        raise OAuthOperationError(400, "invalid_event")
    if payload.get("contract_version") != "1.0" or payload.get("account_mode") not in {"my_drive", "shared_drive"}:
        raise OAuthOperationError(400, "invalid_event")
    return_surface = payload.get("return_surface", "kitsusync")
    if return_surface not in {"kitsusync", "kitsu_plugin"}:
        raise OAuthOperationError(400, "invalid_event")
    try:
        production_id = UUID(payload["production_id"])
        if str(production_id) != payload["production_id"]:
            raise ValueError
        connection_id = UUID(payload["connection_id"]) if payload.get("connection_id") else None
        if connection_id and str(connection_id) != payload["connection_id"]:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise OAuthOperationError(400, "invalid_event") from None
    if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128:
        raise OAuthOperationError(400, "invalid_event")
    client_id, _client_secret, callback, _return_uris = _oauth_settings(settings)
    _oauth_return_uri(settings, return_surface)
    keyring = _keyring(settings, extensions)
    current = datetime.now(timezone.utc) if now is None else now
    request = {"production_id": str(production_id), "account_mode": payload["account_mode"], "connection_id": str(connection_id) if connection_id else None, "return_surface": return_surface}
    request_sha256 = hashlib.sha256(_json_bytes(request)).hexdigest()
    try:
        with session.begin():
            _advisory_lock(session, f"vfx-files-oauth:{production_id}:{idempotency_key}")
            authority = session.scalar(select(ProductionDriveAuthority).where(
                ProductionDriveAuthority.production_id == production_id,
            ).with_for_update())
            if return_surface == "kitsu_plugin":
                _advisory_lock(session, f"vfx-files-config:{production_id}")
                authority = session.scalar(select(ProductionDriveAuthority).where(
                    ProductionDriveAuthority.production_id == production_id,
                ).with_for_update())
                if authority is not None and authority.owner != "plugin":
                    raise OAuthOperationError(409, "legacy_import_required")
                if authority is None:
                    if session.get(KitsuSyncDesiredConfiguration, production_id) is not None:
                        raise OAuthOperationError(409, "legacy_import_required")
                    existing_connection_ids = set(session.scalars(select(StorageConnection.id).where(
                        StorageConnection.production_id == production_id,
                    )).all())
                    native_connection_ids = set(session.scalars(select(DriveAuthorizationIntent.connection_id).where(
                        DriveAuthorizationIntent.production_id == production_id,
                        DriveAuthorizationIntent.return_surface == "kitsu_plugin",
                    )).all())
                    if existing_connection_ids - native_connection_ids:
                        raise OAuthOperationError(409, "legacy_import_required")
                    authority = ProductionDriveAuthority(
                        production_id=production_id, owner="plugin", state="unconfigured",
                        config_revision=0, activation_epoch=0, connection_id=None,
                        root_id=None, activation_enabled=False, updated_by=actor_id,
                    )
                    session.add(authority)
                    session.flush()
            elif authority is not None and authority.owner == "plugin":
                raise OAuthOperationError(409, "plugin_authority_active")
            existing_intent = session.scalar(select(DriveAuthorizationIntent).where(
                DriveAuthorizationIntent.production_id == production_id,
                DriveAuthorizationIntent.idempotency_key == idempotency_key,
            ).with_for_update())
            if existing_intent is not None:
                if existing_intent.actor_id != actor_id or existing_intent.request_sha256 != request_sha256:
                    raise OAuthOperationError(409, "idempotency_conflict")
                response = keyring.decrypt(existing_intent.key_id, existing_intent.response_nonce,
                    existing_intent.response_ciphertext, _aad("authorization-response", production_id, existing_intent.connection_id, actor_id))
                return response, 201
            authorization_id = uuid4()
            connection = None
            require_consent = False
            if connection_id:
                connection = session.scalar(select(StorageConnection).where(
                    StorageConnection.id == connection_id, StorageConnection.production_id == production_id,
                ).with_for_update())
                if connection is None or connection.account_mode != payload["account_mode"]:
                    raise OAuthOperationError(404, "connection_not_found")
                current_credential = session.get(DriveCredential, connection_id)
                if current_credential is not None and current_credential.state in {"revoking", "revoke_pending"}:
                    raise OAuthOperationError(409, "connection_not_ready")
                require_consent = current_credential is not None and current_credential.state in {"revoked", "reauth_required"}
            else:
                connection_id = authorization_id
                connection = StorageConnection(
                    id=connection_id, production_id=production_id, provider="google_drive",
                    account_mode=payload["account_mode"], allowed_root_id=f"__pending__:{connection_id}",
                    credential_ref=str(connection_id), enabled=False,
                )
                session.add(connection)
                session.flush()
            state = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
            params = {
                "client_id": client_id, "redirect_uri": callback, "response_type": "code",
                "scope": f"openid {_DRIVE_SCOPE}", "access_type": "offline",
                "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256",
            }
            if require_consent:
                # Ask Google for a replacement offline grant only when the
                # stored grant is known unusable or has been revoked.
                params["prompt"] = "consent"
            authorization_url = GOOGLE_AUTH_URL + "?" + urlencode(params)
            response = {
                "contract_version": "1.0", "authorization_id": str(authorization_id),
                "authorization_url": authorization_url,
                "expires_at": (current + OAUTH_INTENT_TTL).isoformat().replace("+00:00", "Z"),
            }
            key_id, verifier_nonce, verifier_ciphertext = keyring.encrypt({"verifier": verifier}, _aad("authorization-verifier", production_id, connection_id, actor_id))
            response_key_id, response_nonce, response_ciphertext = keyring.encrypt(response, _aad("authorization-response", production_id, connection_id, actor_id))
            if response_key_id != key_id:
                raise OAuthOperationError(503, "credential_store_unavailable")
            session.add(DriveAuthorizationIntent(
                id=authorization_id, production_id=production_id, actor_id=actor_id,
                connection_id=connection_id, account_mode=payload["account_mode"],
                return_surface=return_surface,
                state_sha256=hashlib.sha256(state.encode("ascii")).hexdigest(),
                key_id=key_id, verifier_nonce=verifier_nonce, verifier_ciphertext=verifier_ciphertext,
                response_nonce=response_nonce, response_ciphertext=response_ciphertext,
                idempotency_key=idempotency_key, request_sha256=request_sha256,
                status="pending", expires_at=current + OAUTH_INTENT_TTL,
            ))
            session.flush()
        return response, 201
    except OAuthOperationError:
        raise
    except Exception:
        raise OAuthOperationError(503, "oauth_intent_unavailable") from None


def _provider_request(transport, method, url, **kwargs):
    try:
        response = transport.request(method, url, **kwargs)
    except Exception:
        raise OAuthOperationError(503, "provider_unavailable") from None
    return response


class DatabaseDriveTokenProvider:
    """Refresh OAuth credentials under a DB row lock and cache only in memory."""

    refresh_after_unauthorized_accepts_token = True

    def __init__(self, session_factory, connection_id, keyring, client_id, client_secret, transport, *, clock=time.monotonic):
        self.session_factory = session_factory
        self.connection_id = UUID(str(connection_id))
        self.keyring = keyring
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport
        self.clock = clock
        self._lock = threading.RLock()
        self._token = None
        self._expires_at = 0

    def __call__(self):
        with self._lock:
            if self._token and self.clock() < self._expires_at:
                from ..models import DriveCredential
                from ..storage.base import PermissionFailure

                # A process-local token cache must not outlive explicit local
                # revocation/reauthorization state changes made by another
                # API or Worker process.
                with self.session_factory() as session:
                    credential = session.get(DriveCredential, self.connection_id)
                    if credential is not None and credential.state == "active":
                        return self._token
                self._token = None
                self._expires_at = 0
                raise PermissionFailure("Drive credential is unavailable")
            return self._refresh(force=False)

    def refresh_after_unauthorized(self, previous_token=None):
        with self._lock:
            self._token = None
            self._expires_at = 0
            return self._refresh(force=True, rejected_token=previous_token)

    def _refresh(self, *, force, rejected_token=None):
        from sqlalchemy import select
        from ..models import DriveCredential, StorageConnection
        from ..storage.base import PermissionFailure, RetryableStorageFailure

        invalid_grant = False
        try:
            with self.session_factory.begin() as session:
                credential = session.scalar(select(DriveCredential).where(
                    DriveCredential.connection_id == self.connection_id,
                ).with_for_update())
                if credential is None or credential.state != "active":
                    raise PermissionFailure("Drive credential is unavailable")
                secret = self.keyring.decrypt(
                    credential.key_id, credential.nonce, credential.ciphertext,
                    _aad("drive-credential", credential.production_id, credential.connection_id, credential.account_subject),
                )
                cached_token = secret.get("access_token")
                cached_expiry = secret.get("access_token_expires_at")
                try:
                    expiry = _utc(datetime.fromisoformat(cached_expiry))
                except (TypeError, ValueError):
                    expiry = None
                can_reuse_cached = not force or (
                    rejected_token is not None and cached_token != rejected_token
                )
                if (
                    can_reuse_cached and isinstance(cached_token, str) and cached_token
                    and expiry is not None and expiry > datetime.now(timezone.utc) + timedelta(seconds=30)
                ):
                    self._token = cached_token
                    self._expires_at = self.clock() + (expiry - datetime.now(timezone.utc)).total_seconds()
                    return self._token
                try:
                    response = _provider_request(self.transport, "POST", GOOGLE_TOKEN_URL,
                        data={"client_id": self.client_id, "client_secret": self.client_secret,
                              "refresh_token": secret["refresh_token"], "grant_type": "refresh_token"},
                        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
                except OAuthOperationError:
                    raise RetryableStorageFailure(30, "Google OAuth token service requested a retry")
                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    raise RetryableStorageFailure(30, "Google OAuth token service requested a retry")
                if response.status_code in (400, 401, 403):
                    credential.state = "reauth_required"
                    connection = session.get(StorageConnection, self.connection_id)
                    if connection is not None:
                        connection.enabled = False
                    from .storage_authority import fence_plugin_authority_after_credential_failure
                    fence_plugin_authority_after_credential_failure(
                        session, credential.production_id, self.connection_id,
                    )
                    from ..models import KitsuSyncDesiredConfiguration
                    desired = session.get(KitsuSyncDesiredConfiguration, credential.production_id)
                    if desired is not None and desired.desired_config.get("activation", {}).get("enabled"):
                        body = dict(desired.desired_config)
                        activation = dict(body.get("activation", {}))
                        activation.update({"enabled": False, "activated_at": None,
                                           "notification_epoch": desired.notification_epoch + 1})
                        body["activation"] = activation
                        desired.notification_epoch += 1
                        desired.config_revision += 1
                        desired.desired_config = body
                    invalid_grant = True
                elif response.status_code != 200:
                    raise PermissionFailure("Google OAuth token response is invalid")
                else:
                    data = response.json()
                    token, duration = data.get("access_token"), data.get("expires_in")
                    if not isinstance(token, str) or not 1 <= len(token) <= 8192 or isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
                        raise PermissionFailure("Google OAuth token response is invalid")
                    rotated_refresh = data.get("refresh_token")
                    if rotated_refresh is not None:
                        if not isinstance(rotated_refresh, str) or not rotated_refresh or len(rotated_refresh) > 8192:
                            raise PermissionFailure("Google OAuth token response is invalid")
                    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(duration))
                    new_secret = {
                        "refresh_token": rotated_refresh or secret["refresh_token"],
                        "access_token": token,
                        "access_token_expires_at": expires_at.isoformat(),
                    }
                    key_id, nonce, ciphertext = self.keyring.encrypt(
                        new_secret,
                        _aad("drive-credential", credential.production_id, credential.connection_id, credential.account_subject),
                    )
                    credential.key_id, credential.nonce, credential.ciphertext = key_id, nonce, ciphertext
                    self._token = token
                    self._expires_at = self.clock() + max(0, int(duration) - min(60, int(duration) // 10))
            if invalid_grant:
                raise PermissionFailure("Google Drive credential requires reauthorization")
            return self._token
        except (PermissionFailure, RetryableStorageFailure):
            raise
        except OAuthOperationError:
            raise RetryableStorageFailure(30, "Google OAuth token service is temporarily unavailable") from None
        except Exception:
            raise RetryableStorageFailure(30, "Google OAuth token service is temporarily unavailable") from None


def build_database_drive_adapter(connection, session_factory, settings, extensions, *, transport=None):
    """Build an adapter backed by one Production-bound encrypted grant."""
    from ..storage.google_drive import DriveConfig, GoogleDriveStorageAdapter
    from ..storage.worker_factory import RequestsDriveTransport

    client_id, client_secret, _callback, _return_uri = _oauth_settings(settings)
    keyring = _keyring(settings, extensions)
    adapter_transport = transport or RequestsDriveTransport(timeout=(10, 120))
    token_provider = DatabaseDriveTokenProvider(
        session_factory, connection.id, keyring, client_id, client_secret, adapter_transport,
    )
    return GoogleDriveStorageAdapter(
        DriveConfig(connection.account_mode, connection.allowed_root_id, connection.drive_id, connection.owner_subject),
        token_provider, adapter_transport,
    )


def complete_authorization(session_factory, *, state, code, oauth_error, settings, extensions, transport, now=None):
    from sqlalchemy import select
    from ..models import DriveAuthorizationIntent, DriveCredential, StorageConnection

    if not isinstance(state, str) or not 32 <= len(state) <= 256:
        raise OAuthOperationError(400, "authorization_invalid")
    state_sha256 = hashlib.sha256(state.encode("utf-8")).hexdigest()
    current = datetime.now(timezone.utc) if now is None else now
    with session_factory.begin() as session:
        intent = session.scalar(select(DriveAuthorizationIntent).where(
            DriveAuthorizationIntent.state_sha256 == state_sha256,
        ).with_for_update())
        if intent is None or not hmac.compare_digest(intent.state_sha256, state_sha256):
            raise OAuthOperationError(400, "authorization_invalid")
        if intent.status != "pending" or intent.consumed_at is not None:
            raise OAuthOperationError(409, "authorization_replayed")
        if _utc(intent.expires_at) <= _utc(current):
            intent.status = "expired"
            intent.consumed_at = current
            expired = True
        else:
            expired = False
        if expired:
            # Commit the expiry marker so the same short-lived state cannot
            # become usable again after a rolled-back callback.
            auth_id = intent.id
        else:
            intent.status = "processing"
            intent.consumed_at = current
            production_id, connection_id = intent.production_id, intent.connection_id
            account_mode, actor_id, auth_id = intent.account_mode, intent.actor_id, intent.id
            return_surface = getattr(intent, "return_surface", "kitsusync") or "kitsusync"
            key_id, nonce, ciphertext = intent.key_id, intent.verifier_nonce, intent.verifier_ciphertext
    if expired:
        raise OAuthOperationError(410, "authorization_expired")
    # Persist consumption before network I/O: a lost callback cannot replay a
    # provider code. The administrator can create a fresh bounded intent.
    keyring = _keyring(settings, extensions)
    verifier = keyring.decrypt(key_id, nonce, ciphertext, _aad("authorization-verifier", production_id, connection_id, actor_id))["verifier"]
    try:
        client_id, client_secret, callback, _return_uris = _oauth_settings(settings)
        return_uri = _oauth_return_uri(settings, return_surface)
        if oauth_error:
            raise OAuthOperationError(400, "authorization_denied")
        if not isinstance(code, str) or not 1 <= len(code) <= 4096:
            raise OAuthOperationError(400, "authorization_invalid")
        token_response = _provider_request(transport, "POST", GOOGLE_TOKEN_URL,
            data={"code": code, "client_id": client_id, "client_secret": client_secret,
                  "redirect_uri": callback, "grant_type": "authorization_code", "code_verifier": verifier},
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
        if token_response.status_code != 200:
            raise OAuthOperationError(400, "authorization_failed")
        token_body = token_response.json()
        access_token = token_body.get("access_token")
        refresh_token = token_body.get("refresh_token")
        returned_scopes = set(str(token_body.get("scope", "")).split())
        if (
            not isinstance(access_token, str) or not 1 <= len(access_token) <= 8192
            or returned_scopes != {"openid", _DRIVE_SCOPE}
        ):
            raise OAuthOperationError(400, "authorization_failed")
        account_response = _provider_request(transport, "GET", GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
        if account_response.status_code != 200:
            raise OAuthOperationError(403, "identity_unverified")
        subject = account_response.json().get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise OAuthOperationError(403, "identity_unverified")
        with session_factory.begin() as session:
            connection = session.scalar(select(StorageConnection).where(
                StorageConnection.id == connection_id,
                StorageConnection.production_id == production_id,
            ).with_for_update())
            intent = session.scalar(select(DriveAuthorizationIntent).where(DriveAuthorizationIntent.id == auth_id).with_for_update())
            if connection is None or intent is None or intent.status != "processing":
                raise OAuthOperationError(409, "authorization_replayed")
            old = session.get(DriveCredential, connection_id)
            if old is not None:
                if old.account_mode != account_mode or old.account_subject != subject:
                    raise OAuthOperationError(409, "account_mismatch")
                if not refresh_token and old.state in {"active", "revoking", "revoke_pending"}:
                    old_secret = keyring.decrypt(old.key_id, old.nonce, old.ciphertext,
                        _aad("drive-credential", production_id, connection_id, subject))
                    refresh_token = old_secret.get("refresh_token")
            if not refresh_token or len(refresh_token) > 8192:
                raise OAuthOperationError(400, "reauthorization_required")
            expires_in = token_body.get("expires_in")
            if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in <= 0:
                raise OAuthOperationError(400, "authorization_failed")
            secret = {
                "refresh_token": refresh_token,
                "access_token": access_token,
                "access_token_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat(),
            }
            stored_key_id, stored_nonce, stored_ciphertext = keyring.encrypt(
                secret, _aad("drive-credential", production_id, connection_id, subject),
            )
            credential = old or DriveCredential(connection_id=connection_id, production_id=production_id, account_mode=account_mode)
            credential.production_id = production_id
            credential.account_mode = account_mode
            credential.account_subject = subject
            credential.owner_subject = subject if account_mode == "my_drive" else None
            credential.key_id, credential.nonce, credential.ciphertext = stored_key_id, stored_nonce, stored_ciphertext
            credential.state = "active"
            session.add(credential)
            connection.account_mode = account_mode
            connection.credential_ref = str(connection_id)
            if account_mode == "my_drive":
                if connection.owner_subject and connection.owner_subject != subject:
                    raise OAuthOperationError(409, "account_mismatch")
                connection.owner_subject = subject
            intent.status = "connected"
            session.flush()
        return return_uri + ("&" if "?" in return_uri else "?") + urlencode({"authorization_id": str(auth_id), "status": "connected"})
    except OAuthOperationError as exc:
        with session_factory.begin() as session:
            intent = session.get(DriveAuthorizationIntent, auth_id)
            if intent is not None and intent.status == "processing":
                intent.status = "failed"
        raise exc
    except Exception:
        with session_factory.begin() as session:
            intent = session.get(DriveAuthorizationIntent, auth_id)
            if intent is not None and intent.status == "processing":
                intent.status = "failed"
        raise OAuthOperationError(400, "authorization_failed") from None


def connection_status(connection, credential=None):
    from .providers import connection_payload

    payload = connection_payload(connection)
    if credential is not None:
        if credential.state == "active":
            payload["state"] = "connected"
        elif credential.state in {"revoking", "revoke_pending"}:
            payload["state"] = credential.state
        elif credential.state == "reauth_required":
            payload["state"] = "expired"
        elif credential.state == "revoked":
            payload["state"] = "not_configured"
    return payload


def rotate_encrypted_records(session_factory, *, old_keyring: CredentialKeyring, new_keyring: CredentialKeyring):
    """Re-encrypt credentials and pending PKCE/response blobs in one DB transaction."""
    from sqlalchemy import select
    from ..models import DriveAuthorizationIntent, DriveCredential

    if old_keyring.active_key_id == new_keyring.active_key_id and old_keyring.keys[old_keyring.active_key_id] != new_keyring.keys[new_keyring.active_key_id]:
        raise OAuthOperationError(400, "invalid_key_rotation")
    changed = 0
    with session_factory.begin() as session:
        credentials = session.scalars(select(DriveCredential).where(DriveCredential.state != "revoked").with_for_update()).all()
        for row in credentials:
            identity = row.account_subject
            plain = old_keyring.decrypt(row.key_id, row.nonce, row.ciphertext,
                _aad("drive-credential", row.production_id, row.connection_id, identity))
            row.key_id, row.nonce, row.ciphertext = new_keyring.encrypt(
                plain, _aad("drive-credential", row.production_id, row.connection_id, identity),
            )
            changed += 1
        intents = session.scalars(select(DriveAuthorizationIntent).with_for_update()).all()
        for row in intents:
            verifier = old_keyring.decrypt(row.key_id, row.verifier_nonce, row.verifier_ciphertext,
                _aad("authorization-verifier", row.production_id, row.connection_id, row.actor_id))["verifier"]
            response = old_keyring.decrypt(row.key_id, row.response_nonce, row.response_ciphertext,
                _aad("authorization-response", row.production_id, row.connection_id, row.actor_id))
            row.key_id, row.verifier_nonce, row.verifier_ciphertext = new_keyring.encrypt(
                {"verifier": verifier}, _aad("authorization-verifier", row.production_id, row.connection_id, row.actor_id),
            )
            _key_id, row.response_nonce, row.response_ciphertext = new_keyring.encrypt(
                response, _aad("authorization-response", row.production_id, row.connection_id, row.actor_id),
            )
            changed += 1
        # Personal (per-user) Google grants and pending intents share this keyring and must rotate with it.
        try:
            from .user_oauth import rotate_personal_records
        except ImportError:  # minimal model stubs in focused tests
            rotate_personal_records = None
        if rotate_personal_records is not None:
            changed += rotate_personal_records(session, old_keyring=old_keyring, new_keyring=new_keyring)
    return changed


def rotate_configured_encrypted_records(session_factory, settings, extensions):
    """Rotate persisted OAuth envelopes using configured previous/active keys.

    Deployments stage both owner-only key files, set the active key ID and the
    previous key ID, run this once in the Zou application context, then remove
    the previous key only after row verification. The helper never prints key
    material and the transaction is all-or-nothing.
    """
    previous_key_id = _setting(settings, "VFX_FILES_CREDENTIAL_PREVIOUS_KEY_ID")
    new_keyring = _keyring(settings, extensions)
    if not isinstance(previous_key_id, str) or not _KEY_ID.fullmatch(previous_key_id):
        raise OAuthOperationError(503, "credential_store_unavailable")
    if previous_key_id == new_keyring.active_key_id or previous_key_id not in new_keyring.keys:
        raise OAuthOperationError(503, "credential_store_unavailable")
    old_keyring = CredentialKeyring(previous_key_id, new_keyring.keys)
    return rotate_encrypted_records(
        session_factory, old_keyring=old_keyring, new_keyring=new_keyring,
    )


def make_drive_credential_revoke_handler(session_factory, settings, extensions, *, transport=None):
    """Retry-safe durable Google grant revocation; local access is disabled first."""
    from sqlalchemy import func, select
    from ..jobs.queue import JobResult
    from ..models import DriveCredential, Job, StorageConnection
    from ..storage.worker_factory import RequestsDriveTransport

    provider = transport or RequestsDriveTransport(timeout=(10, 30))

    def handle(leased_job):
        try:
            payload = leased_job.payload
            connection_id = UUID(payload["connection_id"])
            production_id = UUID(payload["production_id"])
            if payload.get("job_type") != "revoke_drive_credential":
                raise ValueError
        except (KeyError, ValueError, TypeError):
            return JobResult("failed", error_code="invalid_revoke_job")
        try:
            keyring = _keyring(settings, extensions)
            with session_factory.begin() as session:
                now = session.scalar(select(func.now()))
                job = session.scalar(select(Job).where(
                    Job.id == leased_job.job_id,
                    Job.state == "leased", Job.lease_owner == leased_job.worker_id,
                    Job.lease_generation == leased_job.generation,
                    Job.lease_expires_at > now,
                ).with_for_update())
                credential = session.scalar(select(DriveCredential).where(
                    DriveCredential.connection_id == connection_id,
                    DriveCredential.production_id == production_id,
                ).with_for_update())
                connection = session.scalar(select(StorageConnection).where(
                    StorageConnection.id == connection_id,
                    StorageConnection.production_id == production_id,
                ).with_for_update())
                if job is None:
                    return JobResult("retry", error_code="revoke_lease_lost")
                if credential is None or credential.state == "revoked":
                    return JobResult("completed")
                if connection is None or credential.state not in {"revoking", "revoke_pending"}:
                    return JobResult("blocked_authorization", error_code="revoke_state_changed")
                if not credential.ciphertext:
                    return JobResult("reconcile_required", error_code="revoke_credential_unavailable")
                secret = keyring.decrypt(
                    credential.key_id, credential.nonce, credential.ciphertext,
                    _aad("drive-credential", production_id, connection_id, credential.account_subject),
                )
                token = secret.get("refresh_token")
                if not isinstance(token, str) or not token:
                    return JobResult("reconcile_required", error_code="revoke_credential_unavailable")
            try:
                response = provider.request("POST", GOOGLE_REVOKE_URL,
                    data={"token": token},
                    headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
            except Exception:
                return JobResult("retry", error_code="provider_temporarily_unavailable", retry_after_seconds=30)
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                return JobResult("retry", error_code="provider_temporarily_unavailable", retry_after_seconds=30)
            if response.status_code != 200:
                with session_factory.begin() as session:
                    current = session.get(DriveCredential, connection_id)
                    if current is not None and current.production_id == production_id and current.state == "revoking":
                        current.state = "revoke_pending"
                return JobResult("reconcile_required", error_code="provider_revoke_unconfirmed")
            with session_factory.begin() as session:
                now = session.scalar(select(func.now()))
                job = session.scalar(select(Job).where(
                    Job.id == leased_job.job_id, Job.state == "leased",
                    Job.lease_owner == leased_job.worker_id,
                    Job.lease_generation == leased_job.generation,
                    Job.lease_expires_at > now,
                ).with_for_update())
                current = session.scalar(select(DriveCredential).where(
                    DriveCredential.connection_id == connection_id,
                    DriveCredential.production_id == production_id,
                ).with_for_update())
                if job is None:
                    return JobResult("retry", error_code="revoke_lease_lost_after_provider")
                if current is not None and current.state in {"revoking", "revoke_pending"}:
                    current.state = "revoked"
                    current.ciphertext = None
                    connection = session.get(StorageConnection, connection_id)
                    if connection is not None:
                        connection.enabled = False
            return JobResult("completed")
        except OAuthOperationError:
            return JobResult("failed", error_code="credential_store_unavailable")
        except Exception:
            return JobResult("retry", error_code="revoke_operation_failed")

    return handle
