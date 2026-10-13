import base64
import json
import time
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import Flask

from plugin.integrations.http import TemplatesResource
from plugin.integrations.service_auth import JWKSCache


ISSUER = "https://sync.example.test"
AUDIENCE = "https://files.example.test/api/plugins/vfx_files/integrations/v1"
ACTOR_ID = "20000000-0000-4000-8000-000000000002"
PRODUCTION_ID = "10000000-0000-4000-8000-000000000001"


def _b64(payload):
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _token(private_key, scopes):
    now = int(time.time())
    header = {"typ": "at+JWT", "alg": "RS256", "kid": "provider-http-test"}
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "kitsusync-test-service",
        "client_id": "provider-http-installation",
        "iat": now,
        "nbf": now,
        "exp": now + 120,
        "jti": str(uuid4()),
        "scope": scopes,
        "production_id": PRODUCTION_ID,
        "actor_id": ACTOR_ID,
        "actor_role": "production_admin",
    }
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    signature = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


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
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "provider-http-test",
        "n": _b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
        "e": _b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
    }]}
    app.extensions["vfx_files_kitsusync_jwks_cache"] = JWKSCache(ISSUER, fetcher=lambda _url: jwks)
    app.add_url_rule("/plugins/vfx_files/integrations/v1/templates", view_func=TemplatesResource.as_view("templates"))
    client = app.test_client()
    client.provider_token = lambda scopes: _token(private, scopes)
    return client


def test_templates_handler_requires_service_auth_and_exact_scope():
    client = _client()
    url = "/plugins/vfx_files/integrations/v1/templates?scope=shot"

    unauthenticated = client.get(url)
    assert unauthenticated.status_code == 401
    assert unauthenticated.json["code"] == "unauthorized_service"

    wrong_scope = client.get(url, headers={
        "Authorization": f"Bearer {client.provider_token('vfx.connections.read')}",
        "X-KitsuSync-Actor-ID": ACTOR_ID,
    })
    assert wrong_scope.status_code == 403
    assert wrong_scope.json["code"] == "invalid_scope"

    allowed = client.get(url, headers={
        "Authorization": f"Bearer {client.provider_token('vfx.templates.read')}",
        "X-KitsuSync-Actor-ID": ACTOR_ID,
    })
    assert allowed.status_code == 200
    assert allowed.json["templates"][0]["scope"] == "shot"


def test_templates_handler_fails_closed_on_invalid_scope_or_actor():
    client = _client()
    url = "/plugins/vfx_files/integrations/v1/templates?scope=department"
    headers = {
        "Authorization": f"Bearer {client.provider_token('vfx.templates.read')}",
        "X-KitsuSync-Actor-ID": ACTOR_ID,
    }
    invalid_scope = client.get(url, headers=headers)
    assert invalid_scope.status_code == 400
    assert invalid_scope.json["code"] == "invalid_event"

    actor_mismatch = client.get(
        "/plugins/vfx_files/integrations/v1/templates?scope=shot",
        headers={**headers, "X-KitsuSync-Actor-ID": str(uuid4())},
    )
    assert actor_mismatch.status_code == 401
    assert actor_mismatch.json["code"] == "unauthorized_service"


def test_oauth_management_and_callback_routes_are_registered_on_plugin_surface():
    from plugin.integrations.http import (
        DriveAuthorizationResource,
        DriveConnectionRevokeResource,
        DriveConnectionStatusResource,
        GoogleOAuthCallbackResource,
    )
    from plugin.resources import routes

    registered = {path: view for path, view in routes}
    assert registered["/integrations/v1/drive-connections/authorizations"] is DriveAuthorizationResource
    assert registered["/integrations/v1/drive-connections/<connection_id>"] is DriveConnectionStatusResource
    assert registered["/integrations/v1/drive-connections/<connection_id>/revoke"] is DriveConnectionRevokeResource
    assert registered["/oauth/google/callback"] is GoogleOAuthCallbackResource


def test_authorization_management_handler_rejects_missing_or_wrong_scope_before_storage_access():
    from plugin.integrations.http import DriveAuthorizationResource

    client = _client()
    client.application.add_url_rule(
        "/plugins/vfx_files/integrations/v1/drive-connections/authorizations",
        view_func=DriveAuthorizationResource.as_view("drive_authorization"),
    )
    url = "/plugins/vfx_files/integrations/v1/drive-connections/authorizations"
    body = {
        "contract_version": "1.0",
        "production_id": PRODUCTION_ID,
        "account_mode": "my_drive",
    }
    denied = client.post(url, json=body)
    assert denied.status_code == 401
    assert denied.json["code"] == "unauthorized_service"
    wrong_scope = client.post(url, json=body, headers={
        "Authorization": f"Bearer {client.provider_token('vfx.connections.read')}",
        "X-KitsuSync-Actor-ID": ACTOR_ID,
    })
    assert wrong_scope.status_code == 403
    assert wrong_scope.json["code"] == "invalid_scope"
