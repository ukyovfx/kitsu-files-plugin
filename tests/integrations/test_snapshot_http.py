import base64
import json
import time
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import Flask

from plugin.integrations.http import (
    SyncSnapshotCompleteResource,
    SyncSnapshotPageResource,
    SyncSnapshotStartResource,
)
from plugin.integrations.service_auth import JWKSCache

ISSUER = "https://sync.example.test"
AUDIENCE = "https://files.example.test/api/plugins/vfx_files/integrations/v1"
ACTOR_ID = "20000000-0000-4000-8000-000000000002"
PRODUCTION_ID = "10000000-0000-4000-8000-000000000001"


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _client():
    app = Flask(__name__)
    app.config.update(
        VFX_FILES_KITSUSYNC_ISSUER=ISSUER,
        VFX_FILES_KITSUSYNC_AUDIENCE=AUDIENCE,
        VFX_FILES_KITSUSYNC_SUBJECT="kitsusync-test-service",
        VFX_FILES_KITSUSYNC_CLIENT_ID="provider-http-installation",
    )
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()
    jwks = {"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "snapshot-http-test",
        "n": _b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
        "e": _b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
    }]}
    app.extensions["vfx_files_kitsusync_jwks_cache"] = JWKSCache(ISSUER, fetcher=lambda _url: jwks)

    def view(view_class, name):
        app.add_url_rule(f"/plugins/vfx_files/integrations/v1/{name}", view_func=view_class.as_view(name.replace("/", "_")))

    view(SyncSnapshotStartResource, "productions/<production_id>/sync-snapshots")
    view(SyncSnapshotPageResource, "sync-snapshots/<snapshot_id>/pages/<int:page_index>")
    view(SyncSnapshotCompleteResource, "sync-snapshots/<snapshot_id>/complete")

    def token(scopes, production_id=PRODUCTION_ID):
        now = int(time.time())
        header = {"typ": "at+JWT", "alg": "RS256", "kid": "snapshot-http-test"}
        claims = {
            "iss": ISSUER, "aud": AUDIENCE, "sub": "kitsusync-test-service",
            "client_id": "provider-http-installation", "iat": now, "nbf": now,
            "exp": now + 120, "jti": str(uuid4()), "scope": scopes,
            "production_id": production_id, "actor_id": ACTOR_ID,
            "actor_role": "production_admin",
        }
        signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
        signature = private.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input}.{_b64(signature)}"

    client = app.test_client()
    client.snapshot_token = token
    return client


def test_snapshot_http_handlers_fail_closed_for_missing_or_wrong_scope():
    client = _client()
    url = f"/plugins/vfx_files/integrations/v1/productions/{PRODUCTION_ID}/sync-snapshots"
    body = {
        "contract_version": "1.0", "snapshot_id": "b0000000-0000-4000-8000-000000000001",
        "config_revision": 1, "source_etag": "f" * 64, "page_count": 1,
        "object_count": 1, "snapshot_sha256": "a" * 64,
    }
    assert client.post(url, json=body).status_code == 401
    denied = client.post(url, json=body, headers={
        "Authorization": f"Bearer {client.snapshot_token('vfx.config.write')}",
        "X-KitsuSync-Actor-ID": ACTOR_ID,
        "Idempotency-Key": "snapshot-start-key-0001",
    })
    assert denied.status_code == 403
    assert denied.json["code"] == "invalid_scope"
    cross_production = client.post(url, json=body, headers={
        "Authorization": f"Bearer {client.snapshot_token('vfx.sync.snapshot.write', '10000000-0000-4000-8000-000000000099')}",
        "X-KitsuSync-Actor-ID": ACTOR_ID,
        "Idempotency-Key": "snapshot-cross-prod-0001",
    })
    assert cross_production.status_code == 403
    assert cross_production.json["code"] == "production_access_denied"


def test_snapshot_handler_registry_uses_contract_routes_and_scope():
    from plugin.resources import routes
    registered = {path: resource for path, resource in routes}
    assert registered["/integrations/v1/productions/<production_id>/sync-snapshots"] is SyncSnapshotStartResource
    assert registered["/integrations/v1/sync-snapshots/<snapshot_id>/pages/<int:page_index>"] is SyncSnapshotPageResource
    assert registered["/integrations/v1/sync-snapshots/<snapshot_id>/complete"] is SyncSnapshotCompleteResource
