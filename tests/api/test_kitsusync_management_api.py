"""PostgreSQL-backed HTTP acceptance for the authenticated KitsuSync config API."""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import time
from uuid import uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("flask_jwt_extended")


ISSUER = "https://sync.example.test"
AUDIENCE = "https://files.example.test/api/plugins/vfx_files/integrations/v1"
SUBJECT = "kitsusync-test-service"
CLIENT_ID = "files-installation-test"
BASE = "/plugins/vfx_files/integrations/v1"


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _make_token(private_key, *, production_id, actor_id, scopes):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    header = {"typ": "at+JWT", "alg": "RS256", "kid": "management-test-key"}
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": SUBJECT,
        "client_id": CLIENT_ID,
        "iat": now,
        "nbf": now,
        "exp": now + 180,
        "jti": str(uuid4()),
        "scope": scopes,
        "production_id": str(production_id),
        "actor_id": str(actor_id),
        "actor_role": "production_admin",
    }
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    signature = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


@pytest.fixture
def management_client(authenticated_plugin_client, synthetic_upload_task, zou_db_factory):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from plugin.integrations.service_auth import JWKSCache

    app = authenticated_plugin_client.vfx_app
    production_id, _entity_type, _task_type, _entity, _task_id, connection_id, actor_id = synthetic_upload_task
    with app.app_context():
        row_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = row_key.public_key().public_numbers()
        jwks = {"keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "management-test-key",
            "n": _b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
            "e": _b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
        }]}
        app.config.update(
            VFX_FILES_KITSUSYNC_ISSUER=ISSUER,
            VFX_FILES_KITSUSYNC_AUDIENCE=AUDIENCE,
            VFX_FILES_KITSUSYNC_SUBJECT=SUBJECT,
            VFX_FILES_KITSUSYNC_CLIENT_ID=CLIENT_ID,
        )
        cache_key = "vfx_files_kitsusync_jwks_cache"
        prior_cache = app.extensions.get(cache_key)
        app.extensions[cache_key] = JWKSCache(ISSUER, fetcher=lambda _url: jwks)

    def headers(scopes, *, token_production=production_id):
        token = _make_token(row_key, production_id=token_production, actor_id=actor_id, scopes=scopes)
        return {"Authorization": f"Bearer {token}", "X-KitsuSync-Actor-ID": str(actor_id)}

    client = app.test_client()
    client.vfx_management_headers = headers
    client.vfx_production_id = production_id
    client.vfx_connection_id = connection_id
    client.vfx_db_factory = zou_db_factory
    yield client

    from plugin.models import KitsuSyncDesiredConfiguration, KitsuSyncManagementIdempotency
    with zou_db_factory.begin() as session:
        session.execute(KitsuSyncManagementIdempotency.__table__.delete().where(
            KitsuSyncManagementIdempotency.production_id == production_id
        ))
        session.execute(KitsuSyncDesiredConfiguration.__table__.delete().where(
            KitsuSyncDesiredConfiguration.production_id == production_id
        ))
    with app.app_context():
        if prior_cache is None:
            app.extensions.pop(cache_key, None)
        else:
            app.extensions[cache_key] = prior_cache
        for key in ("VFX_FILES_KITSUSYNC_ISSUER", "VFX_FILES_KITSUSYNC_AUDIENCE", "VFX_FILES_KITSUSYNC_SUBJECT", "VFX_FILES_KITSUSYNC_CLIENT_ID"):
            app.config.pop(key, None)


def _payload(client, *, mode="adopt_existing"):
    return {
        "contract_version": "1.0",
        "connection_id": str(client.vfx_connection_id),
        "drive": {
            "account_mode": "shared_drive",
            "drive_id": "synthetic-drive-id",
            "owner_subject": None,
            "root_id": "synthetic-root-id",
            "root_state": "verified",
        },
        "template_selection": {"selections": [{
            "scope": "shot", "template_id": "synthetic-shot-template",
            "revision": 1, "overrides": {},
        }]},
        "sync_policy": {
            "mode": mode,
            "scope": {"episodes": False, "sequences": True, "shots": True, "assets": True, "tasks": True},
            "notify_new_deliveries": False,
        },
    }


def _route(client):
    return f"{BASE}/productions/{client.vfx_production_id}/desired-config"


def test_management_api_enforces_service_auth_scope_actor_and_production(management_client):
    client = management_client
    denied = client.get(_route(client))
    assert denied.status_code == 401
    assert denied.json["code"] == "unauthorized_service"
    assert "error" not in denied.json

    missing_scope = client.get(_route(client), headers=client.vfx_management_headers("vfx.config.write"))
    assert missing_scope.status_code == 403
    assert missing_scope.json["code"] == "invalid_scope"

    other_production = uuid4()
    cross_scope = client.get(
        f"{BASE}/productions/{other_production}/desired-config",
        headers=client.vfx_management_headers("vfx.config.read"),
    )
    assert cross_scope.status_code == 403
    assert cross_scope.json["code"] == "production_access_denied"

    bad_actor_headers = dict(client.vfx_management_headers("vfx.config.read"))
    bad_actor_headers["X-KitsuSync-Actor-ID"] = str(uuid4())
    bad_actor = client.get(_route(client), headers=bad_actor_headers)
    assert bad_actor.status_code == 401
    assert bad_actor.json["code"] == "unauthorized_service"


