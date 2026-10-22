"""M3: personal Google OAuth lifecycle, permissions and security. Mock Google transport only; no real Google."""

import hashlib
import io
from base64 import urlsafe_b64encode
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

BASE = "/plugins/vfx_files"
ORIGIN = "https://kitsu.test"
SCOPE = "openid https://www.googleapis.com/auth/drive.file"
RETURN_URI = "https://kitsu.example.invalid/api/plugins/vfx_files/frontend/"


class _R:
    def __init__(self, status_code, body=None, content=b""):
        self.status_code, self._body, self.content, self.headers = status_code, body or {}, content, {}

    def json(self):
        return self._body

    def iter_content(self, chunk_size):
        yield self.content


class GoogleMock:
    """Records every provider call; behaviour can be bent per test."""

    def __init__(self):
        self.subject = f"google-sub-{uuid4().hex}"
        self.scope = SCOPE
        self.refresh_token = f"rt-{uuid4().hex}"
        self.access_counter = 0
        self.token_status = 200
        self.refresh_status = 200
        self.revoke_status = 200
        self.userinfo = "ok"
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/token"):
            grant = kwargs["data"]["grant_type"]
            if grant == "authorization_code":
                if self.token_status != 200:
                    return _R(self.token_status)
                self.access_counter += 1
                return _R(200, {"access_token": f"at-{self.access_counter}", "refresh_token": self.refresh_token,
                                "expires_in": 3600, "scope": self.scope})
            if self.refresh_status != 200:
                return _R(self.refresh_status, {"error": "invalid_grant"})
            self.access_counter += 1
            return _R(200, {"access_token": f"at-{self.access_counter}", "expires_in": 3600})
        if url.endswith("/userinfo"):
            return _R(200, {"sub": self.subject}) if self.userinfo == "ok" else _R(200, {})
        if url.endswith("/revoke"):
            return _R(self.revoke_status)
        if "/drive/v3/files/" in url:
            self.drive_methods = getattr(self, "drive_methods", []) + [method]
            if kwargs.get("params", {}).get("alt") == "media":
                return _R(200, content=b"personal-original-bytes")
            return _R(200, {"id": url.rsplit("/", 1)[1], "name": "n", "mimeType": "image/x-exr", "trashed": False})
        raise AssertionError(f"unexpected Google call {method} {url}")

    def calls_to(self, suffix):
        return [call for call in self.calls if call[1].endswith(suffix)]


def _keyring():
    from plugin.integrations.oauth import CredentialKeyring

    return CredentialKeyring("pg-key", {"pg-key": b"p" * 32})


@pytest.fixture
def pg(authenticated_plugin_client, synthetic_upload_task, zou_db_factory, monkeypatch):
    class Ctx:
        pass

    c = Ctx()
    app = authenticated_plugin_client.vfx_app
    c.app, c.db, c.admin_client, c.production_id = app, zou_db_factory, authenticated_plugin_client, synthetic_upload_task[0]
    c.google, c.keyring, c.monkeypatch = GoogleMock(), _keyring(), monkeypatch
    for key, value in {
        "VFX_FILES_GOOGLE_CLIENT_ID": "synthetic-client-id", "VFX_FILES_GOOGLE_CLIENT_SECRET": "synthetic-client-secret",
        "VFX_FILES_GOOGLE_OAUTH_REDIRECT_URI": "https://files.example.invalid/api/plugins/vfx_files/oauth/google/callback",
        "VFX_FILES_KITSU_PLUGIN_RETURN_URI": RETURN_URI,
    }.items():
        monkeypatch.setitem(app.config, key, value)
    monkeypatch.setitem(app.extensions, "vfx_files_credential_keyring", c.keyring)
    monkeypatch.setitem(app.extensions, "vfx_files_oauth_transport", c.google)
    c.users = []
    yield c
    from sqlalchemy import text

    with zou_db_factory.begin() as session:
        session.execute(text("DELETE FROM plugin_vfx_files_google_user_authorization_intent"))
        session.execute(text("DELETE FROM plugin_vfx_files_google_user_credential"))
        session.execute(text("DELETE FROM plugin_vfx_files_production_output_setting WHERE production_id = :p"),
                        {"p": str(c.production_id)})


