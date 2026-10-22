"""Personal (per Kitsu user) Google account linking.

Adapted from the reviewed parts of the unmerged per-user OAuth prototype (state/PKCE intent, encrypted grant,
revoke-then-erase), re-scoped for the integrated branch:

* the grant belongs to the Kitsu user; each authorization is started from, and re-checked against, one Production's
  allow/block setting;
* tokens never leave the server (only non-sensitive status is returned);
* nothing here is a Production storage credential: the Production OAuth tables and adapters are not touched.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID, uuid4

from .oauth import (
    GOOGLE_AUTH_URL,
    GOOGLE_REVOKE_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    OAUTH_INTENT_TTL,
    OAuthOperationError,
    _DRIVE_SCOPE,
    _aad,
    _advisory_lock,
    _json_bytes,
    _keyring,
    _oauth_settings,
    _provider_request,
    _utc,
)

PERSONAL_SCOPES = frozenset({"openid", _DRIVE_SCOPE})
POLICY_ALLOWED = "allowed"
POLICY_BLOCKED = "blocked"


class UserOAuthError(Exception):
    """A sanitized failure suitable for an API boundary."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise UserOAuthError(400, "invalid_actor") from None


def _translate(exc):
    if isinstance(exc, UserOAuthError):
        return exc
    if isinstance(exc, OAuthOperationError):
        return UserOAuthError(exc.status, exc.code)
    return UserOAuthError(503, "oauth_operation_unavailable")


def _intent_aad(actor_id, intent_id, kind):
    return _aad(f"user-oauth-{kind}", actor_id, intent_id, actor_id)


def _credential_aad(actor_id, google_subject):
    return f"vfx-files:user-drive-credential:1:{actor_id}:{google_subject}".encode("utf-8")


def _models():
    # Focused tests import the tree as ``plugin``; Zou installs it as ``vfx_files``.
    from importlib import import_module

    return import_module(f"{__package__.split('.', 1)[0]}.models")


def _encode(value):
    import base64

    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def get_personal_google_policy(session, production_id, *, lock=False) -> str:
    """`lock=True` takes a row share lock so a concurrent admin change waits for this transaction to commit."""
    from sqlalchemy import select

    setting = _models().ProductionOutputSetting
    if lock:
        row = session.scalar(select(setting).where(setting.production_id == production_id).with_for_update(read=True))
    else:
        row = session.get(setting, production_id)
    value = row.personal_google_policy if row is not None else POLICY_ALLOWED
    return value if value in (POLICY_ALLOWED, POLICY_BLOCKED) else POLICY_BLOCKED  # unknown values fail closed


def _policy_lock(session, production_id):
    """Transaction-scoped lock shared by policy writers and the grant-saving transaction.

    A row lock cannot serialize against a settings row that does not exist yet (first-ever change), so both sides
    take this advisory lock before reading or writing the policy.
    """
    _advisory_lock(session, f"vfx-files-personal-google-policy:{production_id}")


def set_personal_google_policy(session, production_id, policy, actor_id) -> None:
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy import func

    if policy not in (POLICY_ALLOWED, POLICY_BLOCKED):
        raise ValueError("Unsupported personal Google policy")
    _policy_lock(session, production_id)
    setting = _models().ProductionOutputSetting
    session.execute(insert(setting).values(
        production_id=production_id, personal_google_policy=policy, updated_by=actor_id,
    ).on_conflict_do_update(
        index_elements=[setting.production_id],
        set_={"personal_google_policy": policy, "updated_by": actor_id, "updated_at": func.now()},
    ))


