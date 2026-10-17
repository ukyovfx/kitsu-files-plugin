"""Synthetic tests for encrypted, Production-bound Google OAuth state."""

import base64
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import ModuleType
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import Flask, g


PRODUCTION = UUID("10000000-0000-4000-8000-000000000001")
ACTOR = UUID("20000000-0000-4000-8000-000000000002")
SETTINGS = {
    "VFX_FILES_GOOGLE_CLIENT_ID": "synthetic-client-id",
    "VFX_FILES_GOOGLE_CLIENT_SECRET": "synthetic-client-secret",
    "VFX_FILES_GOOGLE_OAUTH_REDIRECT_URI": "https://files.example.test/api/plugins/vfx_files/oauth/google/callback",
    "VFX_FILES_KITSUSYNC_RETURN_URI": "https://sync.example.test/admin/vfx-files/authorization-return",
    "VFX_FILES_KITSU_PLUGIN_RETURN_URI": "https://kitsu.example.test/api/plugins/vfx_files/frontend/",
}


@pytest.fixture
def oauth_db(monkeypatch, tmp_path):
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, LargeBinary, String, Uuid, UniqueConstraint, create_engine
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

    class Base(DeclarativeBase):
        pass

    class StorageConnection(Base):
        __tablename__ = "storage_connection"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
        production_id: Mapped[UUID] = mapped_column(Uuid)
        provider: Mapped[str] = mapped_column(String)
        account_mode: Mapped[str] = mapped_column(String)
        owner_subject: Mapped[str | None] = mapped_column(String, nullable=True)
        drive_id: Mapped[str | None] = mapped_column(String, nullable=True)
        allowed_root_id: Mapped[str] = mapped_column(String)
        credential_ref: Mapped[str] = mapped_column(String)
        enabled: Mapped[bool] = mapped_column(Boolean)

    class DriveCredential(Base):
        __tablename__ = "drive_credential"
        connection_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
        production_id: Mapped[UUID] = mapped_column(Uuid)
        account_mode: Mapped[str] = mapped_column(String)
        account_subject: Mapped[str] = mapped_column(String)
        owner_subject: Mapped[str | None] = mapped_column(String, nullable=True)
        drive_id: Mapped[str | None] = mapped_column(String, nullable=True)
        key_id: Mapped[str] = mapped_column(String)
        nonce: Mapped[bytes] = mapped_column(LargeBinary)
        ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
        state: Mapped[str] = mapped_column(String)
        updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    class DriveAuthorizationIntent(Base):
        __tablename__ = "drive_authorization_intent"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
        production_id: Mapped[UUID] = mapped_column(Uuid)
        actor_id: Mapped[UUID] = mapped_column(Uuid)
        connection_id: Mapped[UUID] = mapped_column(Uuid)
        account_mode: Mapped[str] = mapped_column(String)
        return_surface: Mapped[str] = mapped_column(String, default="kitsusync")
        state_sha256: Mapped[str] = mapped_column(String, unique=True)
        key_id: Mapped[str] = mapped_column(String)
        verifier_nonce: Mapped[bytes] = mapped_column(LargeBinary)
        verifier_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
        response_nonce: Mapped[bytes] = mapped_column(LargeBinary)
        response_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
        idempotency_key: Mapped[str] = mapped_column(String)
        request_sha256: Mapped[str] = mapped_column(String)
        status: Mapped[str] = mapped_column(String)
        expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
        consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    class KitsuSyncDesiredConfiguration(Base):
        __tablename__ = "desired_config"
        production_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
        config_revision: Mapped[int] = mapped_column(Integer)
        notification_epoch: Mapped[int] = mapped_column(Integer)
        desired_config: Mapped[dict] = mapped_column(JSON)

    class ProductionDriveAuthority(Base):
        __tablename__ = "production_drive_authority"
        production_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
        owner: Mapped[str] = mapped_column(String)
        state: Mapped[str] = mapped_column(String)
        config_revision: Mapped[int] = mapped_column(Integer)
        activation_epoch: Mapped[int] = mapped_column(Integer)
        connection_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
        root_id: Mapped[str | None] = mapped_column(String, nullable=True)
        desired_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
        activation_enabled: Mapped[bool] = mapped_column(Boolean)
        activation_transition_pending: Mapped[bool] = mapped_column(Boolean, default=False)
        updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    class ProductionDriveAuthorityEvent(Base):
        __tablename__ = "drive_authority_event"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
        production_id: Mapped[UUID] = mapped_column(Uuid)
        actor_id: Mapped[UUID] = mapped_column(Uuid)
        event_type: Mapped[str] = mapped_column(String)
        source_revision: Mapped[int] = mapped_column(BigInteger)
        config_revision: Mapped[int] = mapped_column(BigInteger)
        activation_epoch: Mapped[int] = mapped_column(BigInteger)
        idempotency_key: Mapped[str] = mapped_column(String)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
        __table_args__ = (UniqueConstraint("production_id", "idempotency_key"),)

    class SharedTemplateConfiguration(Base):
        __tablename__ = "shared_template_configuration"
        production_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
        storage_connection_id: Mapped[UUID] = mapped_column(Uuid)
        allowed_root_id: Mapped[str] = mapped_column(String)
        template_folder_id: Mapped[str] = mapped_column(String)
        template_tree: Mapped[dict] = mapped_column(JSON)
        role_mappings: Mapped[dict] = mapped_column(JSON)
        version: Mapped[int] = mapped_column(Integer)
        enabled: Mapped[bool] = mapped_column(Boolean)
        updated_by: Mapped[UUID] = mapped_column(Uuid)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    class Job(Base):
        __tablename__ = "job"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
        job_type: Mapped[str] = mapped_column(String)
        idempotency_key: Mapped[str] = mapped_column(String, unique=True)
        payload: Mapped[dict] = mapped_column(JSON)
        state: Mapped[str] = mapped_column(String)
        lease_owner: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
        lease_generation: Mapped[int] = mapped_column(Integer)
        lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    class KitsuSyncManagementIdempotency(Base):
        __tablename__ = "management_idempotency"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
        production_id: Mapped[UUID] = mapped_column(Uuid)
        idempotency_key: Mapped[str] = mapped_column(String)
        actor_id: Mapped[UUID] = mapped_column(Uuid)
        operation: Mapped[str] = mapped_column(String)
        request_sha256: Mapped[str] = mapped_column(String)
        response_status: Mapped[int] = mapped_column(Integer)
        response_etag: Mapped[str | None] = mapped_column(String, nullable=True)
        response_body: Mapped[dict] = mapped_column(JSON)
        __table_args__ = (UniqueConstraint("production_id", "idempotency_key"),)

    class KitsuSyncHierarchySnapshot(Base):
        __tablename__ = "hierarchy_snapshot_stub"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)

    class KitsuSyncHierarchySnapshotPage(Base):
        __tablename__ = "hierarchy_snapshot_page_stub"
        snapshot_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
        page_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    class KitsuSyncEventOutbox(Base):
        __tablename__ = "event_outbox_stub"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
        production_id: Mapped[UUID] = mapped_column(Uuid)
        storage_connection_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
        event_id: Mapped[str] = mapped_column(String, unique=True)
        event_type: Mapped[str] = mapped_column(String)
        payload: Mapped[dict] = mapped_column(JSON)
        payload_sha256: Mapped[str] = mapped_column(String)
        state: Mapped[str] = mapped_column(String)
        attempts: Mapped[int] = mapped_column(Integer)
        lease_generation: Mapped[int] = mapped_column(Integer)
        lease_owner: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
        lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
        last_response_code: Mapped[str | None] = mapped_column(String, nullable=True)

    models = ModuleType("plugin.models")
    for model in (
        StorageConnection, DriveCredential, DriveAuthorizationIntent, KitsuSyncDesiredConfiguration,
        ProductionDriveAuthority, ProductionDriveAuthorityEvent, SharedTemplateConfiguration,
        Job, KitsuSyncManagementIdempotency, KitsuSyncHierarchySnapshot, KitsuSyncHierarchySnapshotPage,
        KitsuSyncEventOutbox,
    ):
        setattr(models, model.__name__, model)
    monkeypatch.setitem(sys.modules, "plugin.models", models)
    engine = create_engine(f"sqlite:///{tmp_path / 'oauth.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    from plugin.integrations.oauth import CredentialKeyring
    keyring = CredentialKeyring("test-key-1", {"test-key-1": b"k" * 32})
    extensions = {"vfx_files_credential_keyring": keyring}
    return sessions, models, keyring, extensions


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self.body = body or {}

    def json(self):
        return self.body


class OAuthTransport:
    def __init__(self, *, subject="oidc-subject-a", refresh_token="synthetic-refresh-token", refresh_status=200, granted_scopes="openid https://www.googleapis.com/auth/drive.file"):
        self.subject = subject
        self.refresh_token = refresh_token
        self.refresh_status = refresh_status
        self.granted_scopes = granted_scopes
        self.calls = []
        self._guard = __import__("threading").Lock()

    def request(self, method, url, **kwargs):
        with self._guard:
            self.calls.append((method, url, kwargs))
        if url.endswith("/token") and kwargs.get("data", {}).get("grant_type") == "authorization_code":
            return FakeResponse(200, {
                "access_token": "synthetic-access-token", "refresh_token": self.refresh_token,
                "expires_in": 3600, "scope": self.granted_scopes,
            })
        if url.endswith("/userinfo"):
            return FakeResponse(200, {"sub": self.subject})
        if url.endswith("/token"):
            if self.refresh_status != 200:
                return FakeResponse(self.refresh_status, {"error": "synthetic provider error"})
            return FakeResponse(200, {"access_token": "refreshed-access-token", "refresh_token": "rotated-refresh-token", "expires_in": 3600})
        if url.endswith("/revoke"):
            return FakeResponse(200)
        raise AssertionError("Unexpected provider endpoint")


def _intent(session_factory, extensions, *, mode="my_drive", connection_id=None, key="oauth-idempotency-key-01", actor=ACTOR, return_surface="kitsusync", settings=SETTINGS):
    from plugin.integrations.oauth import start_authorization
    body = {"contract_version": "1.0", "production_id": str(PRODUCTION), "account_mode": mode}
    if connection_id is not None:
        body["connection_id"] = str(connection_id)
    if return_surface != "kitsusync":
        body["return_surface"] = return_surface
    with session_factory() as session:
        return start_authorization(
            session, payload=body, actor_id=actor, idempotency_key=key,
            settings=settings, extensions=extensions,
        )


def _state_from_response(response):
    parsed = parse_qs(urlsplit(response["authorization_url"]).query)
    return parsed["state"][0], parsed


def _service_bearer(private_key, *, issuer, audience, scope):
    def b64(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    now = int(datetime.now(timezone.utc).timestamp())
    header = {"typ": "at+JWT", "alg": "RS256", "kid": "oauth-http-test"}
    claims = {
        "iss": issuer, "aud": audience, "sub": "synthetic-service", "client_id": "synthetic-client",
        "iat": now, "nbf": now, "exp": now + 120, "jti": str(uuid4()), "scope": scope,
        "production_id": str(PRODUCTION), "actor_id": str(ACTOR), "actor_role": "production_admin",
    }
    signing_input = f"{b64(json.dumps(header, separators=(',', ':')).encode())}.{b64(json.dumps(claims, separators=(',', ':')).encode())}"
    signature = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{b64(signature)}"


def test_oauth_intent_is_pkce_bound_durable_idempotent_and_secret_free(oauth_db):
    sessions, models, _keyring, extensions = oauth_db
    first, status = _intent(sessions, extensions)
    assert status == 201
    state, params = _state_from_response(first)
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid https://www.googleapis.com/auth/drive.file"]
    assert "include_granted_scopes" not in params
    assert "prompt" not in params
    with sessions() as session:
        intent = session.get(models.DriveAuthorizationIntent, UUID(first["authorization_id"]))
        assert intent.state_sha256 != state
        assert state.encode() not in intent.verifier_ciphertext
        assert b"synthetic-client-secret" not in intent.response_ciphertext
    replay, replay_status = _intent(sessions, extensions)
    assert replay == first and replay_status == 201


def test_native_oauth_return_is_selected_from_server_allowlist_and_bound_to_intent(oauth_db):
    sessions, models, _keyring, extensions = oauth_db
    from plugin.integrations.oauth import complete_authorization

    settings = {key: value for key, value in SETTINGS.items() if key != "VFX_FILES_KITSUSYNC_RETURN_URI"}
    intent, status = _intent(
        sessions, extensions, key="native-oauth-idempotency-01",
        return_surface="kitsu_plugin", settings=settings,
    )
    assert status == 201
    state, _params = _state_from_response(intent)
    result = complete_authorization(
        sessions, state=state, code="synthetic-code", oauth_error=None,
        settings=settings, extensions=extensions, transport=OAuthTransport(),
    )
    assert result.startswith(settings["VFX_FILES_KITSU_PLUGIN_RETURN_URI"])
    assert "authorization_id=" in result and "status=connected" in result
    assert "code=" not in result and "token" not in result
    with sessions() as session:
        stored = session.get(models.DriveAuthorizationIntent, UUID(intent["authorization_id"]))
        assert stored.return_surface == "kitsu_plugin"


def test_native_kitsu_authorization_requires_manager_role_and_same_origin_csrf(oauth_db, monkeypatch):
    from types import SimpleNamespace

    from flask_jwt_extended import JWTManager, create_access_token

    from plugin import resources
    from plugin.authz import ResolvedScope, issue_csrf_token

    sessions, models, _keyring, extensions = oauth_db
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="synthetic-kitsu-secret",
        JWT_SECRET_KEY="synthetic-jwt-secret-with-more-than-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
        VFX_FILES_KITSU_ORIGIN="https://kitsu.example.test",
        **SETTINGS,
    )
    JWTManager(app)
    app.extensions.update(extensions)
    app.extensions["sqlalchemy"] = SimpleNamespace(engine=sessions.kw["bind"])
    role = ["manager"]
    monkeypatch.setattr(resources, "_assert_compatible", lambda: None)
    monkeypatch.setattr(resources, "current_actor_role", lambda _production_id: role[0])
    monkeypatch.setattr(
        resources,
        "_resolve_scope",
        lambda _actor, _production_id: ResolvedScope(PRODUCTION, None, None),
    )
    app.add_url_rule(
        "/api/plugins/vfx_files/productions/<production_id>/drive/authorizations",
        view_func=resources.NativeDriveAuthorization.as_view("native_drive_authorization"),
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/plugins/vfx_files/productions/<production_id>/drive/status",
        view_func=resources.NativeDriveStatus.as_view("native_drive_status"),
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/plugins/vfx_files/productions/<production_id>/drive/configuration",
        view_func=resources.NativeDriveConfiguration.as_view("native_drive_configuration"),
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/plugins/vfx_files/productions/<production_id>/drive/legacy-import",
        view_func=resources.NativeDriveLegacyImport.as_view("native_drive_legacy_import"),
        methods=["GET", "POST"],
    )
    with app.app_context():
        token = create_access_token(identity=str(ACTOR))
    client = app.test_client()
    headers = {
        "Authorization": f"Bearer {token}",
        "Origin": "https://kitsu.example.test",
        "X-VFX-Files-CSRF": issue_csrf_token("synthetic-kitsu-secret", ACTOR),
        "Idempotency-Key": "native-kitsu-drive-intent-001",
    }
    path = f"/api/plugins/vfx_files/productions/{PRODUCTION}/drive/authorizations"
    with sessions.begin() as session:
        session.add(models.ProductionDriveAuthority(
            production_id=PRODUCTION, owner="legacy_kitsusync", state="legacy_import_required",
            config_revision=0, activation_epoch=0, connection_id=None, activation_enabled=False,
        ))
    legacy = client.post(
        path,
        json={"contract_version": "1.0", "account_mode": "my_drive"},
        headers=headers,
        base_url="https://kitsu.example.test",
    )
    assert legacy.status_code == 409 and legacy.json["code"] == "legacy_import_required"
    legacy_config = client.get(
        f"/api/plugins/vfx_files/productions/{PRODUCTION}/drive/configuration",
        headers={"Authorization": f"Bearer {token}"},
        base_url="https://kitsu.example.test",
    )
    assert legacy_config.status_code == 200
    assert legacy_config.json["configured"] is False
    assert legacy_config.json["requires_explicit_legacy_import"] is True
    with sessions.begin() as session:
        session.delete(session.get(models.ProductionDriveAuthority, PRODUCTION))
    response = client.post(
        path,
        json={"contract_version": "1.0", "account_mode": "my_drive"},
        headers=headers,
        base_url="https://kitsu.example.test",
    )
    assert response.status_code == 201
    assert "authorization_url" in response.json
    assert "synthetic-client-secret" not in response.get_data(as_text=True)
    with sessions() as session:
        intent = session.get(models.DriveAuthorizationIntent, UUID(response.json["authorization_id"]))
        assert intent.actor_id == ACTOR
        assert intent.return_surface == "kitsu_plugin"

    status = client.get(
        f"/api/plugins/vfx_files/productions/{PRODUCTION}/drive/status",
        headers={"Authorization": f"Bearer {token}"},
        base_url="https://kitsu.example.test",
    )
    assert status.status_code == 200
    assert status.json["owner"] == "plugin" and status.json["state"] == "unconfigured"
    assert "credential_state" in status.json
    assert "refresh_token" not in status.get_data(as_text=True)

    legacy_import_path = f"/api/plugins/vfx_files/productions/{PRODUCTION}/drive/legacy-import"
    allowed_preview = client.get(
        legacy_import_path,
        headers={"Authorization": f"Bearer {token}"},
        base_url="https://kitsu.example.test",
    )
    assert allowed_preview.status_code == 200
    assert allowed_preview.json["status"] == "no_legacy_configuration"
    assert allowed_preview.json["writes_performed"] is False

    role[0] = "user"
    hidden_status = client.get(
        f"/api/plugins/vfx_files/productions/{PRODUCTION}/drive/status",
        headers={"Authorization": f"Bearer {token}"},
        base_url="https://kitsu.example.test",
    )
    assert hidden_status.status_code == 403
    denied = client.post(
        path,
        json={"contract_version": "1.0", "account_mode": "my_drive"},
        headers={**headers, "Idempotency-Key": "native-kitsu-drive-intent-002"},
        base_url="https://kitsu.example.test",
    )
    assert denied.status_code == 403
    denied_preview = client.get(
        legacy_import_path,
        headers={"Authorization": f"Bearer {token}"},
        base_url="https://kitsu.example.test",
    )
    assert denied_preview.status_code == 403
    denied_import = client.post(
        legacy_import_path,
        json={"contract_version": "1.0", "source_revision": 1, "confirm": True},
        headers={**headers, "Idempotency-Key": "native-kitsu-legacy-import-user-denied"},
        base_url="https://kitsu.example.test",
    )
    assert denied_import.status_code == 403
    bad_origin = client.post(
        path,
        json={"contract_version": "1.0", "account_mode": "my_drive"},
        headers={**headers, "Origin": "https://attacker.example.test", "Idempotency-Key": "native-kitsu-drive-intent-003"},
        base_url="https://kitsu.example.test",
    )
    assert bad_origin.status_code == 403


def test_native_root_selection_requires_app_visible_root_and_fences_configuration(oauth_db):
    from plugin.integrations.configuration import ConfigurationError
    from plugin.integrations.native_storage import list_native_root_candidates, select_native_root
    from plugin.storage.fake import FakeStorage

    sessions, models, _keyring, _extensions = oauth_db
    connection_id = uuid4()
    connection = models.StorageConnection(
        id=connection_id, production_id=PRODUCTION, provider="google_drive",
        account_mode="my_drive", owner_subject="oidc-subject-a", drive_id=None,
        allowed_root_id=f"__pending__:{connection_id}", credential_ref=str(connection_id), enabled=False,
    )
    credential = models.DriveCredential(
        connection_id=connection_id, production_id=PRODUCTION, account_mode="my_drive",
        account_subject="oidc-subject-a", owner_subject="oidc-subject-a", drive_id=None,
        key_id="test-key-1", nonce=b"n" * 12, ciphertext=b"encrypted", state="active",
    )
    with sessions.begin() as session:
        session.add_all([connection, credential])

    adapter = FakeStorage(root_id="app-visible-root")
    assert list_native_root_candidates(connection, adapter) == {
        "contract_version": "1.0",
        "roots": [{"id": "app-visible-root", "name": "FakeStorage root", "account_mode": "my_drive"}],
    }
    caps = adapter.verify_root_candidate("app-visible-root")
    payload = {"contract_version": "1.0", "root_id": "app-visible-root"}
    with sessions() as session:
        body, status, etag = select_native_root(
            session, production_id=PRODUCTION, actor_id=ACTOR, connection_id=connection_id,
            payload=payload, idempotency_key="native-root-select-0001", if_match='"0"',
            capabilities=caps,
        )
    assert status == 200 and etag == '"1"'
    assert body["owner"] == "plugin" and body["state"] == "configured"
    assert body["root_id"] == "app-visible-root" and not body["activation_enabled"]
    with sessions() as session:
        stored = session.get(models.StorageConnection, connection_id)
        authority = session.get(models.ProductionDriveAuthority, PRODUCTION)
        assert stored.enabled and stored.allowed_root_id == "app-visible-root"
        assert authority.owner == "plugin" and authority.config_revision == 1
        assert authority.activation_epoch == 1 and authority.activation_enabled is False

    with sessions() as session:
        assert select_native_root(
            session, production_id=PRODUCTION, actor_id=ACTOR, connection_id=connection_id,
            payload=payload, idempotency_key="native-root-select-0001", if_match='"0"',
            capabilities=caps,
        ) == (body, status, etag)
    with sessions() as session, pytest.raises(ConfigurationError) as stale:
        select_native_root(
            session, production_id=PRODUCTION, actor_id=ACTOR, connection_id=connection_id,
            payload=payload, idempotency_key="native-root-select-0002", if_match='"0"',
            capabilities=caps,
        )
    assert stale.value.code == "stale_config"


def test_native_root_selection_refuses_legacy_owner_without_mutation(oauth_db):
    from types import SimpleNamespace

    from plugin.integrations.configuration import ConfigurationError
    from plugin.integrations.native_storage import select_native_root

    sessions, models, _keyring, _extensions = oauth_db
    connection_id = uuid4()
    connection = models.StorageConnection(
        id=connection_id, production_id=PRODUCTION, provider="google_drive",
        account_mode="my_drive", owner_subject="oidc-subject-a", drive_id=None,
        allowed_root_id=f"__pending__:{connection_id}", credential_ref=str(connection_id), enabled=False,
    )
    credential = models.DriveCredential(
        connection_id=connection_id, production_id=PRODUCTION, account_mode="my_drive",
        account_subject="oidc-subject-a", owner_subject="oidc-subject-a", drive_id=None,
        key_id="test-key-1", nonce=b"n" * 12, ciphertext=b"encrypted", state="active",
    )
    legacy = models.ProductionDriveAuthority(
        production_id=PRODUCTION, owner="legacy_kitsusync", state="legacy_import_required",
        config_revision=3, activation_epoch=7, connection_id=None, root_id=None, activation_enabled=False,
    )
    with sessions.begin() as session:
        session.add_all([connection, credential, legacy])
    with sessions() as session, pytest.raises(ConfigurationError) as blocked:
        select_native_root(
            session, production_id=PRODUCTION, actor_id=ACTOR, connection_id=connection_id,
            payload={"contract_version": "1.0", "root_id": "app-visible-root"},
            idempotency_key="native-root-legacy-0001", if_match='"3"',
            capabilities=SimpleNamespace(root_visible=True, root_id="app-visible-root", can_add_children=True),
        )
    assert blocked.value.code == "legacy_import_required"
    with sessions() as session:
        assert session.get(models.StorageConnection, connection_id).enabled is False
        assert session.get(models.ProductionDriveAuthority, PRODUCTION).config_revision == 3


def test_legacy_kitsusync_writer_persists_owner_marker_when_config_precedes_authority(oauth_db):
    from plugin.integrations.configuration import _assert_kitsusync_is_legacy_owner

    sessions, models, _keyring, _extensions = oauth_db
    legacy = models.KitsuSyncDesiredConfiguration(
        production_id=PRODUCTION, config_revision=4, notification_epoch=9,
        desired_config={"contract_version": "1.0"},
    )
    with sessions.begin() as session:
        session.add(legacy)
    with sessions.begin() as session:
        _assert_kitsusync_is_legacy_owner(session, PRODUCTION)
    with sessions() as session:
        authority = session.get(models.ProductionDriveAuthority, PRODUCTION)
        assert authority.owner == "legacy_kitsusync"
        assert authority.state == "legacy_import_required"
        assert authority.config_revision == 0 and authority.activation_epoch == 9
        assert authority.connection_id is None and not authority.activation_enabled


def test_plugin_configuration_and_activation_are_fenced_and_do_not_create_legacy_writer(oauth_db):
    from types import SimpleNamespace
    from sqlalchemy import select

    from plugin.integrations.configuration import ConfigurationError, put_plugin_desired_config, set_plugin_activation
    from plugin.models import KitsuSyncDesiredConfiguration, ProductionDriveAuthority, SharedTemplateConfiguration

    sessions, models, _keyring, _extensions = oauth_db
    connection_id = uuid4()
    connection = models.StorageConnection(
        id=connection_id, production_id=PRODUCTION, provider="google_drive",
        account_mode="my_drive", owner_subject="oidc-subject-a", drive_id=None,
        allowed_root_id="app-visible-root", credential_ref=str(connection_id), enabled=True,
    )
    credential = models.DriveCredential(
        connection_id=connection_id, production_id=PRODUCTION, account_mode="my_drive",
        account_subject="oidc-subject-a", owner_subject="oidc-subject-a", drive_id=None,
        key_id="test-key-1", nonce=b"n" * 12, ciphertext=b"encrypted", state="active",
    )
    authority = models.ProductionDriveAuthority(
        production_id=PRODUCTION, owner="plugin", state="configured", config_revision=1,
        activation_epoch=1, connection_id=connection_id, root_id="app-visible-root",
        desired_config=None, activation_enabled=False,
    )
    with sessions.begin() as session:
        session.add_all([connection, credential, authority])

    payload = {
        "contract_version": "1.0",
        "template_selection": {"selections": [
            {"scope": "production", "template_id": "vfx-files.production.default", "revision": 1, "overrides": {}},
            {"scope": "shot", "template_id": "vfx-files.shot.default", "revision": 1, "overrides": {}},
            {"scope": "task", "template_id": "vfx-files.task.default", "revision": 1, "overrides": {}},
        ]},
        "sync_policy": {
            "mode": "future_only",
            "scope": {"episodes": False, "sequences": False, "shots": True, "assets": False, "tasks": True},
            "notify_new_deliveries": False,
        },
    }
    with sessions() as session:
        saved, status, etag = put_plugin_desired_config(
            session, production_id=PRODUCTION, actor_id=ACTOR, payload=payload,
            idempotency_key="plugin-config-save-0001", if_match='"1"',
        )
    assert status == 200 and etag == '"2"'
    assert saved["activation"]["enabled"] is False
    with sessions() as session:
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        template = session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == PRODUCTION,
        ))
        assert authority.desired_config["sync_policy"] == payload["sync_policy"]
        assert authority.activation_epoch == 2 and not authority.activation_enabled
        assert template is not None and not template.enabled and template.version == 2
        assert session.get(KitsuSyncDesiredConfiguration, PRODUCTION) is None

    capabilities = SimpleNamespace(root_visible=True, root_id="app-visible-root", can_add_children=True)
    with sessions() as session:
        active, status, etag = set_plugin_activation(
            session, production_id=PRODUCTION, actor_id=ACTOR, enabled=True,
            payload={"contract_version": "1.0"}, idempotency_key="plugin-activate-0001",
            if_match='"2"', capabilities=capabilities, deactivation_barrier_required=True,
        )
    assert status == 202 and etag == '"3"'
    assert active["activation"]["enabled"] is False
    assert active["activation"]["requested_enabled"] is True
    assert active["activation"]["transition_pending"] is True
    with sessions() as session:
        from plugin.integrations.configuration import get_desired_config
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        assert authority.activation_epoch == 3 and not authority.activation_enabled
        assert authority.activation_transition_pending
        assert get_desired_config(session, PRODUCTION)[0]["activation"]["enabled"] is False
        assert session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == PRODUCTION,
        )).enabled is False
        outbox_row = session.scalar(select(models.KitsuSyncEventOutbox).where(
            models.KitsuSyncEventOutbox.production_id == PRODUCTION,
        ))
        assert outbox_row.event_type == "storage_authority.activation_requested"
        assert outbox_row.payload["activation_epoch"] == 3
        worker_id = uuid4()
        outbox_row.state = "leased"
        outbox_row.lease_owner = worker_id
        outbox_row.lease_generation = 1
        outbox_row.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        session.commit()
        from plugin.integrations.event_delivery import SqlAlchemyEventOutbox
        leased = __import__("types").SimpleNamespace(
            id=outbox_row.id, production_id=outbox_row.production_id,
            event_type=outbox_row.event_type, payload=outbox_row.payload,
            lease_generation=outbox_row.lease_generation,
        )
        assert SqlAlchemyEventOutbox(sessions).finish(leased, worker_id, "delivered", response_code="202")
        session.expire_all()
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        assert authority.activation_enabled and not authority.activation_transition_pending
        from plugin.integrations.configuration import get_desired_config
        assert get_desired_config(session, PRODUCTION)[0]["activation"]["enabled"] is True
        assert session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == PRODUCTION,
        )).enabled is True
    with sessions() as session, pytest.raises(ConfigurationError) as stale:
        set_plugin_activation(
            session, production_id=PRODUCTION, actor_id=ACTOR, enabled=False,
            payload={"contract_version": "1.0"}, idempotency_key="plugin-deactivate-stale-001",
            if_match='"2"', capabilities=None,
        )
    assert stale.value.code == "stale_config"
    with sessions() as session:
        standalone, status, _etag = set_plugin_activation(
            session, production_id=PRODUCTION, actor_id=ACTOR, enabled=False,
            payload={"contract_version": "1.0"}, idempotency_key="plugin-deactivate-standalone-001",
            if_match='"3"', capabilities=None,
        )
    assert status == 200 and standalone["activation"]["enabled"] is False
    assert standalone["activation"]["transition_pending"] is False