def _member(c, role="user"):
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink
    from zou.app.services.persons_service import create_access_token_for_raw_person

    with c.app.app_context(), c.db.begin() as session:
        person = Person(first_name=f"Pg{uuid4().hex[:8]}", last_name=role.title(), role="user", active=True)
        session.add(person)
        session.flush()
        session.add(ProjectPersonLink(project_id=c.production_id, person_id=person.id, role=role))
        person_id = person.id
    with c.app.app_context():
        token = create_access_token_for_raw_person(Person.query.filter_by(id=person_id).one())
    client = c.app.test_client()
    client.set_cookie(c.app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), token)
    return person_id, client


def _hdr(client):
    return {"Origin": ORIGIN, "X-VFX-Files-CSRF": client.get(f"{BASE}/csrf-token").json["csrf_token"]}


def _start(c, client, key=None):
    return client.post(f"{BASE}/productions/{c.production_id}/personal-google/authorizations", json={},
                       headers={**_hdr(client), "Idempotency-Key": key or f"personal-google:{uuid4()}"})


def _state(response):
    return parse_qs(urlsplit(response.json["authorization_url"]).query)["state"][0]


def _callback(client, state, code="synthetic-code", **extra):
    query = {"state": state, **({"code": code} if code else {}), **extra}
    return client.get(f"{BASE}/oauth/google/callback", query_string=query)


def _connect(c, client):
    started = _start(c, client)
    assert started.status_code == 201, started.get_data(as_text=True)
    done = _callback(client, _state(started))
    assert done.status_code == 303 and "personal_google=connected" in done.headers["Location"], done.headers.get("Location")
    return started


def _status(c, client):
    response = client.get(f"{BASE}/productions/{c.production_id}/personal-google")
    assert response.status_code == 200
    return response.json


def test_owner_can_start_connect_check_and_disconnect_without_token_leakage(pg):
    from sqlalchemy import select
    from plugin.models import GoogleUserCredential

    actor_id, client = _member(pg)
    assert _status(pg, client)["state"] == "not_linked" and _status(pg, client)["allowed"] is True
    started = _start(pg, client)
    assert started.status_code == 201
    query = parse_qs(urlsplit(started.json["authorization_url"]).query)
    assert query["scope"] == [SCOPE] and query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
    assert query["redirect_uri"] == ["https://files.example.invalid/api/plugins/vfx_files/oauth/google/callback"]
    # Same idempotency key replays the same authorization.
    key = "personal-google:replay-0000000001"
    first, second = _start(pg, client, key), _start(pg, client, key)
    assert (first.status_code, second.status_code) == (201, 200) and first.json == second.json

    done = _callback(client, _state(started))
    assert done.status_code == 303 and done.headers["Location"].startswith(RETURN_URI)
    for forbidden in ("at-1", pg.google.refresh_token, "synthetic-code", "access_token", "refresh_token"):
        assert forbidden not in done.headers["Location"]
    status = _status(pg, client)
    assert status["linked"] is True and status["state"] == "active" and status["account_hint"] == f"…{pg.google.subject[-4:]}"
    blob = " ".join(response.get_data(as_text=True) for response in (
        client.get(f"{BASE}/productions/{pg.production_id}/personal-google"), started,
        client.post(f"{BASE}/productions/{pg.production_id}/personal-google/check", json={}, headers=_hdr(client))))
    for secret in (pg.google.refresh_token, pg.google.subject, "at-1", "at-2", "synthetic-client-secret"):
        assert secret not in blob

    with pg.db.begin() as session:
        row = session.scalar(select(GoogleUserCredential).where(GoogleUserCredential.actor_id == actor_id))
        assert row.state == "active" and row.google_subject == pg.google.subject
        assert pg.google.refresh_token.encode() not in bytes(row.ciphertext)
        assert pg.keyring.decrypt(row.key_id, row.nonce, row.ciphertext,
                                  f"vfx-files:user-drive-credential:1:{actor_id}:{pg.google.subject}".encode())["refresh_token"] == pg.google.refresh_token
        # Binding: the encrypted blob cannot be decrypted as another user's.
        with pytest.raises(Exception):
            pg.keyring.decrypt(row.key_id, row.nonce, row.ciphertext,
                               f"vfx-files:user-drive-credential:1:{uuid4()}:{pg.google.subject}".encode())

    gone = client.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(client))
    assert gone.status_code == 200 and gone.json == {"state": "revoked"}
    assert pg.google.calls_to("/revoke")[0][2]["data"] == {"token": pg.google.refresh_token}
    assert _status(pg, client)["state"] == "not_linked"
    with pg.db.begin() as session:
        row = session.scalar(select(GoogleUserCredential).where(GoogleUserCredential.actor_id == actor_id))
        assert row.ciphertext is None and row.google_subject is None


