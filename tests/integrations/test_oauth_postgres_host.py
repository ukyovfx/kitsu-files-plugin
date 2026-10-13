"""Authenticated Google grant/root lifecycle on disposable pinned Zou/PostgreSQL."""

import base64
import json
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class SyntheticGoogleTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/token"):
            return SyntheticResponse({
                "access_token": "synthetic-pg-access-token",
                "refresh_token": "synthetic-pg-refresh-token",
                "expires_in": 3600,
                "scope": "openid https://www.googleapis.com/auth/drive.file",
            })
        if url.endswith("/userinfo"):
            return SyntheticResponse({"sub": "synthetic-shared-drive-actor"})
        raise AssertionError("Unexpected Google endpoint in synthetic transport")


class SyntheticResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def _jwk_b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _service_token(private, *, issuer, audience, subject, client_id, production_id, actor_id, scope):
    now = int(time.time())
    header = {"typ": "at+JWT", "alg": "RS256", "kid": "synthetic-oauth-pg"}
    claims = {
        "iss": issuer, "aud": audience, "sub": subject, "client_id": client_id,
        "iat": now, "nbf": now, "exp": now + 120, "jti": str(uuid4()),
        "scope": scope, "production_id": str(production_id), "actor_id": str(actor_id),
        "actor_role": "production_admin",
    }
    header_bytes = json.dumps(header, separators=(",", ":")).encode()
    claims_bytes = json.dumps(claims, separators=(",", ":")).encode()
    signing = f"{_jwk_b64(header_bytes)}.{_jwk_b64(claims_bytes)}"
    signature = private.sign(signing.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{_jwk_b64(signature)}"


def test_authenticated_oauth_callback_and_root_verification_persist_on_postgres(zou_app, zou_db_factory):
    from plugin.integrations.oauth import CredentialKeyring
    from plugin.integrations.service_auth import JWKSCache
    from plugin.storage.base import StorageCapabilities

    app = zou_app
    production_id, actor_id = uuid4(), uuid4()
    issuer = "https://sync.oauth-pg.test"
    audience = "https://files.oauth-pg.test/api/plugins/vfx_files/integrations/v1"
    subject, client_id = "synthetic-sync-service", "synthetic-sync-client"
    app.config.update(
        TESTING=True,
        VFX_FILES_KITSUSYNC_ISSUER=issuer,
        VFX_FILES_KITSUSYNC_AUDIENCE=audience,
        VFX_FILES_KITSUSYNC_SUBJECT=subject,
        VFX_FILES_KITSUSYNC_CLIENT_ID=client_id,
        VFX_FILES_GOOGLE_CLIENT_ID="synthetic-google-client",
        VFX_FILES_GOOGLE_CLIENT_SECRET="synthetic-google-client-secret",
        VFX_FILES_GOOGLE_OAUTH_REDIRECT_URI="https://files.oauth-pg.test/api/plugins/vfx_files/oauth/google/callback",
        VFX_FILES_KITSUSYNC_RETURN_URI="https://sync.oauth-pg.test/admin/vfx-files/authorization-return",
        VFX_FILES_STORAGE_MODE="fake",
    )
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()
    jwks = {"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "synthetic-oauth-pg",
        "n": _jwk_b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
        "e": _jwk_b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
    }]}
    app.extensions["vfx_files_kitsusync_jwks_cache"] = JWKSCache(issuer, fetcher=lambda _url: jwks)
    app.extensions["vfx_files_credential_keyring"] = CredentialKeyring("pg-test-key", {"pg-test-key": b"p" * 32})
    google = SyntheticGoogleTransport()
    app.extensions["vfx_files_oauth_transport"] = google

    class RootAdapter:
        calls = 0

        def verify_root_candidate(self, root_id):
            self.calls += 1
            return StorageCapabilities(True, root_id, "synthetic-pg-drive", False, True, (root_id,))

    root_adapter = RootAdapter()
    app.extensions["vfx_files_storage_adapter_factory"] = lambda _connection: root_adapter
    client = app.test_client()
    prefix = "/plugins/vfx_files"
    base = prefix + "/integrations/v1/drive-connections"

    assert client.get(base + "/templates").status_code == 401  # Dynamic connection route stays authenticated.
    unauthorized = client.get(base + "?production_id=" + str(production_id))
    assert unauthorized.status_code == 401, unauthorized.get_data(as_text=True)

    authorize_token = _service_token(
        private, issuer=issuer, audience=audience, subject=subject, client_id=client_id,
        production_id=production_id, actor_id=actor_id, scope="vfx.connections.authorize",
    )
    authorization = client.post(
        base + "/authorizations",
        json={"contract_version": "1.0", "production_id": str(production_id), "account_mode": "shared_drive"},
        headers={"Authorization": "Bearer " + authorize_token, "X-KitsuSync-Actor-ID": str(actor_id),
                 "Idempotency-Key": "synthetic-pg-oauth-intent-01"},
    )
    assert authorization.status_code == 201, authorization.get_data(as_text=True)
    state = parse_qs(urlsplit(authorization.json["authorization_url"]).query)["state"][0]
    callback = client.get(prefix + "/oauth/google/callback", query_string={"state": state, "code": "synthetic-pg-code"})
    assert callback.status_code == 303
    assert "synthetic-pg-refresh-token" not in callback.get_data(as_text=True)

    read_token = _service_token(
        private, issuer=issuer, audience=audience, subject=subject, client_id=client_id,
        production_id=production_id, actor_id=actor_id, scope="vfx.connections.read",
    )
    status = client.get(
        base + f"/{authorization.json['authorization_id']}?production_id={production_id}",
        headers={"Authorization": "Bearer " + read_token, "X-KitsuSync-Actor-ID": str(actor_id)},
    )
    assert status.status_code == 200 and status.json["state"] == "connected"
    assert "synthetic-pg-refresh-token" not in status.get_data(as_text=True)

    verify_token = _service_token(
        private, issuer=issuer, audience=audience, subject=subject, client_id=client_id,
        production_id=production_id, actor_id=actor_id, scope="vfx.roots.verify",
    )
    root_url = base + f"/{authorization.json['authorization_id']}/roots"
    root_body = {
        "contract_version": "1.0", "account_mode": "shared_drive",
        "production_id": str(production_id), "root_id": "synthetic-pg-root",
        "drive_id": "synthetic-pg-drive",
    }
    root_headers = {"Authorization": "Bearer " + verify_token, "X-KitsuSync-Actor-ID": str(actor_id),
                    "Idempotency-Key": "synthetic-pg-root-verify-01"}
    verified = client.post(root_url, json=root_body, headers=root_headers)
    assert verified.status_code == 200, verified.get_data(as_text=True)
    assert verified.json["owner_subject"] is None and verified.json["drive_id"] == "synthetic-pg-drive"
    assert client.post(root_url, json=root_body, headers=root_headers).json == verified.json
    assert root_adapter.calls == 1

    from zou.app import db
    from plugin.models import DriveCredential, StorageConnection
    with app.app_context():
        connection_id = authorization.json["authorization_id"]
        credential = db.session.get(DriveCredential, connection_id)
        connection = db.session.get(StorageConnection, connection_id)
        assert credential.state == "active" and credential.ciphertext
        assert b"synthetic-pg-refresh-token" not in credential.ciphertext
        assert connection.enabled and connection.allowed_root_id == "synthetic-pg-root"
        assert connection.drive_id == "synthetic-pg-drive"

    assert len(google.calls) == 2