def test_integrated_deactivation_stays_pending_until_signed_barrier_ack(oauth_db):
    from sqlalchemy import select

    from plugin.integrations.configuration import set_plugin_activation
    from plugin.models import ProductionDriveAuthority

    sessions, models, _keyring, _extensions = oauth_db
    connection_id = uuid4()
    connection = models.StorageConnection(
        id=connection_id, production_id=PRODUCTION, provider="google_drive",
        account_mode="my_drive", owner_subject="oidc-subject-a", drive_id=None,
        allowed_root_id="app-visible-root", credential_ref=str(connection_id), enabled=True,
    )
    authority = models.ProductionDriveAuthority(
        production_id=PRODUCTION, owner="plugin", state="configured", config_revision=4,
        activation_epoch=7, connection_id=connection_id, root_id="app-visible-root",
        desired_config={"contract_version": "1.0", "activation": {"enabled": True, "notification_epoch": 7}},
        activation_enabled=True,
    )
    with sessions.begin() as session:
        session.add_all([connection, authority])

    with sessions() as session:
        body, status, etag = set_plugin_activation(
            session, production_id=PRODUCTION, actor_id=ACTOR, enabled=False,
            payload={"contract_version": "1.0"}, idempotency_key="plugin-deactivate-barrier-0001",
            if_match='"4"', capabilities=None, deactivation_barrier_required=True,
        )
    assert status == 202 and etag == '"5"'
    assert body["activation"]["enabled"] is False
    assert body["activation"]["transition_pending"] is True
    with sessions() as session:
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        row = session.scalar(select(models.KitsuSyncEventOutbox).where(
            models.KitsuSyncEventOutbox.production_id == PRODUCTION,
        ))
        assert not authority.activation_enabled and authority.activation_transition_pending
        assert authority.activation_epoch == 8
        assert row.event_type == "storage_authority.deactivation_requested"
        assert row.payload["activation_epoch"] == 7
        assert row.payload["new_activation_epoch"] == 8
        assert row.state == "queued"