def test_pkce_state_scope_and_subject_are_enforced_and_state_is_single_use(pg):
    from sqlalchemy import select
    from plugin.models import GoogleUserAuthorizationIntent, GoogleUserCredential

    actor_id, client = _member(pg)
    started = _start(pg, client)
    state = _state(started)
    challenge = parse_qs(urlsplit(started.json["authorization_url"]).query)["code_challenge"][0]
    with pg.db.begin() as session:
        intent = session.scalar(select(GoogleUserAuthorizationIntent).where(GoogleUserAuthorizationIntent.actor_id == actor_id))
        verifier = pg.keyring.decrypt(intent.key_id, intent.verifier_nonce, intent.verifier_ciphertext,
                                      f"vfx-files:user-oauth-verifier:1:{actor_id}:{intent.id}:{actor_id}".encode())["verifier"]
        assert intent.state_sha256 == hashlib.sha256(state.encode()).hexdigest() and state not in str(intent.state_sha256)
    assert urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") == challenge

    assert _callback(client, state).status_code == 303
    exchange = pg.google.calls_to("/token")[0][2]["data"]
    assert exchange["code_verifier"] == verifier and exchange["grant_type"] == "authorization_code"
    replay = _callback(client, state)
    assert replay.status_code == 303 and "error=authorization_replayed" in replay.headers["Location"]
    assert len(pg.google.calls_to("/token")) == 1  # the replay never reached Google
    assert _callback(client, "x" * 40, "code").status_code in (302, 303, 400)

    # Wrong scope: nothing stored. Extra scope (e.g. full drive) is refused, not silently accepted.
    other_id, other = _member(pg)
    pg.google.scope = SCOPE + " https://www.googleapis.com/auth/drive"
    bad = _start(pg, other)
    result = _callback(other, _state(bad))
    assert "error=authorization_failed" in result.headers["Location"]
    # Missing subject.
    pg.google.scope, pg.google.userinfo = SCOPE, "no-sub"
    result = _callback(other, _state(_start(pg, other)))
    assert "error=identity_unverified" in result.headers["Location"]
    # Google denied consent.
    pg.google.userinfo = "ok"
    result = _callback(other, _state(_start(pg, other)), code=None, error="access_denied")
    assert "error=authorization_denied" in result.headers["Location"]
    with pg.db.begin() as session:
        assert session.get(GoogleUserCredential, other_id) is None
        assert session.get(GoogleUserCredential, actor_id).state == "active"


def test_users_cannot_see_or_affect_each_others_links_or_mix_up_accounts(pg):
    a_id, a = _member(pg)
    b_id, b = _member(pg)
    _connect(pg, a)
    assert _status(pg, b)["state"] == "not_linked"  # status is strictly the caller's own
    assert b.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(b)).json == {"state": "revoked"}
    assert _status(pg, a)["state"] == "active"  # B's disconnect touched nothing of A's
    assert not pg.google.calls_to("/revoke")

    # The same Google account cannot be linked to a second Kitsu user.
    result = _callback(b, _state(_start(pg, b)))
    assert "error=google_account_already_linked" in result.headers["Location"]
    assert _status(pg, b)["state"] == "not_linked"

    # Login-CSRF / account mix-up: a different signed-in user cannot complete A's pending authorization.
    c_id, c = _member(pg)
    pg.google.subject = f"google-sub-{uuid4().hex}"
    pending = _start(pg, c)
    mixed = _callback(a, _state(pending))
    assert "error=account_mismatch" in mixed.headers["Location"]
    assert _callback(c, _state(pending)).status_code == 303 and _status(pg, c)["state"] == "active"

    # Switching Google accounts needs an explicit disconnect first.
    pg.google.subject = f"google-sub-{uuid4().hex}"
    switch = _callback(c, _state(_start(pg, c)))
    assert "error=account_switch_requires_disconnect" in switch.headers["Location"]