def start_user_authorization(session, *, actor_id, production_id, idempotency_key, settings, extensions, now=None):
    """Create a ten-minute, one-use state+PKCE request for the Kitsu actor. Caller has authorized membership."""
    from sqlalchemy import func, select

    Intent = _models().GoogleUserAuthorizationIntent
    actor_id, production_id = _uuid(actor_id), _uuid(production_id)
    if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128:
        raise UserOAuthError(400, "invalid_idempotency_key")
    try:
        client_id, _secret, callback, return_uris = _oauth_settings(settings)
        if not return_uris.get("kitsu_plugin"):
            raise UserOAuthError(503, "oauth_not_configured")
        keyring = _keyring(settings, extensions)
        current = datetime.now(timezone.utc) if now is None else _utc(now)
        request_hash = hashlib.sha256(_json_bytes({
            "actor_id": str(actor_id), "production_id": str(production_id), "scope": sorted(PERSONAL_SCOPES),
        })).hexdigest()
        with session.begin():
            _advisory_lock(session, f"vfx-files-user-oauth:{actor_id}")
            if get_personal_google_policy(session, production_id) != POLICY_ALLOWED:
                raise UserOAuthError(403, "personal_google_blocked")
            existing = session.scalar(select(Intent).where(
                Intent.actor_id == actor_id, Intent.idempotency_key == idempotency_key,
            ).with_for_update())
            if existing is not None:
                if not hmac.compare_digest(existing.request_sha256, request_hash):
                    raise UserOAuthError(409, "idempotency_conflict")
                if existing.status != "pending" or _utc(existing.expires_at) <= current:
                    raise UserOAuthError(409, "authorization_intent_unavailable")
                response = keyring.decrypt(existing.key_id, existing.response_nonce, existing.response_ciphertext,
                                           _intent_aad(actor_id, existing.id, "response"))
                return response, 200
            active = session.scalar(select(func.count()).select_from(Intent).where(
                Intent.actor_id == actor_id, Intent.status == "pending", Intent.expires_at > current,
            )) or 0
            if active >= 3:
                raise UserOAuthError(429, "too_many_authorization_attempts")

            intent_id = uuid4()
            state = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            challenge = _encode(hashlib.sha256(verifier.encode("ascii")).digest())
            params = {
                "client_id": client_id, "redirect_uri": callback, "response_type": "code",
                "scope": f"openid {_DRIVE_SCOPE}", "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256",
                "access_type": "offline", "prompt": "select_account consent",
            }
            response = {
                "authorization_id": str(intent_id),
                "authorization_url": GOOGLE_AUTH_URL + "?" + urlencode(params),
                "expires_at": (current + OAUTH_INTENT_TTL).isoformat(),
            }
            key_id, verifier_nonce, verifier_ciphertext = keyring.encrypt(
                {"verifier": verifier}, _intent_aad(actor_id, intent_id, "verifier"))
            response_key_id, response_nonce, response_ciphertext = keyring.encrypt(
                response, _intent_aad(actor_id, intent_id, "response"))
            if key_id != response_key_id:
                raise UserOAuthError(503, "credential_store_unavailable")
            session.add(Intent(
                id=intent_id, actor_id=actor_id, production_id=production_id,
                state_sha256=hashlib.sha256(state.encode("ascii")).hexdigest(),
                key_id=key_id, verifier_nonce=verifier_nonce, verifier_ciphertext=verifier_ciphertext,
                response_nonce=response_nonce, response_ciphertext=response_ciphertext,
                idempotency_key=idempotency_key, request_sha256=request_hash,
                status="pending", expires_at=current + OAUTH_INTENT_TTL,
            ))
            session.flush()
        return response, 201
    except Exception as exc:
        raise _translate(exc) from None