def test_integrated_credential_loss_queues_deactivation_barrier(oauth_db, monkeypatch):
    from sqlalchemy import select

    from plugin.integrations.storage_authority import fence_plugin_authority_after_credential_failure
    from plugin.models import ProductionDriveAuthority

    sessions, models, _keyring, _extensions = oauth_db
    connection_id = uuid4()
    with sessions.begin() as session:
        session.add(models.StorageConnection(
            id=connection_id, production_id=PRODUCTION, provider="google_drive",
            account_mode="my_drive", owner_subject="oidc-subject-a", drive_id=None,
            allowed_root_id="app-visible-root", credential_ref=str(connection_id), enabled=True,
        ))
        session.add(ProductionDriveAuthority(
            production_id=PRODUCTION, owner="plugin", state="configured", config_revision=9,
            activation_epoch=13, connection_id=connection_id, root_id="app-visible-root",
            desired_config={"contract_version": "1.0", "activation": {"enabled": True, "notification_epoch": 13}},
            activation_enabled=True,
        ))
    monkeypatch.setenv("VFX_FILES_KITSUSYNC_EVENT_ENDPOINT", "https://kitsusync.example.invalid/api/integrations/vfx-files/v1/events")
    with sessions.begin() as session:
        assert fence_plugin_authority_after_credential_failure(session, PRODUCTION, connection_id)
    with sessions() as session:
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        row = session.scalar(select(models.KitsuSyncEventOutbox).where(
            models.KitsuSyncEventOutbox.production_id == PRODUCTION,
        ))
        assert not authority.activation_enabled and authority.activation_transition_pending
        assert authority.activation_epoch == 14 and authority.config_revision == 10
        assert row.event_type == "storage_authority.deactivation_requested"
        assert row.payload["activation_epoch"] == 13 and row.payload["new_activation_epoch"] == 14