def test_cookie_only_requests_are_rejected_for_every_mutation(pg):
    _actor, client = _member(pg)
    no_csrf = {"Origin": ORIGIN}
    foreign = {"Origin": "https://evil.example", "X-VFX-Files-CSRF": client.get(f"{BASE}/csrf-token").json["csrf_token"]}
    for headers in ({}, no_csrf, foreign):
        for url in (f"{BASE}/productions/{pg.production_id}/personal-google/authorizations",
                    f"{BASE}/productions/{pg.production_id}/personal-google/check",
                    f"{BASE}/me/google-account/disconnect",
                    f"{BASE}/productions/{pg.production_id}/personal-google-policy"):
            assert client.post(url, json={"policy": "blocked"}, headers=headers).status_code == 403
    assert not pg.google.calls


def test_production_admin_policy_blocks_use_but_keeps_existing_links(pg):
    from plugin.integrations.personal_google import PersonalSourceDenied, open_personal_source

    admin = pg.admin_client
    user_id, user = _member(pg)
    _connect(pg, user)
    assert _status(pg, user)["can_change_policy"] is False
    assert user.post(f"{BASE}/productions/{pg.production_id}/personal-google-policy", json={"policy": "blocked"},
                     headers=_hdr(user)).status_code == 403
    assert admin.post(f"{BASE}/productions/{pg.production_id}/personal-google-policy", json={"policy": "everyone"},
                      headers=_hdr(admin)).status_code == 400
    kwargs = dict(actor_id=user_id, production_id=pg.production_id, settings=pg.app.config,
                  extensions=pg.app.extensions, transport=pg.google)
    with pg.app.app_context():
        assert open_personal_source(pg.db, **kwargs) is not None

    response = admin.post(f"{BASE}/productions/{pg.production_id}/personal-google-policy", json={"policy": "blocked"},
                          headers=_hdr(admin))
    assert response.status_code == 200 and response.json == {"policy": "blocked"}
    blocked = _status(pg, user)
    assert blocked["allowed"] is False and blocked["state"] == "active"  # the link itself is kept
    started = _start(pg, user)
    assert started.status_code == 403 and b"personal_google_blocked" in started.data
    with pg.app.app_context():
        with pytest.raises(PersonalSourceDenied) as denied:
            open_personal_source(pg.db, **kwargs)
        assert denied.value.code == "personal_google_blocked"
    assert user.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(user)).status_code == 200  # still allowed

    # Blocking during an in-flight authorization: no grant is created.
    other_id, other = _member(pg)
    admin.post(f"{BASE}/productions/{pg.production_id}/personal-google-policy", json={"policy": "allowed"}, headers=_hdr(admin))
    pending = _start(pg, other)
    admin.post(f"{BASE}/productions/{pg.production_id}/personal-google-policy", json={"policy": "blocked"}, headers=_hdr(admin))
    result = _callback(other, _state(pending))
    assert "error=personal_google_blocked" in result.headers["Location"]
    assert _status(pg, other)["state"] == "not_linked" and len(pg.google.calls_to("/token")) == 1
    admin.post(f"{BASE}/productions/{pg.production_id}/personal-google-policy", json={"policy": "allowed"}, headers=_hdr(admin))
    assert _status(pg, admin)["can_change_policy"] is True


def test_leaving_the_production_denies_use_but_not_unlinking(pg):
    from plugin.integrations.personal_google import PersonalSourceDenied, open_personal_source
    from zou.app.models.project import ProjectPersonLink

    user_id, user = _member(pg)
    _connect(pg, user)
    with pg.app.app_context(), pg.db.begin() as session:
        session.query(ProjectPersonLink).filter_by(project_id=pg.production_id, person_id=user_id).delete()
    assert user.get(f"{BASE}/productions/{pg.production_id}/personal-google").status_code == 403
    assert _start(pg, user).status_code == 403
    assert user.post(f"{BASE}/productions/{pg.production_id}/personal-google/check", json={}, headers=_hdr(user)).status_code == 403
    with pg.app.app_context():
        with pytest.raises(PersonalSourceDenied) as denied:
            open_personal_source(pg.db, actor_id=user_id, production_id=pg.production_id, settings=pg.app.config,
                                 extensions=pg.app.extensions, transport=pg.google)
        assert denied.value.code == "not_a_production_member"
    assert user.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(user)).json == {"state": "revoked"}