def complete_user_authorization(session_factory, *, state, code, oauth_error, settings, extensions, transport, now=None):
    """Consume the state before provider I/O, exchange the code server-side, verify identity, encrypt the grant."""
    from sqlalchemy import select

    models = _models()
    Intent, Credential = models.GoogleUserAuthorizationIntent, models.GoogleUserCredential
    if not isinstance(state, str) or not 32 <= len(state) <= 256:
        raise UserOAuthError(400, "authorization_invalid")
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    current = datetime.now(timezone.utc) if now is None else _utc(now)
    intent_id = None
    try:
        with session_factory.begin() as session:
            intent = session.scalar(select(Intent).where(Intent.state_sha256 == state_hash).with_for_update())
            if intent is None or not hmac.compare_digest(intent.state_sha256, state_hash):
                raise UserOAuthError(400, "authorization_invalid")
            if intent.status != "pending" or intent.consumed_at is not None:
                raise UserOAuthError(409, "authorization_replayed")
            actor_id, intent_id, production_id = intent.actor_id, intent.id, intent.production_id
            key_id, nonce, ciphertext = intent.key_id, intent.verifier_nonce, intent.verifier_ciphertext
            intent.consumed_at = current
            if _utc(intent.expires_at) <= current:
                intent.status = "expired"
                expired = True
            else:
                intent.status = "processing"
                expired = False
            blocked = get_personal_google_policy(session, production_id) != POLICY_ALLOWED
        if expired:
            raise UserOAuthError(410, "authorization_expired")
        if blocked:  # the admin blocked personal linking after this attempt began: no grant is created
            raise UserOAuthError(403, "personal_google_blocked")
        keyring = _keyring(settings, extensions)
        verifier = keyring.decrypt(key_id, nonce, ciphertext, _intent_aad(actor_id, intent_id, "verifier"))["verifier"]
        if oauth_error:
            raise UserOAuthError(400, "authorization_denied")
        if not isinstance(code, str) or not 1 <= len(code) <= 4096:
            raise UserOAuthError(400, "authorization_invalid")
        client_id, client_secret, callback, return_uris = _oauth_settings(settings)
        return_uri = return_uris.get("kitsu_plugin")
        if not return_uri:
            raise UserOAuthError(503, "oauth_not_configured")
        token_response = _provider_request(
            transport, "POST", GOOGLE_TOKEN_URL,
            data={"code": code, "client_id": client_id, "client_secret": client_secret,
                  "redirect_uri": callback, "grant_type": "authorization_code", "code_verifier": verifier},
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
        if token_response.status_code != 200:
            raise UserOAuthError(400, "authorization_failed")
        body = token_response.json()
        access_token, refresh_token = body.get("access_token"), body.get("refresh_token")
        expires_in = body.get("expires_in")
        if (not isinstance(access_token, str) or not 1 <= len(access_token) <= 8192
                or set(str(body.get("scope", "")).split()) != PERSONAL_SCOPES
                or not isinstance(expires_in, (int, float)) or isinstance(expires_in, bool) or expires_in <= 0):
            raise UserOAuthError(400, "authorization_failed")
        identity = _provider_request(transport, "GET", GOOGLE_USERINFO_URL,
                                     headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
        if identity.status_code != 200:
            raise UserOAuthError(403, "identity_unverified")
        subject = identity.json().get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise UserOAuthError(403, "identity_unverified")
        with session_factory.begin() as session:
            intent = session.scalar(select(Intent).where(Intent.id == intent_id, Intent.status == "processing").with_for_update())
            credential = session.scalar(select(Credential).where(Credential.actor_id == actor_id).with_for_update())
            if intent is None:
                raise UserOAuthError(409, "authorization_replayed")
            # The admin may have blocked personal Google while we were talking to Google. Re-check in the saving
            # transaction under a share lock: a concurrent block now waits for this commit instead of slipping past it.
            _policy_lock(session, production_id)
            if get_personal_google_policy(session, production_id, lock=True) != POLICY_ALLOWED:
                refused_token = refresh_token if (credential is None or credential.state == "revoked") else None
                raise _RefusedAfterExchange(403, "personal_google_blocked", refused_token)
            if credential is not None and credential.state in {"revoking", "revoke_pending"}:
                raise UserOAuthError(409, "account_disconnect_in_progress")
            if credential is not None and credential.state != "revoked" and credential.google_subject != subject:
                raise UserOAuthError(409, "account_switch_requires_disconnect")
            owner = session.scalar(select(Credential.actor_id).where(Credential.google_subject == subject))
            if owner is not None and owner != actor_id:
                raise UserOAuthError(409, "google_account_already_linked")
            if not isinstance(refresh_token, str) or not 1 <= len(refresh_token) <= 8192:
                if credential is None or credential.state not in ("active", "reauth_required") or credential.google_subject != subject:
                    raise UserOAuthError(400, "reauthorization_required")
                previous = keyring.decrypt(credential.key_id, credential.nonce, credential.ciphertext,
                                           _credential_aad(actor_id, subject))
                refresh_token = previous.get("refresh_token")
                if not isinstance(refresh_token, str) or not refresh_token:
                    raise UserOAuthError(400, "reauthorization_required")
            enc_key, enc_nonce, enc = keyring.encrypt({
                "refresh_token": refresh_token, "access_token": access_token,
                "access_token_expires_at": (current + timedelta(seconds=int(expires_in))).isoformat(),
            }, _credential_aad(actor_id, subject))
            if credential is None:
                credential = Credential(actor_id=actor_id)
            credential.google_subject = subject
            credential.key_id, credential.nonce, credential.ciphertext = enc_key, enc_nonce, enc
            credential.granted_scopes = "openid " + _DRIVE_SCOPE
            credential.state = "active"
            session.add(credential)
            intent.status = "connected"
        return return_uri + ("&" if "?" in return_uri else "?") + urlencode(
            {"authorization_id": str(intent_id), "personal_google": "connected"})
    except Exception as exc:
        if intent_id is not None:
            try:
                with session_factory.begin() as session:
                    stuck = session.get(Intent, intent_id)
                    if stuck is not None and stuck.status == "processing":
                        stuck.status = "failed"
            except Exception:
                pass
        if isinstance(exc, _RefusedAfterExchange) and exc.token:
            # Google already issued a grant that we refuse to keep and never stored: do not leave it live there.
            try:
                _provider_request(transport, "POST", GOOGLE_REVOKE_URL, data={"token": exc.token},
                                  headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
            except Exception:
                pass
        raise _translate(exc) from None


class _RefusedAfterExchange(UserOAuthError):
    def __init__(self, status, code, token):
        super().__init__(status, code)
        self.token = token


REVOKE_JOB_TYPE = "revoke_personal_google"
_REVOKE_RETRY_SECONDS = 300


def revoke_job_key(actor_id) -> str:
    return f"revoke-personal-google:{actor_id}"


def enqueue_personal_revoke(session_factory, actor_id):
    """Queue (or re-queue) the durable retry for a `revoke_pending` grant. One job per user; idempotent."""
    from sqlalchemy import func, select

    Job = _models().Job
    actor_id = _uuid(actor_id)
    with session_factory.begin() as session:
        job = session.scalar(select(Job).where(Job.idempotency_key == revoke_job_key(actor_id)).with_for_update())
        if job is None:
            session.add(Job(job_type=REVOKE_JOB_TYPE, idempotency_key=revoke_job_key(actor_id), state="queued",
                            payload={"job_type": REVOKE_JOB_TYPE, "actor_id": str(actor_id)}))
        elif job.state in ("completed", "failed", "reconcile_required", "blocked_authorization") or (
                job.attempts >= job.max_attempts and job.state in ("retry", "queued")):
            job.state, job.attempts, job.last_error_code = "queued", 0, None
            job.lease_owner, job.lease_expires_at, job.available_at = None, None, func.now()


def make_personal_revoke_handler(session_factory, settings, extensions, *, transport=None):
    """Durable, retry-safe completion of an unfinished personal disconnect (reuses the shared Job queue)."""
    from ..jobs.queue import JobResult
    from ..storage.worker_factory import RequestsDriveTransport

    provider = transport or RequestsDriveTransport(timeout=(10, 30))

    def handle(leased):
        try:
            actor_id = _uuid(leased.payload.get("actor_id"))
        except UserOAuthError:
            return JobResult("failed", error_code="invalid_revoke_payload")
        try:
            result = disconnect_user_drive_account(
                session_factory, actor_id=actor_id, settings=settings, extensions=extensions, transport=provider,
                schedule_retry=False)
        except UserOAuthError:
            return JobResult("retry", error_code="personal_revoke_unavailable", retry_after_seconds=_REVOKE_RETRY_SECONDS)
        if result.get("state") == "revoked":
            return JobResult("completed")
        return JobResult("retry", error_code="personal_revoke_pending", retry_after_seconds=_REVOKE_RETRY_SECONDS)

    return handle


def disconnect_user_drive_account(session_factory, *, actor_id, settings, extensions, transport, schedule_retry=True):
    """Disable local use first; erase the grant only after Google confirms revoke. Safe to repeat.

    If Google cannot confirm now, the grant stays `revoke_pending` (unusable) and a durable retry Job is queued;
    repeating this call (the Disconnect button) also re-queues a Job that ran out of attempts.
    """
    from sqlalchemy import select

    Credential = _models().GoogleUserCredential
    actor_id = _uuid(actor_id)
    result = _disconnect_once(session_factory, actor_id=actor_id, settings=settings, extensions=extensions,
                              transport=transport, Credential=Credential)
    if result.get("state") == "revoke_pending" and schedule_retry:
        enqueue_personal_revoke(session_factory, actor_id)
    return result


def _disconnect_once(session_factory, *, actor_id, settings, extensions, transport, Credential):
    from sqlalchemy import select

    def mark(state):
        with session_factory.begin() as session:
            current = session.scalar(select(Credential).where(Credential.actor_id == actor_id).with_for_update())
            if current is not None and current.state in ("revoking", "revoke_pending"):
                current.state = state

    try:
        with session_factory.begin() as session:
            row = session.scalar(select(Credential).where(Credential.actor_id == actor_id).with_for_update())
            if row is None or row.state == "revoked":
                return {"state": "revoked"}
            row.state = "revoking"
            key_id, nonce, ciphertext, subject = row.key_id, row.nonce, row.ciphertext, row.google_subject
        try:
            refresh_token = _keyring(settings, extensions).decrypt(
                key_id, nonce, ciphertext, _credential_aad(actor_id, subject)).get("refresh_token")
        except Exception:
            refresh_token = None
        if not isinstance(refresh_token, str) or not refresh_token:
            mark("revoke_pending")
            return {"state": "revoke_pending"}
        try:
            response = _provider_request(transport, "POST", GOOGLE_REVOKE_URL, data={"token": refresh_token},
                                         headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
        except Exception:
            response = None
        with session_factory.begin() as session:
            row = session.scalar(select(Credential).where(Credential.actor_id == actor_id).with_for_update())
            if row is None:
                return {"state": "revoked"}
            # Google answers 400 for a token that is already invalid/revoked: the goal (no live grant) is met.
            if response is None or response.status_code not in (200, 400):
                row.state = "revoke_pending"
                return {"state": "revoke_pending"}
            row.google_subject = None
            row.key_id = row.nonce = row.ciphertext = row.granted_scopes = None
            row.state = "revoked"
            return {"state": "revoked"}
    except Exception as exc:
        raise _translate(exc) from None


def user_drive_status(session, *, actor_id):
    """Only the caller's non-sensitive link state; never tokens, never the full Google subject."""
    row = session.get(_models().GoogleUserCredential, _uuid(actor_id))
    if row is None or row.state == "revoked":
        return {"linked": False, "state": "not_linked"}
    return {
        "linked": row.state == "active", "state": row.state,
        "account_hint": f"…{row.google_subject[-4:]}" if row.google_subject else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def check_user_connection(session_factory, *, actor_id, settings, extensions, transport):
    """Force a token refresh to detect Google-side revocation; marks the grant `reauth_required` if rejected."""
    from ..storage.base import PermissionFailure, RetryableStorageFailure

    provider = build_user_drive_token_provider(session_factory, actor_id, settings, extensions, transport=transport)
    try:
        provider.refresh_after_unauthorized()
        result = "active"
    except PermissionFailure:
        result = "reauth_required"
    except RetryableStorageFailure:
        result = "unavailable"
    with session_factory() as session:
        status = user_drive_status(session, actor_id=actor_id)
    return {**status, "check": result}


class DatabaseUserDriveTokenProvider:
    """Refresh one user's encrypted grant for server-side, read-only Drive calls. Never returned to a browser."""

    refresh_after_unauthorized_accepts_token = True

    def __init__(self, session_factory, actor_id, keyring, client_id, client_secret, transport, *, clock=time.monotonic):
        self.session_factory = session_factory
        self.actor_id = _uuid(actor_id)
        self.keyring, self.client_id, self.client_secret = keyring, client_id, client_secret
        self.transport, self.clock = transport, clock
        self._lock = threading.RLock()
        self._token = None
        self._expires_at = 0.0

    def __call__(self):
        from ..storage.base import PermissionFailure

        with self._lock:
            if self._token and self.clock() < self._expires_at:
                with self.session_factory() as session:
                    row = session.get(_models().GoogleUserCredential, self.actor_id)
                    if row is not None and row.state == "active":
                        return self._token
                self._token, self._expires_at = None, 0.0
                raise PermissionFailure("User Drive credential is unavailable")
            return self._refresh()

    def refresh_after_unauthorized(self, previous_token=None):
        with self._lock:
            self._token, self._expires_at = None, 0.0
            return self._refresh(force=True, rejected_token=previous_token)

    def _refresh(self, *, force=False, rejected_token=None):
        from sqlalchemy import select
        from ..storage.base import PermissionFailure, RetryableStorageFailure

        Credential = _models().GoogleUserCredential
        try:
            with self.session_factory.begin() as session:
                credential = session.scalar(select(Credential).where(Credential.actor_id == self.actor_id).with_for_update())
                if (credential is None or credential.state != "active"
                        or set((credential.granted_scopes or "").split()) != PERSONAL_SCOPES):
                    raise PermissionFailure("User Drive credential is unavailable")
                secret = self.keyring.decrypt(credential.key_id, credential.nonce, credential.ciphertext,
                                              _credential_aad(self.actor_id, credential.google_subject))
                cached = secret.get("access_token")
                try:
                    expiry = _utc(datetime.fromisoformat(secret.get("access_token_expires_at", "")))
                except (TypeError, ValueError):
                    expiry = None
                reusable = not force or (rejected_token is not None and cached != rejected_token)
                if reusable and isinstance(cached, str) and cached and expiry and expiry > datetime.now(timezone.utc) + timedelta(seconds=30):
                    self._token = cached
                    self._expires_at = self.clock() + max(0, (expiry - datetime.now(timezone.utc)).total_seconds() - 20)
                    return self._token
                refresh_token = secret.get("refresh_token")
                invalid_grant = False
                if not isinstance(refresh_token, str) or not refresh_token:
                    credential.state, invalid_grant = "reauth_required", True
                else:
                    response = _provider_request(
                        self.transport, "POST", GOOGLE_TOKEN_URL,
                        data={"client_id": self.client_id, "client_secret": self.client_secret,
                              "refresh_token": refresh_token, "grant_type": "refresh_token"},
                        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
                    if response.status_code in (400, 401, 403):
                        credential.state, invalid_grant = "reauth_required", True
                    elif response.status_code == 429 or 500 <= response.status_code <= 599:
                        raise RetryableStorageFailure(30, "Google OAuth token service requested a retry")
                    elif response.status_code != 200:
                        raise PermissionFailure("Google OAuth token response is invalid")
                    else:
                        data = response.json()
                        token, duration = data.get("access_token"), data.get("expires_in")
                        rotated = data.get("refresh_token", refresh_token)
                        if (not isinstance(token, str) or not 1 <= len(token) <= 8192
                                or isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0
                                or not isinstance(rotated, str) or not 1 <= len(rotated) <= 8192):
                            raise PermissionFailure("Google OAuth token response is invalid")
                        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(duration))
                        key_id, nonce, ciphertext = self.keyring.encrypt(
                            {**secret, "refresh_token": rotated, "access_token": token,
                             "access_token_expires_at": expires_at.isoformat()},
                            _credential_aad(self.actor_id, credential.google_subject))
                        credential.key_id, credential.nonce, credential.ciphertext = key_id, nonce, ciphertext
                        self._token = token
                        self._expires_at = self.clock() + max(0, int(duration) - min(60, int(duration) // 10))
            if invalid_grant:
                self._token, self._expires_at = None, 0.0
                raise PermissionFailure("User Drive credential requires reauthorization")
            return self._token
        except (PermissionFailure, RetryableStorageFailure):
            raise
        except Exception:
            raise RetryableStorageFailure(30, "Google OAuth token service is temporarily unavailable") from None


def build_user_drive_token_provider(session_factory, actor_id, settings, extensions, *, transport):
    """Server-only provider. Callers must independently authorize Production membership and policy."""
    client_id, client_secret, _callback, _return_uris = _oauth_settings(settings)
    return DatabaseUserDriveTokenProvider(session_factory, actor_id, _keyring(settings, extensions),
                                          client_id, client_secret, transport)


def rotate_personal_records(session, *, old_keyring, new_keyring) -> int:
    """Re-encrypt personal grants and pending intents inside the caller's transaction; returns rows changed."""
    from sqlalchemy import select

    try:
        models = _models()
        Credential, Intent = models.GoogleUserCredential, models.GoogleUserAuthorizationIntent
    except AttributeError:  # focused tests replace the model module with Production-only stubs
        return 0
    changed = 0
    for row in session.scalars(select(Credential).where(Credential.state != "revoked").with_for_update()).all():
        aad = _credential_aad(row.actor_id, row.google_subject)
        row.key_id, row.nonce, row.ciphertext = new_keyring.encrypt(old_keyring.decrypt(row.key_id, row.nonce, row.ciphertext, aad), aad)
        changed += 1
    for row in session.scalars(select(Intent).with_for_update()).all():
        verifier = old_keyring.decrypt(row.key_id, row.verifier_nonce, row.verifier_ciphertext, _intent_aad(row.actor_id, row.id, "verifier"))
        response = old_keyring.decrypt(row.key_id, row.response_nonce, row.response_ciphertext, _intent_aad(row.actor_id, row.id, "response"))
        row.key_id, row.verifier_nonce, row.verifier_ciphertext = new_keyring.encrypt(verifier, _intent_aad(row.actor_id, row.id, "verifier"))
        _key_id, row.response_nonce, row.response_ciphertext = new_keyring.encrypt(response, _intent_aad(row.actor_id, row.id, "response"))
        changed += 1
    return changed