@pytest.fixture
def legacy_import_database(oauth_db):
    sessions, models, _keyring, _extensions = oauth_db
    from plugin.models import SharedTemplateConfiguration

    connection_id = uuid4()
    original_tree = {"folder_id": "existing-tree", "name": "", "children": []}
    legacy_config = {
        "contract_version": "1.0",
        "connection_id": str(connection_id),
        "drive": {"account_mode": "my_drive", "owner_subject": "oidc-subject-a", "root_id": "legacy-root", "root_state": "verified"},
        "template_selection": {"selections": [
            {"scope": "production", "template_id": "vfx-files.production.default", "revision": 1, "overrides": {}},
            {"scope": "shot", "template_id": "vfx-files.shot.default", "revision": 1, "overrides": {}},
            {"scope": "task", "template_id": "vfx-files.task.default", "revision": 1, "overrides": {}},
        ]},
        "sync_policy": {"mode": "future_only", "scope": {"episodes": False, "sequences": False, "shots": True, "assets": False, "tasks": True}, "notify_new_deliveries": True},
        "activation": {"enabled": True, "activated_at": "2026-10-01T00:00:00Z", "notification_epoch": 4},
    }
    connection = models.StorageConnection(
        id=connection_id, production_id=PRODUCTION, provider="google_drive",
        account_mode="my_drive", owner_subject="oidc-subject-a", drive_id=None,
        allowed_root_id="legacy-root", credential_ref=str(connection_id), enabled=True,
    )
    credential = models.DriveCredential(
        connection_id=connection_id, production_id=PRODUCTION, account_mode="my_drive",
        account_subject="oidc-subject-a", owner_subject="oidc-subject-a", drive_id=None,
        key_id="test-key-1", nonce=b"n" * 12, ciphertext=b"encrypted", state="active",
    )
    authority = models.ProductionDriveAuthority(
        production_id=PRODUCTION, owner="legacy_kitsusync", state="legacy_import_required",
        config_revision=0, activation_epoch=4, connection_id=None, root_id=None,
        desired_config=None, activation_enabled=False,
    )
    legacy = models.KitsuSyncDesiredConfiguration(
        production_id=PRODUCTION, config_revision=7, notification_epoch=4, desired_config=legacy_config,
    )
    template = models.SharedTemplateConfiguration(
        production_id=PRODUCTION, storage_connection_id=connection_id, allowed_root_id="legacy-root",
        template_folder_id="existing-template", template_tree=original_tree,
        role_mappings={"task": ["existing-task-role"]}, version=9, enabled=True,
        updated_by=ACTOR,
    )
    with sessions.begin() as session:
        session.add_all([connection, credential, authority, legacy, template])

    return sessions, models, connection_id, legacy_config, original_tree


def test_legacy_import_requires_verified_root_and_explicit_revisioned_confirmation(legacy_import_database):
    from types import SimpleNamespace
    from sqlalchemy import select

    from plugin.integrations.configuration import ConfigurationError
    from plugin.integrations.legacy_import import import_legacy_configuration
    from plugin.models import ProductionDriveAuthority, SharedTemplateConfiguration

    sessions, models, _connection_id, legacy_config, original_tree = legacy_import_database

    caps = SimpleNamespace(root_visible=True, root_id="legacy-root", can_add_children=True)
    request = {"contract_version": "1.0", "source_revision": 7, "confirm": True}
    with sessions() as session:
        body, status, etag = import_legacy_configuration(
            session, production_id=PRODUCTION, actor_id=ACTOR, payload=request,
            idempotency_key="legacy-import-confirm-0001", if_match='"legacy-7"', capabilities=caps,
        )
    assert status == 200 and etag == '"plugin-8"'
    assert body["owner"] == "plugin" and body["activation_enabled"] is False
    assert body["activation_epoch"] == 5 and body["config_revision"] == 8
    with sessions() as session:
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        legacy = session.get(models.KitsuSyncDesiredConfiguration, PRODUCTION)
        template = session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == PRODUCTION,
        ))
        audit = session.scalar(select(models.ProductionDriveAuthorityEvent).where(
            models.ProductionDriveAuthorityEvent.production_id == PRODUCTION,
        ))
        assert authority.desired_config["activation"]["enabled"] is False
        assert legacy.desired_config == legacy_config
        assert template.template_tree == original_tree and template.role_mappings == {"task": ["existing-task-role"]}
        assert template.enabled is False
        assert audit is not None
        assert audit.actor_id == ACTOR and audit.event_type == "legacy_configuration_imported"
        assert (audit.source_revision, audit.config_revision, audit.activation_epoch) == (7, 8, 5)
        assert audit.idempotency_key == "legacy-import-confirm-0001"

    with sessions() as session:
        replay, replay_status, replay_etag = import_legacy_configuration(
            session, production_id=PRODUCTION, actor_id=ACTOR, payload=request,
            idempotency_key="legacy-import-confirm-0001", if_match='"legacy-7"', capabilities=None,
        )
    assert (replay, replay_status, replay_etag) == (body, status, etag)

    with sessions() as session, pytest.raises(ConfigurationError) as stale:
        import_legacy_configuration(
            session, production_id=PRODUCTION, actor_id=ACTOR, payload=request,
            idempotency_key="legacy-import-confirm-0002", if_match='"legacy-6"', capabilities=caps,
        )
    assert stale.value.code == "invalid_event"


def _native_legacy_import_client(sessions, models, connection_id, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import select
    from flask_jwt_extended import JWTManager, create_access_token

    from plugin import resources
    from plugin.authz import ResolvedScope

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="synthetic-kitsu-secret",
        JWT_SECRET_KEY="synthetic-jwt-secret-with-more-than-32-bytes",
        JWT_TOKEN_LOCATION=["headers"],
        VFX_FILES_KITSU_ORIGIN="https://kitsu.example.test",
        **SETTINGS,
    )
    JWTManager(app)
    app.extensions["sqlalchemy"] = SimpleNamespace(engine=sessions.kw["bind"])
    capabilities = SimpleNamespace(root_visible=True, root_id="legacy-root", can_add_children=True)

    class StorageAdapter:
        def verify_root_candidate(self, root_id):
            assert root_id == "legacy-root"
            return capabilities

    adapter = StorageAdapter()
    monkeypatch.setattr(resources, "_assert_compatible", lambda: None)
    monkeypatch.setattr(resources, "current_actor_role", lambda _production_id: "manager")
    monkeypatch.setattr(
        resources,
        "_resolve_scope",
        lambda _actor, _production_id: ResolvedScope(PRODUCTION, None, None),
    )
    monkeypatch.setattr(resources, "_storage_adapter", lambda _connection, **_kwargs: adapter)

    class ConnectionQuery:
        def filter_by(self, **filters):
            assert filters == {"id": str(connection_id), "production_id": PRODUCTION}
            return self

        def one_or_none(self):
            with sessions() as session:
                return session.scalar(select(models.StorageConnection).where(
                    models.StorageConnection.id == connection_id,
                    models.StorageConnection.production_id == PRODUCTION,
                ))

    monkeypatch.setattr(models.StorageConnection, "query", ConnectionQuery(), raising=False)
    app.add_url_rule(
        "/api/plugins/vfx_files/productions/<production_id>/drive/legacy-import",
        view_func=resources.NativeDriveLegacyImport.as_view("native_legacy_import_route"),
        methods=["GET", "POST"],
    )
    with app.app_context():
        token = create_access_token(identity=str(ACTOR))
    return app.test_client(), token, capabilities