def test_google_side_revocation_is_detected_and_reconnect_recovers(pg):
    from plugin.integrations.personal_google import PersonalSourceDenied, open_personal_source

    user_id, user = _member(pg)
    _connect(pg, user)
    pg.google.refresh_status = 400  # the user revoked access in their Google account
    checked = user.post(f"{BASE}/productions/{pg.production_id}/personal-google/check", json={}, headers=_hdr(user))
    assert checked.status_code == 200 and checked.json["check"] == "reauth_required" and checked.json["linked"] is False
    assert _status(pg, user)["state"] == "reauth_required"
    with pg.app.app_context():
        with pytest.raises(PersonalSourceDenied):
            open_personal_source(pg.db, actor_id=user_id, production_id=pg.production_id, settings=pg.app.config,
                                 extensions=pg.app.extensions, transport=pg.google)
    pg.google.refresh_status = 200
    assert _callback(user, _state(_start(pg, user))).status_code == 303
    assert _status(pg, user)["state"] == "active"


def test_partial_failures_are_safe_and_retryable(pg):
    from sqlalchemy import select
    from plugin.models import GoogleUserAuthorizationIntent, GoogleUserCredential

    user_id, user = _member(pg)
    pg.google.token_status = 500
    failed = _callback(user, _state(_start(pg, user)))
    assert "error=authorization_failed" in failed.headers["Location"]
    with pg.db.begin() as session:
        assert session.get(GoogleUserCredential, user_id) is None
        assert session.scalar(select(GoogleUserAuthorizationIntent.status).where(
            GoogleUserAuthorizationIntent.actor_id == user_id)) == "failed"
    pg.google.token_status = 200
    _connect(pg, user)

    pg.google.revoke_status = 503  # Google unavailable: access is disabled locally, the grant is kept for retry
    pending = user.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(user))
    assert pending.json == {"state": "revoke_pending"} and _status(pg, user)["linked"] is False
    pg.google.revoke_status = 200
    assert user.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(user)).json == {"state": "revoked"}
    # Expired intents cannot be completed.
    from datetime import datetime, timedelta, timezone
    stale = _start(pg, user)
    with pg.db.begin() as session:
        intent = session.scalar(select(GoogleUserAuthorizationIntent).where(GoogleUserAuthorizationIntent.status == "pending",
                                                                            GoogleUserAuthorizationIntent.actor_id == user_id))
        intent.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert "error=authorization_expired" in _callback(user, _state(stale)).headers["Location"]


def test_production_oauth_and_storage_are_separate_from_personal_grants(pg):
    from sqlalchemy import func, select
    from plugin.integrations.personal_google import PersonalDriveReader
    from plugin.models import DriveAuthorizationIntent, DriveCredential
    from plugin.storage.base import PermissionFailure, resolve_storage_adapter
    from plugin.storage.fake import FakeStorage

    with pg.db.begin() as session:
        before = (session.scalar(select(func.count()).select_from(DriveCredential)),
                  session.scalar(select(func.count()).select_from(DriveAuthorizationIntent)))
    user_id, user = _member(pg)
    _connect(pg, user)
    with pg.db.begin() as session:
        after = (session.scalar(select(func.count()).select_from(DriveCredential)),
                 session.scalar(select(func.count()).select_from(DriveAuthorizationIntent)))
    assert before == after  # personal grants never create or alter Production credentials/intents

    reader = PersonalDriveReader(lambda: "t", pg.google)
    with pytest.raises(PermissionFailure):
        resolve_storage_adapter(reader, object())
    with pytest.raises(PermissionFailure):
        resolve_storage_adapter(lambda _connection: reader, object())
    assert resolve_storage_adapter(FakeStorage(), object()) is not None  # FakeStorage unaffected
    for forbidden in ("upload", "delete", "create_folder", "capabilities", "assert_parent_within_root"):
        assert not hasattr(reader, forbidden)


