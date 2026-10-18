"""Pinned Zou/PostgreSQL acceptance for the authenticated hierarchy snapshot API."""

import base64
import hashlib
import json
import time
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from plugin.integrations.service_auth import JWKSCache
from plugin.integrations.v1 import canonical_json


def test_snapshot_http_persists_pages_immutably_with_production_binding(zou_app, zou_db_factory):
    from zou.app import db

    from plugin.integrations.http import (
        SyncSnapshotCompleteResource,
        SyncSnapshotPageResource,
        SyncSnapshotStartResource,
    )
    from plugin.models import (
        KitsuSyncDesiredConfiguration,
        KitsuSyncHierarchySnapshot,
        KitsuSyncHierarchySnapshotPage,
        KitsuSyncManagementIdempotency,
    )

    production_id = uuid4()
    other_production_id = uuid4()
    actor_id = uuid4()
    snapshot_id = uuid4()
    issuer = "https://sync.snapshot-test.invalid"
    audience = "https://files.snapshot-test.invalid/api/plugins/vfx_files/integrations/v1"
    subject = "kitsusync-snapshot-contract-test"
    client_id = "synthetic-snapshot-test"
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()

    def b64(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    jwks = {"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "snapshot-db-test",
        "n": b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
        "e": b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
    }]}
    zou_app.config.update(
        TESTING=True,
        VFX_FILES_KITSUSYNC_ISSUER=issuer,
        VFX_FILES_KITSUSYNC_AUDIENCE=audience,
        VFX_FILES_KITSUSYNC_SUBJECT=subject,
        VFX_FILES_KITSUSYNC_CLIENT_ID=client_id,
    )
    zou_app.extensions["vfx_files_kitsusync_jwks_cache"] = JWKSCache(issuer, fetcher=lambda _url: jwks)
    suffix = str(uuid4()).replace("-", "")
    prefix = "/plugins/vfx_files/integrations/v1"
    zou_app.add_url_rule(f"{prefix}/productions/<production_id>/sync-snapshots", endpoint=f"snapshot_start_{suffix}", view_func=SyncSnapshotStartResource.as_view(f"snapshot_start_view_{suffix}"))
    zou_app.add_url_rule(f"{prefix}/sync-snapshots/<snapshot_id>/pages/<int:page_index>", endpoint=f"snapshot_page_{suffix}", view_func=SyncSnapshotPageResource.as_view(f"snapshot_page_view_{suffix}"))
    zou_app.add_url_rule(f"{prefix}/sync-snapshots/<snapshot_id>/complete", endpoint=f"snapshot_complete_{suffix}", view_func=SyncSnapshotCompleteResource.as_view(f"snapshot_complete_view_{suffix}"))

    def token(scope, bound_production=production_id):
        now = int(time.time())
        header = {"typ": "at+JWT", "alg": "RS256", "kid": "snapshot-db-test"}
        claims = {
            "iss": issuer, "aud": audience, "sub": subject, "client_id": client_id,
            "iat": now, "nbf": now, "exp": now + 120, "jti": str(uuid4()),
            "scope": scope, "production_id": str(bound_production), "actor_id": str(actor_id),
            "actor_role": "production_admin",
        }
        signing = f"{b64(json.dumps(header, separators=(',', ':')).encode())}.{b64(json.dumps(claims, separators=(',', ':')).encode())}"
        return f"{signing}.{b64(private.sign(signing.encode('ascii'), padding.PKCS1v15(), hashes.SHA256()))}"

    production = {"object_type": "production", "id": str(production_id), "parent_id": None,
                  "name": "Synthetic Production", "included_in_sync": True}
    page_sha256 = hashlib.sha256(canonical_json([production])).hexdigest()
    page_body = {"contract_version": "1.0", "page_index": 0, "page_sha256": page_sha256, "objects": [production]}
    start_body = {
        "contract_version": "1.0", "snapshot_id": str(snapshot_id), "config_revision": 1,
        "source_etag": hashlib.sha256(b"synthetic-source").hexdigest(), "page_count": 1,
        "object_count": 1, "snapshot_sha256": page_sha256,
    }
    start_key = f"start:{uuid4()}"
    page_key = f"page:{uuid4()}"
    complete_key = f"complete:{uuid4()}"
    with zou_app.app_context():
        db.session.add(KitsuSyncDesiredConfiguration(
            production_id=production_id, config_revision=1, notification_epoch=0, desired_config={"contract_version": "1.0"},
        ))
        db.session.commit()
    client = zou_app.test_client()
    auth_headers = {"Authorization": f"Bearer {token('vfx.sync.snapshot.write')}", "X-KitsuSync-Actor-ID": str(actor_id)}
    other_headers = {"Authorization": f"Bearer {token('vfx.sync.snapshot.write', other_production_id)}", "X-KitsuSync-Actor-ID": str(actor_id)}
    base = f"{prefix}/productions/{production_id}/sync-snapshots"
    start = client.post(base, json=start_body, headers={**auth_headers, "Idempotency-Key": start_key})
    assert start.status_code == 201, start.get_data(as_text=True)
    assert start.json["state"] == "uploading"
    replay_start = client.post(base, json=start_body, headers={**auth_headers, "Idempotency-Key": start_key})
    assert replay_start.status_code == 201 and replay_start.json == start.json
    conflict = client.post(base, json={**start_body, "source_etag": "different"}, headers={**auth_headers, "Idempotency-Key": start_key})
    assert conflict.status_code == 409 and conflict.json["code"] == "idempotency_conflict"

    page_url = f"{prefix}/sync-snapshots/{snapshot_id}/pages/0"
    denied = client.put(page_url, json=page_body, headers={**other_headers, "Idempotency-Key": page_key})
    assert denied.status_code == 403 and denied.json["code"] == "production_access_denied"
    page = client.put(page_url, json=page_body, headers={**auth_headers, "Idempotency-Key": page_key})
    assert page.status_code == 200 and page.json["received_pages"] == 1
    replay_page = client.put(page_url, json=page_body, headers={**auth_headers, "Idempotency-Key": page_key})
    assert replay_page.status_code == 200 and replay_page.json == page.json

    # Reusing an identical page request/idempotency key on a different
    # snapshot must not replay the first snapshot's response.
    second_snapshot_id = uuid4()
    shot = {"object_type": "shot", "id": str(uuid4()), "parent_id": str(production_id),
            "name": "shot-01", "included_in_sync": True}
    second_objects = [production, shot]
    second_digest = hashlib.sha256(canonical_json(second_objects)).hexdigest()
    second_start = {
        **start_body, "snapshot_id": str(second_snapshot_id), "page_count": 2,
        "object_count": 2, "snapshot_sha256": second_digest,
        "source_etag": hashlib.sha256(b"synthetic-source-2").hexdigest(),
    }
    second_start_response = client.post(
        base, json=second_start,
        headers={**auth_headers, "Idempotency-Key": f"start:{uuid4()}"},
    )
    assert second_start_response.status_code == 201
    second_page_url = f"{prefix}/sync-snapshots/{second_snapshot_id}/pages/0"
    cross_snapshot_replay = client.put(second_page_url, json=page_body, headers={**auth_headers, "Idempotency-Key": page_key})
    assert cross_snapshot_replay.status_code == 409
    assert cross_snapshot_replay.json["code"] == "idempotency_conflict"

    complete_body = {"contract_version": "1.0", "page_count": 1, "object_count": 1, "snapshot_sha256": page_sha256}
    complete_url = f"{prefix}/sync-snapshots/{snapshot_id}/complete"
    complete = client.post(complete_url, json=complete_body, headers={**auth_headers, "Idempotency-Key": complete_key})
    assert complete.status_code == 200 and complete.json["state"] == "complete"
    replay_complete = client.post(complete_url, json=complete_body, headers={**auth_headers, "Idempotency-Key": complete_key})
    assert replay_complete.status_code == 200 and replay_complete.json == complete.json

    with zou_app.app_context():
        stored = db.session.get(KitsuSyncHierarchySnapshot, snapshot_id)
        assert stored.production_id == production_id and stored.state == "complete"
        assert db.session.get(KitsuSyncHierarchySnapshotPage, (snapshot_id, 0)).page_sha256 == page_sha256
        assert db.session.query(KitsuSyncManagementIdempotency).filter_by(production_id=production_id).count() == 4
    # The migration makes accepted snapshot pages immutable, including against
    # deletion. The random synthetic rows are intentionally left in the
    # disposable test database rather than bypassing that database invariant.