def test_native_legacy_import_route_transfers_configuration_inactive(legacy_import_database, monkeypatch):
    from sqlalchemy import select
    from plugin.authz import issue_csrf_token
    from plugin.models import ProductionDriveAuthority, SharedTemplateConfiguration

    sessions, models, connection_id, legacy_config, original_tree = legacy_import_database
    monkeypatch.delenv("VFX_FILES_KITSUSYNC_EVENT_ENDPOINT", raising=False)
    client, token, _capabilities = _native_legacy_import_client(sessions, models, connection_id, monkeypatch)
    path = f"/api/plugins/vfx_files/productions/{PRODUCTION}/drive/legacy-import"
    authorization = {"Authorization": f"Bearer {token}"}
    preview = client.get(path, headers=authorization, base_url="https://kitsu.example.test")
    assert preview.status_code == 200
    assert preview.json["status"] == "ready_for_explicit_review"
    assert preview.json["source_revision"] == 7
    assert preview.json["proposed_activation_enabled"] is False
    assert preview.json["writes_performed"] is False

    response = client.post(
        path,
        json={"contract_version": "1.0", "source_revision": 7, "confirm": True},
        headers={
            **authorization,
            "Origin": "https://kitsu.example.test",
            "X-VFX-Files-CSRF": issue_csrf_token("synthetic-kitsu-secret", ACTOR),
            "Idempotency-Key": "native-legacy-import-confirm-0001",
            "If-Match": '"legacy-7"',
        },
        base_url="https://kitsu.example.test",
    )
    assert response.status_code == 200
    assert response.headers["ETag"] == '"plugin-8"'
    assert response.json["import_status"] == "imported_inactive"
    assert response.json["activation_enabled"] is False
    assert response.json["drive_operations_performed"] is False

    with sessions() as session:
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        legacy = session.get(models.KitsuSyncDesiredConfiguration, PRODUCTION)
        connection = session.get(models.StorageConnection, connection_id)
        credential = session.get(models.DriveCredential, connection_id)
        template = session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == PRODUCTION,
        ))
        audit = session.scalar(select(models.ProductionDriveAuthorityEvent).where(
            models.ProductionDriveAuthorityEvent.production_id == PRODUCTION,
        ))
        assert authority.owner == "plugin" and authority.state == "configured"
        assert authority.connection_id == connection_id and authority.root_id == "legacy-root"
        assert authority.activation_epoch == 5 and authority.config_revision == 8
        assert authority.activation_enabled is False and authority.activation_transition_pending is False
        assert authority.desired_config["activation"]["enabled"] is False
        assert legacy.desired_config == legacy_config
        assert connection.allowed_root_id == "legacy-root" and connection.enabled is True
        assert credential.ciphertext == b"encrypted" and credential.state == "active"
        assert template.template_tree == original_tree and template.enabled is False
        assert audit.actor_id == ACTOR and audit.event_type == "legacy_configuration_imported"
        assert audit.idempotency_key == "native-legacy-import-confirm-0001"


def test_native_integrated_legacy_import_waits_for_durable_barrier_ack(legacy_import_database, monkeypatch):
    import hashlib
    import hmac
    from sqlalchemy import select

    from plugin.authz import issue_csrf_token
    from plugin.integrations.event_delivery import EventDeliveryConfig, SqlAlchemyEventOutbox, dispatch_one
    from plugin.integrations.v1 import validate_event_batch
    from plugin.models import ProductionDriveAuthority, SharedTemplateConfiguration

    sessions, models, connection_id, legacy_config, original_tree = legacy_import_database
    delivery_config = EventDeliveryConfig(
        endpoint="https://kitsusync.example.invalid/api/integrations/vfx-files/v1/events",
        key_id="synthetic-files-key-01",
        secret=b"synthetic-durable-barrier-key-material-000000",
    )
    monkeypatch.setenv("VFX_FILES_KITSUSYNC_EVENT_ENDPOINT", delivery_config.endpoint)
    client, token, _capabilities = _native_legacy_import_client(sessions, models, connection_id, monkeypatch)
    path = f"/api/plugins/vfx_files/productions/{PRODUCTION}/drive/legacy-import"
    headers = {
        "Authorization": f"Bearer {token}",
        "Origin": "https://kitsu.example.test",
        "X-VFX-Files-CSRF": issue_csrf_token("synthetic-kitsu-secret", ACTOR),
        "If-Match": '"legacy-7"',
    }
    payload = {"contract_version": "1.0", "source_revision": 7, "confirm": True}

    def confirm(idempotency_key):
        return client.post(
            path,
            json=payload,
            headers={**headers, "Idempotency-Key": idempotency_key},
            base_url="https://kitsu.example.test",
        )

    preview = client.get(
        path,
        headers={"Authorization": f"Bearer {token}"},
        base_url="https://kitsu.example.test",
    )
    assert preview.status_code == 200 and preview.json["handoff_barrier_status"] == "required"
    pending = confirm("native-integrated-import-first-0001")
    assert pending.status_code == 202
    assert pending.headers["ETag"] == '"legacy-7"'
    assert pending.json["import_status"] == "deactivation_barrier_pending"
    assert pending.json["requires_explicit_confirmation_after_barrier"] is True

    with sessions() as session:
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        outbox_row = session.scalar(select(models.KitsuSyncEventOutbox))
        template = session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == PRODUCTION,
        ))
        assert authority.owner == "legacy_kitsusync"
        assert authority.activation_transition_pending is True
        assert authority.activation_enabled is False and authority.activation_epoch == 5
        assert outbox_row.event_type == "storage_authority.deactivation_requested"
        assert outbox_row.state == "queued" and template.enabled is True

    before_ack = confirm("native-integrated-import-before-ack-0001")
    assert before_ack.status_code == 409
    assert before_ack.json["code"] == "legacy_import_barrier_pending"

    with sessions.begin() as session:
        row = session.scalar(select(models.KitsuSyncEventOutbox))
        row.available_at = datetime.now(timezone.utc) - timedelta(seconds=2)

    class SyntheticBarrierReceiver:
        calls = []

        def post(self, endpoint, body, headers):
            assert endpoint == delivery_config.endpoint
            assert headers["X-VFX-Key-Id"] == delivery_config.key_id
            body_digest = hashlib.sha256(body).hexdigest()
            assert headers["X-VFX-Content-SHA256"] == body_digest
            signing_input = "\n".join((
                headers["X-VFX-Timestamp"], headers["X-VFX-Nonce"],
                body_digest, headers["Idempotency-Key"],
            )).encode("utf-8")
            expected = "sha256=" + hmac.new(
                delivery_config.secret, signing_input, hashlib.sha256,
            ).hexdigest()
            assert hmac.compare_digest(headers["X-VFX-Signature"], expected)
            events = validate_event_batch(json.loads(body))
            assert len(events) == 1
            event = events[0]
            assert event["event_type"] == "storage_authority.deactivation_requested"
            assert headers["Idempotency-Key"] == event["event_id"]
            self.calls.append((body, headers))
            return 202, {
                "contract_version": "1.0",
                "results": [{
                    "event_id": event["event_id"],
                    "outcome": "accepted",
                    "code": "activation_barrier_complete",
                }],
            }

    outbox = SqlAlchemyEventOutbox(sessions)
    worker_id = uuid4()
    receiver = SyntheticBarrierReceiver()
    assert dispatch_one(outbox, receiver, delivery_config, worker_id=worker_id) == "delivered"
    assert len(receiver.calls) == 1

    after_ack = confirm("native-integrated-import-after-ack-0001")
    assert after_ack.status_code == 200
    assert after_ack.headers["ETag"] == '"plugin-9"'
    assert after_ack.json["import_status"] == "imported_inactive"
    assert after_ack.json["activation_enabled"] is False
    assert after_ack.json["drive_operations_performed"] is False

    with sessions() as session:
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        legacy = session.get(models.KitsuSyncDesiredConfiguration, PRODUCTION)
        connection = session.get(models.StorageConnection, connection_id)
        credential = session.get(models.DriveCredential, connection_id)
        barrier = session.scalar(select(models.KitsuSyncEventOutbox))
        template = session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == PRODUCTION,
        ))
        audit = session.scalar(select(models.ProductionDriveAuthorityEvent).where(
            models.ProductionDriveAuthorityEvent.production_id == PRODUCTION,
        ))
        assert authority.owner == "plugin" and authority.state == "configured"
        assert authority.connection_id == connection_id and authority.root_id == "legacy-root"
        assert authority.activation_epoch == 5 and authority.config_revision == 9
        assert authority.activation_enabled is False and authority.activation_transition_pending is False
        assert legacy.desired_config == legacy_config
        assert connection.allowed_root_id == "legacy-root" and connection.enabled is True
        assert credential.ciphertext == b"encrypted" and credential.state == "active"
        assert barrier.state == "delivered"
        assert template.template_tree == original_tree and template.enabled is False
        assert audit.actor_id == ACTOR and audit.event_type == "legacy_configuration_imported"
        assert audit.idempotency_key == "native-integrated-import-after-ack-0001"