def test_personal_reader_is_get_only_and_validates_ids(pg):
    from plugin.integrations.personal_google import PersonalDriveReader, PersonalSourceDenied, open_personal_source
    from plugin.storage.base import PermissionFailure

    user_id, user = _member(pg)
    _connect(pg, user)
    with pg.app.app_context():
        reader = open_personal_source(pg.db, actor_id=user_id, production_id=pg.production_id, settings=pg.app.config,
                                      extensions=pg.app.extensions, transport=pg.google)
    sink = io.BytesIO()
    file_id = "1AbCdEfGhIjKlMnOpQrStUv"
    assert reader.get_metadata(file_id)["id"] == file_id
    count, digest = reader.download_to(file_id, sink)
    assert sink.getvalue() == b"personal-original-bytes" and digest == hashlib.sha256(sink.getvalue()).hexdigest()
    assert set(pg.google.drive_methods) == {"GET"}  # no write/move/delete/share request is ever issued
    for bad in ("short", "has/slash/in-it-12345", "../x" * 5, None, "a" * 201):
        with pytest.raises(PersonalSourceDenied):
            reader.get_metadata(bad)

    class Denied(GoogleMock):
        def request(self, method, url, **kwargs):
            return _R(404) if "/drive/v3/files/" in url else super().request(method, url, **kwargs)

    with pytest.raises(PermissionFailure):
        PersonalDriveReader(lambda: "t", Denied()).get_metadata(file_id)


def test_key_rotation_reencrypts_personal_grants(pg):
    from sqlalchemy import select
    from plugin.integrations.oauth import CredentialKeyring
    from plugin.integrations.user_oauth import rotate_personal_records
    from plugin.models import GoogleUserCredential

    user_id, user = _member(pg)
    _connect(pg, user)
    new = CredentialKeyring("pg-key-2", {"pg-key": b"p" * 32, "pg-key-2": b"q" * 32})
    old = CredentialKeyring("pg-key", {"pg-key": b"p" * 32, "pg-key-2": b"q" * 32})
    with pg.db.begin() as session:  # the shared test DB holds unrelated Production rows; rotate only personal ones here
        assert rotate_personal_records(session, old_keyring=old, new_keyring=new) >= 1
    with pg.db.begin() as session:
        row = session.scalar(select(GoogleUserCredential).where(GoogleUserCredential.actor_id == user_id))
        assert row.key_id == "pg-key-2"
        assert new.decrypt(row.key_id, row.nonce, row.ciphertext,
                           f"vfx-files:user-drive-credential:1:{user_id}:{pg.google.subject}".encode())["refresh_token"] == pg.google.refresh_token


def test_database_constraints_for_personal_grants_and_policy(pg):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    for statement in (
        f"INSERT INTO plugin_vfx_files_production_output_setting (production_id, personal_google_policy) VALUES ('{uuid4()}', 'maybe')",
        f"INSERT INTO plugin_vfx_files_google_user_credential (actor_id, state) VALUES ('{uuid4()}', 'active')",
        f"INSERT INTO plugin_vfx_files_google_user_credential (actor_id, google_subject, state) VALUES ('{uuid4()}', 'x', 'revoked')",
    ):
        with pytest.raises(DBAPIError):
            with pg.db.begin() as session:
                session.execute(text(statement))


def test_policy_block_during_the_google_round_trip_rejects_before_any_grant_is_stored(pg):
    from sqlalchemy import select
    from plugin.integrations.user_oauth import set_personal_google_policy
    from plugin.models import GoogleUserAuthorizationIntent, GoogleUserCredential

    user_id, user = _member(pg)
    state = _state(_start(pg, user))

    for hook in ("/token", "/userinfo"):
        class Racing(GoogleMock):
            def request(self, method, url, **kwargs):
                response = super().request(method, url, **kwargs)
                if url.endswith(hook):  # the admin blocks while the exchange is in flight
                    with pg.db.begin() as session:
                        set_personal_google_policy(session, pg.production_id, "blocked", uuid4())
                return response

        racing = Racing()
        racing.subject, racing.refresh_token = pg.google.subject, pg.google.refresh_token
        pg.monkeypatch.setitem(pg.app.extensions, "vfx_files_oauth_transport", racing)
        if hook == "/userinfo":
            with pg.db.begin() as session:
                set_personal_google_policy(session, pg.production_id, "allowed", uuid4())
            state = _state(_start(pg, user, key=f"personal-google:{uuid4()}"))
        result = _callback(user, state)
        assert "error=personal_google_blocked" in result.headers["Location"], hook
        with pg.db.begin() as session:
            assert session.get(GoogleUserCredential, user_id) is None, hook
            assert "connected" not in {row for row in session.scalars(select(GoogleUserAuthorizationIntent.status).where(
                GoogleUserAuthorizationIntent.actor_id == user_id, GoogleUserAuthorizationIntent.state_sha256
                == hashlib.sha256(state.encode()).hexdigest()))}
        # The refresh token Google just issued was never stored, so it is revoked rather than left live.
        assert racing.calls_to("/revoke") and racing.calls_to("/revoke")[0][2]["data"] == {"token": racing.refresh_token}
        assert _status(pg, user)["state"] == "not_linked"

    # With an existing live grant of ours, a refused re-link must not revoke the stored one.
    with pg.db.begin() as session:
        set_personal_google_policy(session, pg.production_id, "allowed", uuid4())
    pg.monkeypatch.setitem(pg.app.extensions, "vfx_files_oauth_transport", pg.google)
    _connect(pg, user)
    revokes = len(pg.google.calls_to("/revoke"))
    with pg.db.begin() as session:
        set_personal_google_policy(session, pg.production_id, "blocked", uuid4())
    assert _status(pg, user)["state"] == "active"  # blocking never deletes the existing link
    assert len(pg.google.calls_to("/revoke")) == revokes


