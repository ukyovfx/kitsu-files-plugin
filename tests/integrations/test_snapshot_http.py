import base64
import json
import time
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import Flask

from plugin.integrations.http import (
    SyncApplyResource,
    SyncJobReconcileResource,
    SyncJobResource,
    SyncJobRetryResource,
    SyncPreviewResource,
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
    view(SyncPreviewResource, "productions/<production_id>/sync-previews")
    view(SyncApplyResource, "productions/<production_id>/sync-jobs")
    view(SyncJobResource, "sync-jobs/<job_id>")
    view(SyncJobRetryResource, "sync-jobs/<job_id>/retry")
    view(SyncJobReconcileResource, "sync-jobs/<job_id>/reconcile")

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
    assert registered["/integrations/v1/productions/<production_id>/sync-previews"] is SyncPreviewResource
    assert registered["/integrations/v1/productions/<production_id>/sync-jobs"] is SyncApplyResource
    assert registered["/integrations/v1/sync-jobs/<job_id>"] is SyncJobResource
    assert registered["/integrations/v1/sync-jobs/<job_id>/retry"] is SyncJobRetryResource
    assert registered["/integrations/v1/sync-jobs/<job_id>/reconcile"] is SyncJobReconcileResource


def test_sync_management_routes_require_their_exact_signed_scopes():
    client = _client()
    base = "/plugins/vfx_files/integrations/v1"
    requests = [
        ("post", f"{base}/productions/{PRODUCTION_ID}/sync-previews", "vfx.sync.apply"),
        ("post", f"{base}/productions/{PRODUCTION_ID}/sync-jobs", "vfx.sync.preview"),
        ("get", f"{base}/sync-jobs/30000000-0000-4000-8000-000000000001", "vfx.sync.retry"),
        ("post", f"{base}/sync-jobs/30000000-0000-4000-8000-000000000001/retry", "vfx.sync.read"),
        ("post", f"{base}/sync-jobs/30000000-0000-4000-8000-000000000001/reconcile", "vfx.sync.retry"),
    ]
    for method, url, wrong_scope in requests:
        response = getattr(client, method)(url, json={} if method == "post" else None, headers={
            "Authorization": f"Bearer {client.snapshot_token(wrong_scope)}",
            "X-KitsuSync-Actor-ID": ACTOR_ID,
        })
        assert response.status_code == 403
        assert response.json["code"] == "invalid_scope"


def test_sync_preview_http_handler_binds_valid_identity_and_forwards_idempotency(monkeypatch):
    from contextlib import nullcontext

    from plugin.integrations import http
    from plugin.integrations import sync

    fake_session = object()
    monkeypatch.setattr(
        http,
        "_integration_session_factory",
        lambda: lambda: nullcontext(fake_session),
    )
    observed = {}

    def preview(session, *, production_id, actor_id, payload, idempotency_key):
        observed.update(
            production_id=str(production_id), actor_id=str(actor_id), payload=payload,
            idempotency_key=idempotency_key, session=session,
        )
        return ({
            "contract_version": "1.0", "preview_id": "30000000-0000-4000-8000-000000000001",
            "production_id": str(production_id), "config_revision": 7, "root_id": "synthetic-root",
            "state": "preview_ready", "summary": {"existing": 1, "create": 0, "link": 0, "excluded": 0, "warnings": 0},
            "changes": [], "expires_at": "2026-10-13T00:15:00Z",
        }, 200, None)

    monkeypatch.setattr(sync, "create_sync_preview", preview)
    client = _client()
    response = client.post(
        f"/plugins/vfx_files/integrations/v1/productions/{PRODUCTION_ID}/sync-previews",
        json={"contract_version": "1.0", "config_revision": 7, "snapshot_id": "30000000-0000-4000-8000-000000000002"},
        headers={
            "Authorization": f"Bearer {client.snapshot_token('vfx.sync.preview')}",
            "X-KitsuSync-Actor-ID": ACTOR_ID,
            "Idempotency-Key": "sync-preview-http-key-01",
        },
    )
    assert response.status_code == 200
    assert response.json["state"] == "preview_ready"
    assert observed == {
        "production_id": PRODUCTION_ID, "actor_id": ACTOR_ID,
        "payload": {"contract_version": "1.0", "config_revision": 7, "snapshot_id": "30000000-0000-4000-8000-000000000002"},
        "idempotency_key": "sync-preview-http-key-01", "session": fake_session,
    }