def test_integrated_legacy_import_waits_for_durable_deactivation_barrier(oauth_db, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import select, update

    from plugin.integrations.configuration import ConfigurationError
    from plugin.integrations.event_delivery import (
        EventDeliveryConfig, HttpsEventReceiver, SqlAlchemyEventOutbox, dispatch_one,
    )
    from plugin.integrations.legacy_import import import_legacy_configuration
    from plugin.integrations.storage_authority import effective_drive_authority
    from plugin.models import ProductionDriveAuthority, SharedTemplateConfiguration

    sessions, models, _keyring, _extensions = oauth_db
    connection_id = uuid4()
    legacy_config = {
        "contract_version": "1.0", "connection_id": str(connection_id),
        "drive": {"account_mode": "my_drive", "owner_subject": "oidc-subject-a", "root_id": "legacy-root", "root_state": "verified"},
        "template_selection": {"selections": [
            {"scope": "production", "template_id": "vfx-files.production.default", "revision": 1, "overrides": {}},
            {"scope": "shot", "template_id": "vfx-files.shot.default", "revision": 1, "overrides": {}},
            {"scope": "task", "template_id": "vfx-files.task.default", "revision": 1, "overrides": {}},
        ]},
        "sync_policy": {"mode": "future_only", "scope": {"episodes": False, "sequences": False, "shots": True, "assets": False, "tasks": True}, "notify_new_deliveries": True},
        "activation": {"enabled": True, "activated_at": "2026-10-01T00:00:00Z", "notification_epoch": 4},
    }
    connection = models.StorageConnection(
        id=connection_id, production_id=PRODUCTION, provider="google_drive", account_mode="my_drive",
        owner_subject="oidc-subject-a", drive_id=None, allowed_root_id="legacy-root",
        credential_ref=str(connection_id), enabled=True,
    )
    credential = models.DriveCredential(
        connection_id=connection_id, production_id=PRODUCTION, account_mode="my_drive",
        account_subject="oidc-subject-a", owner_subject="oidc-subject-a", drive_id=None,
        key_id="test-key-1", nonce=b"n" * 12, ciphertext=b"encrypted", state="active",
    )
    authority = models.ProductionDriveAuthority(
        production_id=PRODUCTION, owner="legacy_kitsusync", state="legacy_import_required",
        config_revision=0, activation_epoch=4, connection_id=None, root_id=None,
        desired_config=None, activation_enabled=False,
    )
    legacy = models.KitsuSyncDesiredConfiguration(
        production_id=PRODUCTION, config_revision=7, notification_epoch=4, desired_config=legacy_config,
    )
    template = models.SharedTemplateConfiguration(
        production_id=PRODUCTION, storage_connection_id=connection_id, allowed_root_id="legacy-root",
        template_folder_id="existing-template", template_tree={"folder_id": "existing", "name": "", "children": []},
        role_mappings={}, version=7, enabled=True, updated_by=ACTOR,
    )
    with sessions.begin() as session:
        session.add_all([connection, credential, authority, legacy, template])

    cross_endpoint = os.environ.get("VFX_FILES_CROSS_REPO_TEST_ENDPOINT")
    cross_key_file = os.environ.get("VFX_FILES_CROSS_REPO_TEST_HMAC_KEY_FILE")
    cross_key_id = os.environ.get("VFX_FILES_CROSS_REPO_TEST_KEY_ID")
    cross_control_url = os.environ.get("VFX_FILES_CROSS_REPO_TEST_CONTROL_URL")
    cross_control_key = os.environ.get("VFX_FILES_CROSS_REPO_TEST_CONTROL_KEY")
    cross_repo = any((cross_endpoint, cross_key_file, cross_key_id, cross_control_url, cross_control_key))
    if cross_repo and not all((cross_endpoint, cross_key_file, cross_key_id, cross_control_url, cross_control_key)):
        pytest.fail("Cross-repository barrier test configuration is incomplete")
    monkeypatch.setenv(
        "VFX_FILES_KITSUSYNC_EVENT_ENDPOINT",
        cross_endpoint or "https://kitsusync.example.invalid/api/integrations/vfx-files/v1/events",
    )
    receiver = HttpsEventReceiver()
    delivery_config = None
    if cross_repo:
        from pathlib import Path

        delivery_config = EventDeliveryConfig(
            cross_endpoint, cross_key_id, Path(cross_key_file).read_bytes(),
        )
    caps = SimpleNamespace(root_visible=True, root_id="legacy-root", can_add_children=True)
    request = {"contract_version": "1.0", "source_revision": 7, "confirm": True}
    with sessions() as session:
        pending, status, etag = import_legacy_configuration(
            session, production_id=PRODUCTION, actor_id=ACTOR, payload=request,
            idempotency_key="legacy-import-barrier-0001", if_match='"legacy-7"', capabilities=caps,
        )
    assert status == 202 and etag == '"legacy-7"'
    assert pending["import_status"] == "deactivation_barrier_pending"
    with sessions() as session:
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        template = session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == PRODUCTION,
        ))
        assert authority.owner == "legacy_kitsusync" and authority.activation_transition_pending
        assert authority.activation_epoch == 5 and template.enabled is True

    # The first confirmation only requests a durable receiver fence. A second
    # administrator confirmation must not transfer ownership while that ACK
    # is absent, including after the outbox attempt has entered retry state.
    with sessions() as session, pytest.raises(ConfigurationError) as unacknowledged:
        import_legacy_configuration(
            session, production_id=PRODUCTION, actor_id=ACTOR, payload=request,
            idempotency_key="legacy-import-before-ack-0001", if_match='"legacy-7"', capabilities=caps,
        )
    assert unacknowledged.value.code == "legacy_import_barrier_pending"

    outbox = SqlAlchemyEventOutbox(sessions)
    with sessions.begin() as session:
        row = session.scalar(select(models.KitsuSyncEventOutbox))
        row.available_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    worker_id = uuid4()
    if cross_repo:
        # Send the real signed outbox event to the KitsuSync HTTP receiver. Its
        # unresolved old Discord outcome must withhold the durable ACK.
        assert dispatch_one(outbox, receiver, delivery_config, worker_id=worker_id) == "retry"
    else:
        leased = outbox.claim_next(worker_id)
        assert leased is not None and leased.event_type == "storage_authority.deactivation_requested"
        assert outbox.is_current_activation(leased)
        assert outbox.finish(leased, worker_id, "retry", response_code="503", retry_after_seconds=1)

    # A newly constructed worker resumes from the persisted retry row, modeling
    # worker restart against the durable database rather than in-memory state.
    with sessions.begin() as session:
        # SQLite's `CURRENT_TIMESTAMP + interval` emulation may return a
        # numeric value for DateTime; set the test clock with a typed value.
        row_id = session.scalar(select(models.KitsuSyncEventOutbox.id))
        assert session.execute(update(models.KitsuSyncEventOutbox).where(
            models.KitsuSyncEventOutbox.id == row_id,
        ).values(available_at=datetime.now(timezone.utc) - timedelta(seconds=2))).rowcount == 1
        state = session.scalar(select(models.KitsuSyncEventOutbox.state).where(
            models.KitsuSyncEventOutbox.id == row_id,
        ))
        assert state == "retry"
        retry_attempts = session.scalar(select(models.KitsuSyncEventOutbox.attempts).where(
            models.KitsuSyncEventOutbox.id == row_id,
        ))
        retry_event_id = session.scalar(select(models.KitsuSyncEventOutbox.event_id).where(
            models.KitsuSyncEventOutbox.id == row_id,
        ))
        assert retry_attempts == 1
        assert retry_event_id
    recovered_outbox = SqlAlchemyEventOutbox(sessions)
    recovered_worker_id = uuid4()
    if not cross_repo:
        recovered = recovered_outbox.claim_next(recovered_worker_id)
        assert recovered is not None and recovered.event_id == leased.event_id
        assert recovered_outbox.is_current_activation(recovered)

    with sessions() as session, pytest.raises(ConfigurationError) as retrying:
        import_legacy_configuration(
            session, production_id=PRODUCTION, actor_id=ACTOR, payload=request,
            idempotency_key="legacy-import-during-retry-0001", if_match='"legacy-7"', capabilities=caps,
        )
    assert retrying.value.code == "legacy_import_barrier_pending"

    if cross_repo:
        # Resolve the synthetic pre-existing dispatch through a loopback-only,
        # random-key control route, then recover the persisted retry with a new
        # worker and require the receiver's post-commit barrier ACK.
        from urllib.request import Request, urlopen

        control = Request(cross_control_url, data=b"", method="POST", headers={
            "X-Test-Control-Key": cross_control_key,
        })
        with urlopen(control, timeout=10) as response:
            assert response.status == 204
        assert dispatch_one(
            recovered_outbox, receiver, delivery_config, worker_id=recovered_worker_id,
        ) == "delivered"
    else:
        assert recovered_outbox.finish(recovered, recovered_worker_id, "delivered", response_code="202")

    with sessions() as session:
        retried = session.scalar(select(models.KitsuSyncEventOutbox))
        assert retried.event_id == retry_event_id
        assert retried.attempts == retry_attempts + 1
        assert retried.state == "delivered"

    with sessions() as session:
        authority = session.get(ProductionDriveAuthority, PRODUCTION)
        effective = effective_drive_authority(session, PRODUCTION)
        assert authority.owner == "legacy_kitsusync" and authority.activation_transition_pending
        assert effective["activation_epoch"] == 5 and not effective["activation_enabled"]
        template = session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == PRODUCTION,
        ))
        assert template is not None and template.enabled is False

    with sessions() as session:
        body, status, etag = import_legacy_configuration(
            session, production_id=PRODUCTION, actor_id=ACTOR, payload=request,
            idempotency_key="legacy-import-barrier-0002", if_match='"legacy-7"', capabilities=caps,
        )
    assert status == 200 and etag == '"plugin-9"'
    assert body["owner"] == "plugin" and body["activation_enabled"] is False
    assert body["activation_epoch"] == 5 and body["config_revision"] == 9
    with sessions() as session:
        legacy = session.get(models.KitsuSyncDesiredConfiguration, PRODUCTION)
        assert legacy.desired_config == legacy_config


def test_callback_query_rejects_duplicates_conflicting_results_and_unknown_parameters():
    from werkzeug.datastructures import MultiDict
    from plugin.integrations.oauth import OAuthOperationError, validate_google_callback_query

    state = "s" * 43
    assert validate_google_callback_query(MultiDict([("state", state), ("code", "synthetic-code")])) == (
        state, "synthetic-code", None,
    )
    invalid_queries = (
        MultiDict([("state", state), ("state", state), ("code", "synthetic-code")]),
        MultiDict([("state", state), ("code", "synthetic-code"), ("error", "access_denied")]),
        MultiDict([("state", state), ("code", "synthetic-code"), ("next", "https://evil.example")]),
        MultiDict([("state", state), ("error", "access_denied"), ("error_description", "ignored"), ("code", "synthetic-code")]),
    )
    for query in invalid_queries:
        with pytest.raises(OAuthOperationError) as rejected:
            validate_google_callback_query(query)
        assert rejected.value.code == "authorization_invalid"