def test_desired_config_create_replay_conflict_update_and_activation_fencing(management_client):
    client = management_client
    route = _route(client)
    payload = _payload(client)
    headers = {
        **client.vfx_management_headers("vfx.config.write"),
        "Idempotency-Key": "create-config-key-0001",
        "If-None-Match": "*",
    }
    created = client.put(route, json=payload, headers=headers)
    assert created.status_code == 201, created.get_data(as_text=True)
    assert created.headers["ETag"] == '"1"'
    assert created.json["config_revision"] == 1
    assert created.json["activation"] == {"enabled": False, "activated_at": None, "notification_epoch": 0}

    replay = client.put(route, json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.json == created.json

    conflicting_replay = client.put(route, json=_payload(client, mode="future_only"), headers=headers)
    assert conflicting_replay.status_code == 409
    assert conflicting_replay.json["code"] == "idempotency_conflict"

    exists_conflict = client.put(route, json=payload, headers={
        **client.vfx_management_headers("vfx.config.write"),
        "Idempotency-Key": "second-create-key-0002", "If-None-Match": "*",
    })
    assert exists_conflict.status_code == 412

    updated_payload = _payload(client, mode="future_only")
    stale = client.put(route, json=updated_payload, headers={
        **client.vfx_management_headers("vfx.config.write"),
        "Idempotency-Key": "stale-update-key-0003", "If-Match": '"9"',
    })
    assert stale.status_code == 409
    assert stale.json["code"] == "stale_config"

    updated = client.put(route, json=updated_payload, headers={
        **client.vfx_management_headers("vfx.config.write"),
        "Idempotency-Key": "update-config-key-0004", "If-Match": '"1"',
    })
    assert updated.status_code == 200
    assert updated.headers["ETag"] == '"2"'
    assert updated.json["config_revision"] == 2

    activation_route = f"{BASE}/productions/{client.vfx_production_id}/activation"
    activate_headers = {
        **client.vfx_management_headers("vfx.activation.write"),
        "Idempotency-Key": "activate-config-key-0005", "If-Match": '"2"',
    }
    activated = client.post(activation_route, json={"contract_version": "1.0"}, headers=activate_headers)
    assert activated.status_code == 200
    assert activated.headers["ETag"] == '"3"'
    assert activated.json["activation"]["enabled"] is True
    assert activated.json["activation"]["notification_epoch"] == 1
    activation_replay = client.post(activation_route, json={"contract_version": "1.0"}, headers=activate_headers)
    assert activation_replay.json == activated.json

    deactivate_route = f"{BASE}/productions/{client.vfx_production_id}/deactivation"
    deactivated = client.post(deactivate_route, json={"contract_version": "1.0"}, headers={
        **client.vfx_management_headers("vfx.activation.write"),
        "Idempotency-Key": "deactivate-config-key-0006", "If-Match": '"3"',
    })
    assert deactivated.status_code == 200
    assert deactivated.json["activation"]["enabled"] is False
    assert deactivated.json["activation"]["notification_epoch"] == 2

    readback = client.get(route, headers=client.vfx_management_headers("vfx.config.read"))
    assert readback.status_code == 200
    assert readback.headers["ETag"] == '"4"'
    assert readback.json["activation"]["notification_epoch"] == 2


def test_cross_production_connection_is_not_accepted(management_client):
    client = management_client
    payload = _payload(client)
    from plugin.models import StorageConnection

    foreign_connection_id = uuid4()
    with client.vfx_db_factory.begin() as session:
        session.add(StorageConnection(
            id=foreign_connection_id,
            production_id=uuid4(),
            provider="google_drive",
            account_mode="shared_drive",
            drive_id="foreign-drive-id",
            allowed_root_id="foreign-root-id",
            credential_ref="synthetic-reference-only",
            enabled=True,
        ))
    payload["connection_id"] = str(foreign_connection_id)
    response = client.put(_route(client), json=payload, headers={
        **client.vfx_management_headers("vfx.config.write"),
        "Idempotency-Key": "foreign-connection-key-01", "If-None-Match": "*",
    })
    assert response.status_code == 404
    assert response.json["code"] == "connection_not_found"
    with client.vfx_db_factory.begin() as session:
        session.delete(session.get(StorageConnection, foreign_connection_id))


def test_concurrent_updates_with_same_etag_have_one_winner(management_client):
    client = management_client
    route = _route(client)
    create = client.put(route, json=_payload(client), headers={
        **client.vfx_management_headers("vfx.config.write"),
        "Idempotency-Key": "concurrent-create-key-01", "If-None-Match": "*",
    })
    assert create.status_code == 201

    def update(index):
        with client.vfx_app.test_client() as isolated_client:
            return isolated_client.put(route, json=_payload(client, mode="future_only" if index else "adopt_existing"), headers={
                **client.vfx_management_headers("vfx.config.write"),
                "Idempotency-Key": f"concurrent-update-key-{index:04d}", "If-Match": '"1"',
            }).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(update, (1, 2)))
    assert sorted(statuses) == [200, 409]
