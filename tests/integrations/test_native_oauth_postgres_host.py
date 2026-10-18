"""Native Kitsu manager OAuth and root selection on pinned Zou/PostgreSQL."""

from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from plugin.authz import issue_csrf_token
from plugin.models import (
    DriveAuthorizationIntent,
    DriveCredential,
    KitsuSyncManagementIdempotency,
    ProductionDriveAuthority,
    ProductionDriveAuthorityEvent,
    StorageConnection,
)
from plugin.storage.base import Listing, StorageCapabilities, StorageObject


class SyntheticGoogleResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class SyntheticGoogleTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/token"):
            return SyntheticGoogleResponse({
                "access_token": "synthetic-native-access-token",
                "refresh_token": "synthetic-native-refresh-token",
                "expires_in": 3600,
                "scope": "openid https://www.googleapis.com/auth/drive.file",
            })
        if url.endswith("/userinfo"):
            return SyntheticGoogleResponse({"sub": "synthetic-native-manager"})
        raise AssertionError("Unexpected Google endpoint in synthetic transport")


class SyntheticMyDriveRoots:
    def __init__(self):
        self.root_id = "synthetic-native-root-01"
        self.verified = []

    def list_root_candidates(self):
        return Listing((StorageObject(
            object_id=self.root_id, parent_id=None, size=None, sha256=None,
            mime_type="folder", name="Synthetic Native Root",
        ),), complete=True)

    def verify_root_candidate(self, root_id):
        self.verified.append(root_id)
        return StorageCapabilities(
            can_add_children=True,
            root_id=root_id,
            drive_id=None,
            shared_drive=False,
            root_visible=root_id == self.root_id,
            root_ancestors=(root_id,),
        )