def test_authenticated_http_authorize_callback_and_status_use_persistent_encrypted_grant(oauth_db, monkeypatch):
    sessions, models, _keyring, extensions = oauth_db
    from types import SimpleNamespace
    from plugin.integrations.http import DriveAuthorizationResource, DriveConnectionStatusResource, DriveRootsResource, GoogleOAuthCallbackResource
    from plugin.integrations.service_auth import JWKSCache
    from plugin.storage.base import StorageCapabilities

    issuer = "https://sync.example.test"
    audience = "https://files.example.test/api/plugins/vfx_files/integrations/v1"
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        VFX_FILES_KITSUSYNC_ISSUER=issuer,
        VFX_FILES_KITSUSYNC_AUDIENCE=audience,
        VFX_FILES_KITSUSYNC_SUBJECT="synthetic-service",
        VFX_FILES_KITSUSYNC_CLIENT_ID="synthetic-client",
        VFX_FILES_STORAGE_MODE="google_drive",
        **SETTINGS,
    )
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()
    def b64(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    jwks = {"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "oauth-http-test",
        "n": b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
        "e": b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
    }]}
    app.extensions["vfx_files_kitsusync_jwks_cache"] = JWKSCache(issuer, fetcher=lambda _url: jwks)
    app.extensions.update(extensions)
    app.extensions["vfx_files_oauth_transport"] = OAuthTransport()
    class RootAdapter:
        def __init__(self):
            self.calls = 0
        def verify_root_candidate(self, root_id):
            self.calls += 1
            return StorageCapabilities(True, root_id, None, False, True, (root_id,))
    root_adapter = RootAdapter()
    app.extensions["vfx_files_storage_adapter_factory"] = lambda _connection: root_adapter
    app.extensions["sqlalchemy"] = SimpleNamespace(engine=sessions.kw["bind"])
    zou_module = ModuleType("zou")
    zou_app_module = ModuleType("zou.app")
    class RequestDatabase:
        @property
        def session(self):
            return g.db_session
    zou_app_module.db = RequestDatabase()
    @app.before_request
    def open_request_session():
        g.db_session = sessions()
    @app.teardown_request
    def close_request_session(_error):
        g.db_session.close()
    monkeypatch.setitem(sys.modules, "zou", zou_module)
    monkeypatch.setitem(sys.modules, "zou.app", zou_app_module)

    prefix = "/plugins/vfx_files"
    app.add_url_rule(prefix + "/integrations/v1/drive-connections/authorizations",
        view_func=DriveAuthorizationResource.as_view("oauth_authorize"))
    app.add_url_rule(prefix + "/integrations/v1/drive-connections/<connection_id>",
        view_func=DriveConnectionStatusResource.as_view("oauth_status"))
    app.add_url_rule(prefix + "/integrations/v1/drive-connections/<connection_id>/roots",
        view_func=DriveRootsResource.as_view("oauth_roots"))
    app.add_url_rule(prefix + "/oauth/google/callback", view_func=GoogleOAuthCallbackResource.as_view("oauth_callback"))
    client = app.test_client()
    authorize_headers = {
        "Authorization": "Bearer " + _service_bearer(private, issuer=issuer, audience=audience, scope="vfx.connections.authorize"),
        "X-KitsuSync-Actor-ID": str(ACTOR), "Idempotency-Key": "oauth-http-idempotency-001",
    }
    authorized = client.post(prefix + "/integrations/v1/drive-connections/authorizations", json={
        "contract_version": "1.0", "production_id": str(PRODUCTION), "account_mode": "my_drive",
    }, headers=authorize_headers)
    assert authorized.status_code == 201, authorized.get_data(as_text=True)
    assert set(authorized.json) == {"contract_version", "authorization_id", "authorization_url", "expires_at"}
    assert "synthetic-client-secret" not in authorized.get_data(as_text=True)
    state, _params = _state_from_response(authorized.json)
    status_url = prefix + f"/integrations/v1/drive-connections/{authorized.json['authorization_id']}?production_id={PRODUCTION}"
    status_headers = {
        "Authorization": "Bearer " + _service_bearer(private, issuer=issuer, audience=audience, scope="vfx.connections.read"),
        "X-KitsuSync-Actor-ID": str(ACTOR),
    }
    before = client.get(status_url, headers=status_headers)
    assert before.status_code == 200 and before.json["state"] == "not_configured"

    callback = client.get(prefix + "/oauth/google/callback", query_string={"state": state, "code": "synthetic-code"})
    assert callback.status_code == 303
    assert callback.headers["Cache-Control"] == "no-store"
    assert callback.headers["Referrer-Policy"] == "no-referrer"
    assert callback.headers["Location"].startswith(SETTINGS["VFX_FILES_KITSUSYNC_RETURN_URI"])
    assert "synthetic-code" not in callback.headers["Location"]
    after = client.get(status_url, headers=status_headers)
    assert after.status_code == 200 and after.json["state"] == "connected"
    root_headers = {
        "Authorization": "Bearer " + _service_bearer(private, issuer=issuer, audience=audience, scope="vfx.roots.verify"),
        "X-KitsuSync-Actor-ID": str(ACTOR), "Idempotency-Key": "oauth-root-verify-idempotency-001",
    }
    verified_root = client.post(
        prefix + f"/integrations/v1/drive-connections/{authorized.json['authorization_id']}/roots",
        json={"contract_version": "1.0", "account_mode": "my_drive", "production_id": str(PRODUCTION), "root_id": "synthetic-root-01"},
        headers=root_headers,
    )
    assert verified_root.status_code == 200, verified_root.get_data(as_text=True)
    assert verified_root.json["owner_subject"] == "oidc-subject-a"
    replayed_root = client.post(
        prefix + f"/integrations/v1/drive-connections/{authorized.json['authorization_id']}/roots",
        json={"contract_version": "1.0", "account_mode": "my_drive", "production_id": str(PRODUCTION), "root_id": "synthetic-root-01"},
        headers=root_headers,
    )
    assert replayed_root.status_code == 200 and replayed_root.json == verified_root.json
    assert root_adapter.calls == 1
    conflicting_root = client.post(
        prefix + f"/integrations/v1/drive-connections/{authorized.json['authorization_id']}/roots",
        json={"contract_version": "1.0", "account_mode": "my_drive", "production_id": str(PRODUCTION), "root_id": "different-root"},
        headers=root_headers,
    )
    assert conflicting_root.status_code == 409 and conflicting_root.json["code"] == "idempotency_conflict"
    with sessions() as session:
        grant = session.get(models.DriveCredential, UUID(authorized.json["authorization_id"]))
        connection = session.get(models.StorageConnection, UUID(authorized.json["authorization_id"]))
        assert grant.state == "active" and grant.ciphertext
        assert b"synthetic-refresh-token" not in grant.ciphertext
        assert connection.enabled is True and connection.allowed_root_id == "synthetic-root-01"


def test_idempotency_key_cannot_be_replayed_by_another_actor(oauth_db):
    sessions, _models, _keyring, extensions = oauth_db
    from plugin.integrations.oauth import OAuthOperationError, start_authorization

    first, _ = _intent(sessions, extensions)
    with sessions() as session:
        with pytest.raises(OAuthOperationError) as conflict:
            start_authorization(
                session,
                payload={"contract_version": "1.0", "production_id": str(PRODUCTION), "account_mode": "my_drive"},
                actor_id=UUID("20000000-0000-4000-8000-000000000003"),
                idempotency_key="oauth-idempotency-key-01", settings=SETTINGS, extensions=extensions,
            )
    assert conflict.value.code == "idempotency_conflict"
    assert first["authorization_id"]


def test_callback_requires_identity_and_credentials_never_persist_plaintext(oauth_db):
    sessions, models, keyring, extensions = oauth_db
    from plugin.integrations.oauth import complete_authorization

    intent, _ = _intent(sessions, extensions)
    state, _params = _state_from_response(intent)
    return_url = complete_authorization(
        sessions, state=state, code="synthetic-code", oauth_error=None,
        settings=SETTINGS, extensions=extensions, transport=OAuthTransport(),
    )
    assert return_url.startswith("https://sync.example.test/admin/vfx-files/authorization-return?")
    assert "synthetic-code" not in return_url and "synthetic-refresh-token" not in return_url
    connection_id = UUID(intent["authorization_id"])
    with sessions() as session:
        credential = session.get(models.DriveCredential, connection_id)
        connection = session.get(models.StorageConnection, connection_id)
        assert credential.state == "active"
        assert credential.account_subject == "oidc-subject-a"
        assert credential.owner_subject == "oidc-subject-a"
        assert connection.owner_subject == "oidc-subject-a"
        assert connection.enabled is False  # No operations until root verification.
        assert b"synthetic-refresh-token" not in credential.ciphertext
        secret = keyring.decrypt(credential.key_id, credential.nonce, credential.ciphertext,
            f"vfx-files:drive-credential:1:{PRODUCTION}:{connection_id}:oidc-subject-a".encode())
        assert secret["refresh_token"] == "synthetic-refresh-token"
        assert secret["access_token"] == "synthetic-access-token"
    with pytest.raises(Exception, match="authorization_replayed"):
        complete_authorization(
            sessions, state=state, code="synthetic-code", oauth_error=None,
            settings=SETTINGS, extensions=extensions, transport=OAuthTransport(),
        )


def test_account_switch_is_rejected_for_existing_production_connection(oauth_db):
    sessions, models, _keyring, extensions = oauth_db
    from plugin.integrations.oauth import OAuthOperationError, complete_authorization

    first, _ = _intent(sessions, extensions)
    first_state, _ = _state_from_response(first)
    complete_authorization(sessions, state=first_state, code="code-a", oauth_error=None,
        settings=SETTINGS, extensions=extensions, transport=OAuthTransport(subject="subject-a"))
    second, _ = _intent(sessions, extensions, connection_id=UUID(first["authorization_id"]), key="oauth-idempotency-key-02")
    second_state, _ = _state_from_response(second)
    with pytest.raises(OAuthOperationError) as mismatch:
        complete_authorization(sessions, state=second_state, code="code-b", oauth_error=None,
            settings=SETTINGS, extensions=extensions, transport=OAuthTransport(subject="subject-b"))
    assert mismatch.value.code == "account_mismatch"
    with sessions() as session:
        credential = session.get(models.DriveCredential, UUID(first["authorization_id"]))
        assert credential.account_subject == "subject-a"


def test_callback_rejects_unrequested_google_scopes(oauth_db):
    sessions, models, _keyring, extensions = oauth_db
    from plugin.integrations.oauth import OAuthOperationError, complete_authorization

    intent, _ = _intent(sessions, extensions)
    state, _ = _state_from_response(intent)
    transport = OAuthTransport(granted_scopes="openid https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/drive")
    with pytest.raises(OAuthOperationError) as error:
        complete_authorization(
            sessions, state=state, code="synthetic-code", oauth_error=None,
            settings=SETTINGS, extensions=extensions, transport=transport,
        )
    assert error.value.code == "authorization_failed"
    assert len(transport.calls) == 1
    with sessions() as session:
        assert session.get(models.DriveCredential, UUID(intent["authorization_id"])) is None


def test_consent_is_requested_only_after_stored_grant_requires_reauthorization(oauth_db):
    sessions, models, _keyring, extensions = oauth_db
    from plugin.integrations.oauth import OAuthOperationError, complete_authorization

    first, _ = _intent(sessions, extensions)
    first_state, _ = _state_from_response(first)
    complete_authorization(sessions, state=first_state, code="code-a", oauth_error=None,
        settings=SETTINGS, extensions=extensions, transport=OAuthTransport(subject="subject-a"))
    connection_id = UUID(first["authorization_id"])
    with sessions.begin() as session:
        session.get(models.DriveCredential, connection_id).state = "reauth_required"

    renewal, _ = _intent(
        sessions, extensions, connection_id=connection_id, key="oauth-idempotency-key-02",
    )
    renewal_state, params = _state_from_response(renewal)
    assert params["prompt"] == ["consent"]
    with pytest.raises(OAuthOperationError) as missing_grant:
        complete_authorization(sessions, state=renewal_state, code="code-b", oauth_error=None,
            settings=SETTINGS, extensions=extensions, transport=OAuthTransport(subject="subject-a", refresh_token=None))
    assert missing_grant.value.code == "reauthorization_required"


def test_active_connection_reauthorization_does_not_force_consent(oauth_db):
    sessions, _models, _keyring, extensions = oauth_db
    from plugin.integrations.oauth import complete_authorization

    first, _ = _intent(sessions, extensions)
    first_state, _ = _state_from_response(first)
    complete_authorization(sessions, state=first_state, code="code-a", oauth_error=None,
        settings=SETTINGS, extensions=extensions, transport=OAuthTransport(subject="subject-a"))
    renewal, _ = _intent(
        sessions, extensions, connection_id=UUID(first["authorization_id"]), key="oauth-idempotency-key-02",
    )
    _state, params = _state_from_response(renewal)
    assert "prompt" not in params