def test_revoke_pending_is_retried_automatically_by_the_shared_job_queue(pg):
    from datetime import timedelta
    from sqlalchemy import func, select
    from plugin.integrations.user_oauth import make_personal_revoke_handler
    from plugin.jobs.queue import LeasedJob
    from plugin.models import GoogleUserCredential, Job
    from plugin.worker_runtime import _authorize_job

    user_id, user = _member(pg)
    _connect(pg, user)
    pg.google.revoke_status = 503
    assert user.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(user)).json == {"state": "revoke_pending"}
    key = f"revoke-personal-google:{user_id}"

    def job():
        with pg.db.begin() as session:
            return session.scalar(select(Job).where(Job.idempotency_key == key))

    def lease():
        worker_id = uuid4()
        with pg.db.begin() as session:
            row = session.scalar(select(Job).where(Job.idempotency_key == key))
            row.state, row.lease_owner = "leased", worker_id
            row.lease_generation += 1
            row.attempts += 1
            row.lease_expires_at = session.scalar(select(func.now())) + timedelta(minutes=2)
            return LeasedJob(row.id, row.job_type, dict(row.payload), worker_id, row.lease_generation, row.attempts)

    queued = job()
    assert queued.job_type == "revoke_personal_google" and queued.state == "queued"
    # Repeating the Disconnect button does not duplicate the job.
    user.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(user))
    with pg.db.begin() as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.idempotency_key == key)) == 1

    with pg.app.app_context():
        assert _authorize_job(queued.payload) is True
        assert _authorize_job({**queued.payload, "actor_id": str(uuid4())}) is False  # no such grant
        assert _authorize_job({"job_type": "revoke_personal_google", "actor_id": "bad"}) is False
    handler = make_personal_revoke_handler(pg.db, pg.app.config, pg.app.extensions, transport=pg.google)
    still_down = handler(lease())
    assert (still_down.state, still_down.retry_after_seconds) == ("retry", 300) and _status(pg, user)["state"] == "revoke_pending"
    pg.google.revoke_status = 200  # Google is back
    assert handler(lease()).state == "completed"
    with pg.db.begin() as session:
        row = session.get(GoogleUserCredential, user_id)
        assert row.state == "revoked" and row.ciphertext is None and row.google_subject is None
    assert handler(lease()).state == "completed"  # replay after completion is idempotent
    with pg.app.app_context():
        assert _authorize_job(queued.payload) is True  # a finished (revoked) grant needs no further work and is harmless

    # An exhausted job is revived by pressing Disconnect again after a fresh failure.
    _connect(pg, user)
    pg.google.revoke_status = 503
    user.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(user))
    with pg.db.begin() as session:
        row = session.scalar(select(Job).where(Job.idempotency_key == key))
        row.state, row.attempts = "retry", row.max_attempts
    user.post(f"{BASE}/me/google-account/disconnect", json={}, headers=_hdr(user))
    revived = job()
    assert (revived.state, revived.attempts) == ("queued", 0)
    pg.google.revoke_status = 200
    assert handler(lease()).state == "completed" and _status(pg, user)["state"] == "not_linked"