def test_native_kitsu_manager_authorizes_server_side_oauth_and_verified_root_on_postgres(
    zou_app, zou_db_factory,
):
    from zou.app.models.person import Person
    from zou.app.models.project import Project, ProjectPersonLink
    from zou.app.services.persons_service import create_access_token_for_raw_person

    from plugin.integrations.oauth import CredentialKeyring

    app = zou_app
    suffix = uuid4().hex[:12]
    manager = Person(
        first_name="Synthetic",
        last_name=f"NativeManager{suffix}",
        email=f"native-manager-{suffix}@example.invalid",
        role="user",
        active=True,
        archived=False,
    )
    artist = Person(
        first_name="Synthetic",
        last_name=f"NativeArtist{suffix}",
        email=f"native-artist-{suffix}@example.invalid",
        role="user",
        active=True,
        archived=False,
    )
    project = Project(name=f"Native Drive Test {suffix}", team=[manager, artist])
    with zou_db_factory.begin() as session:
        session.add_all([manager, artist, project])
        session.flush()
        manager_membership = session.get(ProjectPersonLink, (project.id, manager.id))
        artist_membership = session.get(ProjectPersonLink, (project.id, artist.id))
        manager_membership.role = "manager"
        artist_membership.role = "user"
        production_id, manager_id, artist_id = project.id, manager.id, artist.id

    app.config.update(
        TESTING=True,
        SECRET_KEY=f"synthetic-native-secret-{suffix}",
        JWT_SECRET_KEY=f"synthetic-native-jwt-secret-{suffix}",
        VFX_FILES_KITSU_ORIGIN="https://kitsu.native.example.test",
        VFX_FILES_GOOGLE_CLIENT_ID="synthetic-native-client",
        VFX_FILES_GOOGLE_CLIENT_SECRET="synthetic-native-client-secret",
        VFX_FILES_GOOGLE_OAUTH_REDIRECT_URI=(
            "https://files.native.example.test/api/plugins/vfx_files/oauth/google/callback"
        ),
        VFX_FILES_KITSU_PLUGIN_RETURN_URI=(
            "https://kitsu.native.example.test/api/plugins/vfx_files/frontend/"
        ),
        VFX_FILES_STORAGE_MODE="fake",
    )
    transport = SyntheticGoogleTransport()
    roots = SyntheticMyDriveRoots()
    app.extensions["vfx_files_credential_keyring"] = CredentialKeyring(
        "synthetic-native-key", {"synthetic-native-key": b"n" * 32},
    )
    app.extensions["vfx_files_oauth_transport"] = transport
    app.extensions["vfx_files_storage_adapter_factory"] = lambda _connection: roots

    # The test runner's Zou application is not booted with the Plugin package
    # manager. Bind the real native resource classes to the pinned host app so
    # authentication, Production role lookup, SQLAlchemy persistence and the
    # server-side OAuth flow all execute against Zou/PostgreSQL.
    from plugin import resources

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
        "/api/plugins/vfx_files/productions/<production_id>/drive/connections/<connection_id>/roots",
        view_func=resources.NativeDriveRoots.as_view("native_drive_roots"),
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/api/plugins/vfx_files/oauth/google/callback",
        view_func=resources.GoogleOAuthCallbackResource.as_view("native_google_callback"),
        methods=["GET"],
    )

    with app.app_context():
        from zou.app import db

        manager_token = create_access_token_for_raw_person(db.session.get(Person, manager_id))
        artist_token = create_access_token_for_raw_person(db.session.get(Person, artist_id))
    cookie_name = app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie")

    def client_for(token):
        client = app.test_client()
        client.set_cookie(cookie_name, token)
        return client

    manager_client = client_for(manager_token)
    artist_client = client_for(artist_token)
    base = f"/api/plugins/vfx_files/productions/{production_id}/drive"
    origin = app.config["VFX_FILES_KITSU_ORIGIN"]
    csrf = issue_csrf_token(app.config["SECRET_KEY"], manager_id)
    mutation_headers = {
        "Origin": origin,
        "X-VFX-Files-CSRF": csrf,
        "Content-Type": "application/json",
    }

    try:
        initial = manager_client.get(base + "/status")
        assert initial.status_code == 200, initial.get_data(as_text=True)
        assert initial.json["owner"] == "plugin"
        assert initial.json["state"] == "unconfigured"

        denied = artist_client.get(base + "/status")
        assert denied.status_code == 403

        authorization = manager_client.post(
            base + "/authorizations",
            json={"contract_version": "1.0", "account_mode": "my_drive"},
            headers={
                **mutation_headers,
                "Idempotency-Key": f"native-oauth-{suffix}-intent",
            },
        )
        assert authorization.status_code == 201, authorization.get_data(as_text=True)
        authorization_query = parse_qs(urlsplit(authorization.json["authorization_url"]).query)
        assert authorization_query["scope"] == [
            "openid https://www.googleapis.com/auth/drive.file"
        ]
        state = authorization_query["state"][0]

        callback = manager_client.get(
            "/api/plugins/vfx_files/oauth/google/callback",
            query_string={"state": state, "code": "synthetic-native-code"},
        )
        assert callback.status_code == 303
        assert callback.headers["Location"].startswith(
            app.config["VFX_FILES_KITSU_PLUGIN_RETURN_URI"]
        )
        assert "synthetic-native-refresh-token" not in callback.get_data(as_text=True)
        assert [url.rsplit("/", 1)[-1] for _method, url, _kwargs in transport.calls] == [
            "token", "userinfo",
        ]

        connection_id = authorization.json["authorization_id"]
        candidates_url = base + f"/connections/{connection_id}/roots"
        candidates = manager_client.get(candidates_url)
        assert candidates.status_code == 200
        assert candidates.json["roots"] == [{
            "id": roots.root_id,
            "name": "Synthetic Native Root",
            "account_mode": "my_drive",
        }]

        unlisted = manager_client.post(
            candidates_url,
            json={"contract_version": "1.0", "root_id": "not-listed"},
            headers={
                **mutation_headers,
                "Idempotency-Key": f"native-root-{suffix}-denied",
                "If-Match": '"0"',
            },
        )
        assert unlisted.status_code == 403

        selected = manager_client.post(
            candidates_url,
            json={"contract_version": "1.0", "root_id": roots.root_id},
            headers={
                **mutation_headers,
                "Idempotency-Key": f"native-root-{suffix}-select",
                "If-Match": '"0"',
            },
        )
        assert selected.status_code == 200, selected.get_data(as_text=True)
        assert selected.json["owner"] == "plugin"
        assert selected.json["root_id"] == roots.root_id
        assert selected.json["activation_enabled"] is False
        assert roots.verified == [roots.root_id]

        roots.root_id = "synthetic-native-root-02"
        updated_candidates = manager_client.get(candidates_url)
        assert updated_candidates.status_code == 200
        assert updated_candidates.json["roots"][0]["id"] == roots.root_id
        changed = manager_client.post(
            candidates_url,
            json={"contract_version": "1.0", "root_id": roots.root_id},
            headers={
                **mutation_headers,
                "Idempotency-Key": f"native-root-{suffix}-change",
                "If-Match": '"1"',
            },
        )
        assert changed.status_code == 200, changed.get_data(as_text=True)
        assert changed.json["root_id"] == roots.root_id
        assert changed.json["activation_enabled"] is False
        assert roots.verified == ["synthetic-native-root-01", roots.root_id]

        with zou_db_factory() as session:
            authority = session.get(ProductionDriveAuthority, production_id)
            connection = session.get(StorageConnection, connection_id)
            credential = session.get(DriveCredential, connection_id)
            assert authority.owner == "plugin"
            assert authority.state == "configured"
            assert authority.root_id == roots.root_id
            assert authority.config_revision == 2
            assert authority.activation_epoch == 2
            assert authority.activation_enabled is False
            assert connection.enabled is True
            assert connection.allowed_root_id == roots.root_id
            assert credential.state == "active"
            assert b"synthetic-native-refresh-token" not in credential.ciphertext
            assert session.query(DriveAuthorizationIntent).filter_by(
                production_id=production_id,
            ).count() == 1
    finally:
        with zou_db_factory.begin() as session:
            session.query(KitsuSyncManagementIdempotency).filter_by(
                production_id=production_id,
            ).delete(synchronize_session=False)
            session.query(ProductionDriveAuthorityEvent).filter_by(
                production_id=production_id,
            ).delete(synchronize_session=False)
            session.query(DriveAuthorizationIntent).filter_by(
                production_id=production_id,
            ).delete(synchronize_session=False)
            session.query(DriveCredential).filter_by(
                production_id=production_id,
            ).delete(synchronize_session=False)
            session.query(ProductionDriveAuthority).filter_by(
                production_id=production_id,
            ).delete(synchronize_session=False)
            session.query(StorageConnection).filter_by(
                production_id=production_id,
            ).delete(synchronize_session=False)
            session.query(ProjectPersonLink).filter_by(
                project_id=production_id,
            ).delete(synchronize_session=False)
            session.query(Project).filter_by(id=production_id).delete(synchronize_session=False)
            session.query(Person).filter(Person.id.in_([manager_id, artist_id])).delete(
                synchronize_session=False,
            )