def test_database_refresh_is_cached_rotates_refresh_token_and_serializes_threads(oauth_db):
    sessions, models, keyring, extensions = oauth_db
    from plugin.integrations.oauth import DatabaseDriveTokenProvider, complete_authorization

    intent, _ = _intent(sessions, extensions)
    state, _ = _state_from_response(intent)
    complete_authorization(sessions, state=state, code="synthetic-code", oauth_error=None,
        settings=SETTINGS, extensions=extensions, transport=OAuthTransport())
    connection_id = UUID(intent["authorization_id"])
    transport = OAuthTransport()
    provider = DatabaseDriveTokenProvider(sessions, connection_id, keyring, "synthetic-client-id", "synthetic-client-secret", transport)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _i: provider(), range(16)))
    assert set(tokens) == {"synthetic-access-token"}  # Valid callback token reused without refresh.
    with sessions() as session:
        credential = session.get(models.DriveCredential, connection_id)
        secret = keyring.decrypt(credential.key_id, credential.nonce, credential.ciphertext,
            f"vfx-files:drive-credential:1:{PRODUCTION}:{connection_id}:oidc-subject-a".encode())
        assert secret["refresh_token"] == "synthetic-refresh-token"
        secret["access_token_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        credential.key_id, credential.nonce, credential.ciphertext = keyring.encrypt(
            secret, f"vfx-files:drive-credential:1:{PRODUCTION}:{connection_id}:oidc-subject-a".encode(),
        )
        session.commit()
    provider._expires_at = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        refreshed = list(pool.map(lambda _i: provider(), range(16)))
    assert set(refreshed) == {"refreshed-access-token"}
    assert sum(1 for method, url, kwargs in transport.calls if url.endswith("/token") and kwargs.get("data", {}).get("grant_type") == "refresh_token") == 1
    with sessions() as session:
        credential = session.get(models.DriveCredential, connection_id)
        secret = keyring.decrypt(credential.key_id, credential.nonce, credential.ciphertext,
            f"vfx-files:drive-credential:1:{PRODUCTION}:{connection_id}:oidc-subject-a".encode())
        assert secret["refresh_token"] == "rotated-refresh-token"

    # A second API/worker process may receive a 401 for the prior token after
    # the first process already rotated it. It must reuse the DB's newer token
    # instead of refreshing the same grant again.
    second_process = DatabaseDriveTokenProvider(
        sessions, connection_id, keyring, "synthetic-client-id", "synthetic-client-secret", transport,
    )
    assert second_process.refresh_after_unauthorized("synthetic-access-token") == "refreshed-access-token"
    assert sum(1 for method, url, kwargs in transport.calls if url.endswith("/token") and kwargs.get("data", {}).get("grant_type") == "refresh_token") == 1


def test_cached_access_token_is_rejected_after_cross_process_credential_revocation(oauth_db):
    sessions, models, keyring, extensions = oauth_db
    from plugin.integrations.oauth import DatabaseDriveTokenProvider, complete_authorization
    from plugin.storage.base import PermissionFailure

    intent, _ = _intent(sessions, extensions)
    state, _ = _state_from_response(intent)
    complete_authorization(sessions, state=state, code="synthetic-code", oauth_error=None,
        settings=SETTINGS, extensions=extensions, transport=OAuthTransport())
    connection_id = UUID(intent["authorization_id"])
    transport = OAuthTransport()
    provider = DatabaseDriveTokenProvider(sessions, connection_id, keyring, "synthetic-client-id", "synthetic-client-secret", transport)
    assert provider() == "synthetic-access-token"
    with sessions.begin() as session:
        session.get(models.DriveCredential, connection_id).state = "revoking"
    with pytest.raises(PermissionFailure):
        provider()
    assert transport.calls == []


def test_expired_authorization_state_is_consumed_and_cannot_be_replayed(oauth_db):
    sessions, models, _keyring, extensions = oauth_db
    from plugin.integrations.oauth import OAuthOperationError, complete_authorization

    intent, _ = _intent(sessions, extensions)
    state, _ = _state_from_response(intent)
    with sessions() as session:
        row = session.get(models.DriveAuthorizationIntent, UUID(intent["authorization_id"]))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    expected = ("authorization_expired", "authorization_replayed")
    for code in expected:
        with pytest.raises(OAuthOperationError) as expired:
            complete_authorization(
                sessions, state=state, code="synthetic-code", oauth_error=None,
                settings=SETTINGS, extensions=extensions, transport=OAuthTransport(),
            )
        assert expired.value.code == code
    with sessions() as session:
        row = session.get(models.DriveAuthorizationIntent, UUID(intent["authorization_id"]))
        assert row.status == "expired" and row.consumed_at is not None


def test_invalid_refresh_fails_closed_without_leaking_provider_body(oauth_db):
    sessions, models, keyring, extensions = oauth_db
    from plugin.integrations.oauth import DatabaseDriveTokenProvider, complete_authorization
    from plugin.storage.base import PermissionFailure

    intent, _ = _intent(sessions, extensions)
    state, _ = _state_from_response(intent)
    complete_authorization(sessions, state=state, code="synthetic-code", oauth_error=None,
        settings=SETTINGS, extensions=extensions, transport=OAuthTransport())
    connection_id = UUID(intent["authorization_id"])
    with sessions() as session:
        session.add(models.KitsuSyncDesiredConfiguration(
            production_id=PRODUCTION,
            config_revision=6,
            notification_epoch=4,
            desired_config={"contract_version": "1.0", "activation": {"enabled": True, "activated_at": "synthetic"}},
        ))
        session.add(models.ProductionDriveAuthority(
            production_id=PRODUCTION, owner="plugin", state="configured",
            config_revision=3, activation_epoch=8, connection_id=connection_id,
            activation_enabled=True,
        ))
        session.commit()
    with sessions() as session:
        credential = session.get(models.DriveCredential, connection_id)
        secret = keyring.decrypt(credential.key_id, credential.nonce, credential.ciphertext,
            f"vfx-files:drive-credential:1:{PRODUCTION}:{connection_id}:oidc-subject-a".encode())
        secret["access_token_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        credential.key_id, credential.nonce, credential.ciphertext = keyring.encrypt(
            secret, f"vfx-files:drive-credential:1:{PRODUCTION}:{connection_id}:oidc-subject-a".encode(),
        )
        session.commit()
    provider = DatabaseDriveTokenProvider(sessions, connection_id, keyring, "client", "secret", OAuthTransport(refresh_status=400))
    provider._expires_at = 0
    with pytest.raises(PermissionFailure) as rejected:
        provider()
    assert "synthetic provider error" not in str(rejected.value)
    with sessions() as session:
        credential = session.get(models.DriveCredential, connection_id)
        connection = session.get(models.StorageConnection, connection_id)
        authority = session.get(models.ProductionDriveAuthority, PRODUCTION)
        desired = session.get(models.KitsuSyncDesiredConfiguration, PRODUCTION)
        assert credential.state == "reauth_required"
        assert connection.enabled is False
        assert authority.activation_enabled is False
        assert authority.activation_epoch == 9
        assert authority.config_revision == 4
        assert desired.config_revision == 7
        assert desired.notification_epoch == 5
        assert desired.desired_config["activation"]["enabled"] is False


def test_key_rotation_reencrypts_pending_and_completed_intents(oauth_db):
    sessions, models, old_keyring, extensions = oauth_db
    from plugin.integrations.oauth import CredentialKeyring, complete_authorization, rotate_configured_encrypted_records

    pending, _ = _intent(sessions, extensions)
    first, _ = _intent(sessions, extensions, key="oauth-idempotency-key-02")
    first_state, _ = _state_from_response(first)
    complete_authorization(sessions, state=first_state, code="synthetic-code", oauth_error=None,
        settings=SETTINGS, extensions=extensions, transport=OAuthTransport())
    new_keyring = CredentialKeyring("test-key-2", {"test-key-1": b"k" * 32, "test-key-2": b"n" * 32})
    changed = rotate_configured_encrypted_records(
        sessions,
        {"VFX_FILES_CREDENTIAL_PREVIOUS_KEY_ID": "test-key-1"},
        {"vfx_files_credential_keyring": new_keyring},
    )
    assert changed == 3  # One credential and two intent rows.
    with sessions() as session:
        intent = session.get(models.DriveAuthorizationIntent, UUID(pending["authorization_id"]))
        assert intent.key_id == "test-key-2"
        assert new_keyring.decrypt(intent.key_id, intent.response_nonce, intent.response_ciphertext,
            f"vfx-files:authorization-response:1:{PRODUCTION}:{intent.connection_id}:{ACTOR}".encode())["authorization_id"] == pending["authorization_id"]


@pytest.mark.parametrize(("revoke_status", "expected_state", "credential_state"), [
    (200, "completed", "revoked"),
    (503, "retry", "revoking"),
    (400, "reconcile_required", "revoke_pending"),
])
def test_durable_revoke_disables_locally_and_only_erases_after_confirmed_provider_response(oauth_db, revoke_status, expected_state, credential_state):
    sessions, models, keyring, extensions = oauth_db
    from types import SimpleNamespace
    from plugin.integrations.oauth import complete_authorization, make_drive_credential_revoke_handler

    intent, _ = _intent(sessions, extensions)
    state, _ = _state_from_response(intent)
    complete_authorization(sessions, state=state, code="synthetic-code", oauth_error=None,
        settings=SETTINGS, extensions=extensions, transport=OAuthTransport())
    connection_id = UUID(intent["authorization_id"])
    worker_id, job_id = uuid4(), uuid4()
    payload = {"job_type": "revoke_drive_credential", "connection_id": str(connection_id), "production_id": str(PRODUCTION)}
    with sessions.begin() as session:
        credential = session.get(models.DriveCredential, connection_id)
        connection = session.get(models.StorageConnection, connection_id)
        credential.state = "revoking"
        connection.enabled = False
        session.add(models.Job(
            id=job_id, job_type="revoke_drive_credential", idempotency_key="revoke-test-job-00001",
            payload=payload, state="leased", lease_owner=worker_id, lease_generation=2,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        ))

    class RevokeTransport(OAuthTransport):
        def request(self, method, url, **kwargs):
            if url.endswith("/revoke"):
                return FakeResponse(revoke_status, {"error": "synthetic failure"})
            return super().request(method, url, **kwargs)

    handler = make_drive_credential_revoke_handler(sessions, SETTINGS, extensions, transport=RevokeTransport())
    result = handler(SimpleNamespace(
        payload=payload, job_id=job_id, worker_id=worker_id, generation=2,
    ))
    assert result.state == expected_state
    with sessions() as session:
        credential = session.get(models.DriveCredential, connection_id)
        connection = session.get(models.StorageConnection, connection_id)
        assert connection.enabled is False
        assert credential.state == credential_state
        assert (credential.ciphertext is None) == (expected_state == "completed")
