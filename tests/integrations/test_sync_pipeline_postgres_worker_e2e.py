"""One authenticated HTTP → PostgreSQL → separate Worker → FakeStorage flow.

This acceptance test must run only in the pinned disposable Zou/PostgreSQL host.
It uses the installed Plugin routes and invokes the actual worker entry point
in a child process; no live KitsuSync, Google, or Discord service is contacted.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from plugin.integrations.service_auth import JWKSCache
from plugin.integrations.v1 import canonical_json


ISSUER = "https://sync.pipeline-e2e.invalid"
AUDIENCE = "https://files.pipeline-e2e.invalid/api/plugins/vfx_files/integrations/v1"
SUBJECT = "synthetic-pipeline-e2e"
CLIENT_ID = "synthetic-pipeline-e2e-client"
KID = "pipeline-e2e-rs256"


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _service_token(private_key, *, actor_id, production_id, scope, expired=False):
    now = int(time.time())
    header = {"typ": "at+JWT", "alg": "RS256", "kid": KID}
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": SUBJECT,
        "client_id": CLIENT_ID,
        "iat": now - 180 if expired else now,
        "nbf": now - 180 if expired else now,
        "exp": now - 120 if expired else now + 180,
        "jti": str(uuid4()),
        "scope": scope,
        "production_id": str(production_id),
        "actor_id": str(actor_id),
        "actor_role": "production_admin",
    }
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    signature = private_key.sign(
        signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
    )
    return f"{signing_input}.{_b64(signature)}"


def test_installed_http_snapshot_preview_apply_worker_and_recovery(
    authenticated_plugin_client,
    synthetic_upload_task,
    zou_app,
    zou_db_factory,
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import func, select
    from plugin.models import (
        FolderBinding,
        Job,
        KitsuSyncDesiredConfiguration,
        KitsuSyncHierarchySnapshot,
        KitsuSyncHierarchySnapshotPage,
        KitsuSyncManagementIdempotency,
        KitsuSyncSyncPreview,
        SharedTemplateConfiguration,
        StorageConnection,
    )
    from plugin.storage.fake import PersistentFakeStorage
    from zou.app.models.entity import Entity
    from zou.app.models.task import Task
    from zou.app.models.task_type import TaskType

    client = authenticated_plugin_client
    app = zou_app
    (
        production_id,
        _entity_type_id,
        task_type_id,
        entity_id,
        task_id,
        connection_id,
        actor_id,
    ) = synthetic_upload_task
    wrong_production_id = uuid4()
    wrong_actor_id = uuid4()

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private_key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KID,
                "n": _b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
                "e": _b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
            }
        ]
    }
    app.config.update(
        TESTING=True,
        VFX_FILES_KITSUSYNC_ISSUER=ISSUER,
        VFX_FILES_KITSUSYNC_AUDIENCE=AUDIENCE,
        VFX_FILES_KITSUSYNC_SUBJECT=SUBJECT,
        VFX_FILES_KITSUSYNC_CLIENT_ID=CLIENT_ID,
        VFX_FILES_STORAGE_MODE="fake",
        VFX_FILES_FAKE_STORAGE_ROOT=str(tmp_path / "sync-storage.json"),
        VFX_FILES_FAKE_ROOT_ID="synthetic-root-id",
        VFX_FILES_FAKE_DRIVE_ID="synthetic-drive-id",
        VFX_FILES_STAGING_ROOT=str(tmp_path / "staging"),
    )
    app.extensions["vfx_files_kitsusync_jwks_cache"] = JWKSCache(
        ISSUER, fetcher=lambda _url: jwks
    )

    prefix = "/plugins/vfx_files/integrations/v1"
    registered_paths = {rule.rule for rule in app.url_map.iter_rules()}
    required_paths = {
        f"{prefix}/productions/<production_id>/desired-config",
        f"{prefix}/productions/<production_id>/activation",
        f"{prefix}/productions/<production_id>/sync-snapshots",
        f"{prefix}/sync-snapshots/<snapshot_id>/pages/<int:page_index>",
        f"{prefix}/sync-snapshots/<snapshot_id>/complete",
        f"{prefix}/productions/<production_id>/sync-previews",
        f"{prefix}/productions/<production_id>/sync-jobs",
        f"{prefix}/sync-jobs/<job_id>",
        f"{prefix}/sync-jobs/<job_id>/retry",
        f"{prefix}/sync-jobs/<job_id>/reconcile",
    }
    assert required_paths <= registered_paths

    def headers(
        scope,
        *,
        bound_production=production_id,
        bound_actor=actor_id,
        expired=False,
        idempotency=None,
    ):
        result = {
            "Authorization": "Bearer "
            + _service_token(
                private_key,
                actor_id=bound_actor,
                production_id=bound_production,
                scope=scope,
                expired=expired,
            ),
            "X-KitsuSync-Actor-ID": str(bound_actor),
        }
        if idempotency:
            result["Idempotency-Key"] = idempotency
        return result

    storage_path = tmp_path / "sync-storage.json"
    storage = PersistentFakeStorage(
        storage_path, root_id="synthetic-root-id", drive_id="synthetic-drive-id"
    )
    assert list(storage.objects) == ["synthetic-root-id"]

    with app.app_context(), zou_db_factory.begin() as session:
        connection = session.get(StorageConnection, connection_id)
        connection.allowed_root_id = "synthetic-root-id"
        connection.drive_id = "synthetic-drive-id"
        connection.enabled = True
        task = session.get(Task, task_id)
        shot = session.get(Entity, entity_id)
        task_type = session.get(TaskType, task_type_id)
        assert task.project_id == production_id and task.entity_id == entity_id
        assert task_type.for_entity == "Shot"

    desired = {
        "contract_version": "1.0",
        "connection_id": str(connection_id),
        "drive": {
            "account_mode": "shared_drive",
            "drive_id": "synthetic-drive-id",
            "root_id": "synthetic-root-id",
            "root_state": "verified",
            "owner_subject": None,
        },
        "template_selection": {
            "selections": [
                {
                    "scope": "production",
                    "template_id": "vfx-files.production.default",
                    "revision": 1,
                    "overrides": {},
                },
                {
                    "scope": "shot",
                    "template_id": "vfx-files.shot.default",
                    "revision": 1,
                    "overrides": {},
                },
                {
                    "scope": "task",
                    "template_id": "vfx-files.task.default",
                    "revision": 1,
                    "overrides": {},
                },
            ]
        },
        "sync_policy": {
            "mode": "new_production",
            "scope": {
                "episodes": False,
                "sequences": False,
                "shots": True,
                "assets": False,
                "tasks": True,
            },
            "notify_new_deliveries": False,
        },
    }
    config_url = f"{prefix}/productions/{production_id}/desired-config"
    missing_auth = client.get(config_url)
    assert missing_auth.status_code == 401
    wrong_scope = client.put(
        config_url, json=desired, headers=headers("vfx.sync.preview")
    )
    assert wrong_scope.status_code == 403
    first_config_key = f"config-create-{uuid4()}"
    created = client.put(
        config_url,
        json=desired,
        headers={
            **headers("vfx.config.write", idempotency=first_config_key),
            "If-None-Match": "*",
        },
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    assert (
        created.headers["ETag"] == '"1"'
        and created.json["activation"]["enabled"] is False
    )
    with zou_db_factory.begin() as session:
        execution_template = session.scalar(select(SharedTemplateConfiguration).where(
            SharedTemplateConfiguration.production_id == production_id
        ))
        assert execution_template is not None
        assert execution_template.version == created.json["config_revision"]
        assert execution_template.enabled is False
        assert execution_template.storage_connection_id == connection_id
        assert execution_template.allowed_root_id == "synthetic-root-id"
        assert execution_template.template_tree["folder_id"] == "vfx-files-v1:production-root"
        assert execution_template.role_mappings["shot"] == ["vfx-files-v1:shot"]
        assert execution_template.role_mappings["task"] == ["vfx-files-v1:task-shot"]
    replayed_config = client.put(
        config_url,
        json=desired,
        headers={
            **headers("vfx.config.write", idempotency=first_config_key),
            "If-None-Match": "*",
        },
    )
    assert replayed_config.status_code == 201 and replayed_config.json == created.json

    objects = [
        {
            "object_type": "production",
            "id": str(production_id),
            "parent_id": None,
            "name": "Synthetic",
            "included_in_sync": True,
        },
        {
            "object_type": "shot",
            "id": str(entity_id),
            "parent_id": str(production_id),
            "name": shot.name,
            "included_in_sync": True,
        },
        {
            "object_type": "task",
            "id": str(task_id),
            "parent_id": str(entity_id),
            "name": task.name,
            "task_type_id": str(task_type_id),
            "included_in_sync": True,
        },
        {
            "object_type": "task_type",
            "id": str(task_type_id),
            "parent_id": None,
            "name": task_type.name,
            "included_in_sync": False,
        },
    ]
    full_digest = hashlib.sha256(canonical_json(objects)).hexdigest()
    page_digest = hashlib.sha256(canonical_json(objects)).hexdigest()
    snapshot_id = uuid4()
    snapshot_body = {
        "contract_version": "1.0",
        "snapshot_id": str(snapshot_id),
        "config_revision": 1,
        "source_etag": hashlib.sha256(b"synthetic-source-etag").hexdigest(),
        "page_count": 1,
        "object_count": len(objects),
        "snapshot_sha256": full_digest,
    }
    start_url = f"{prefix}/productions/{production_id}/sync-snapshots"
    stale_start = client.post(
        start_url,
        json={**snapshot_body, "config_revision": 2},
        headers={
            **headers(
                "vfx.sync.snapshot.write", idempotency=f"stale-snapshot-{uuid4()}"
            ),
        },
    )
    assert stale_start.status_code == 409 and stale_start.json["code"] == "stale_config"
    expired_auth = client.post(
        start_url,
        json=snapshot_body,
        headers=headers(
            "vfx.sync.snapshot.write",
            expired=True,
            idempotency=f"expired-snapshot-{uuid4()}",
        ),
    )
    assert expired_auth.status_code == 401
    wrong_production = client.post(
        start_url,
        json=snapshot_body,
        headers=headers(
            "vfx.sync.snapshot.write",
            bound_production=wrong_production_id,
            idempotency=f"cross-production-{uuid4()}",
        ),
    )
    assert wrong_production.status_code == 403

    start_key = f"snapshot-start-{uuid4()}"
    started = client.post(
        start_url,
        json=snapshot_body,
        headers=headers("vfx.sync.snapshot.write", idempotency=start_key),
    )
    assert started.status_code == 201 and started.json["state"] == "uploading"
    assert (
        client.post(
            start_url,
            json=snapshot_body,
            headers=headers("vfx.sync.snapshot.write", idempotency=start_key),
        ).json
        == started.json
    )
    page_url = f"{prefix}/sync-snapshots/{snapshot_id}/pages/0"
    page_body = {
        "contract_version": "1.0",
        "page_index": 0,
        "page_sha256": page_digest,
        "objects": objects,
    }
    page_key = f"snapshot-page-{uuid4()}"
    page_headers = headers("vfx.sync.snapshot.write", idempotency=page_key)
    denied_page = client.put(
        page_url,
        json=page_body,
        headers=headers(
            "vfx.sync.snapshot.write",
            bound_production=wrong_production_id,
            idempotency=page_key,
        ),
    )
    assert denied_page.status_code == 403
    page = client.put(page_url, json=page_body, headers=page_headers)
    assert page.status_code == 200 and page.json["received_pages"] == 1
    assert client.put(page_url, json=page_body, headers=page_headers).json == page.json
    complete_url = f"{prefix}/sync-snapshots/{snapshot_id}/complete"
    complete_body = {
        "contract_version": "1.0",
        "page_count": 1,
        "object_count": len(objects),
        "snapshot_sha256": full_digest,
    }
    complete_headers = headers(
        "vfx.sync.snapshot.write", idempotency=f"snapshot-complete-{uuid4()}"
    )
    completed = client.post(complete_url, json=complete_body, headers=complete_headers)
    assert completed.status_code == 200 and completed.json["state"] == "complete"
    assert (
        client.post(complete_url, json=complete_body, headers=complete_headers).json
        == completed.json
    )

    preview_url = f"{prefix}/productions/{production_id}/sync-previews"
    preview_body = {
        "contract_version": "1.0",
        "config_revision": 1,
        "snapshot_id": str(snapshot_id),
    }
    preview_before_activation = client.post(
        preview_url,
        json=preview_body,
        headers=headers("vfx.sync.preview", idempotency=f"inactive-preview-{uuid4()}"),
    )
    assert preview_before_activation.status_code == 200, preview_before_activation.get_data(as_text=True)
    assert preview_before_activation.json["state"] == "preview_ready"
    with zou_db_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.job_type == "sync_hierarchy",
                    Job.payload["production_id"].astext == str(production_id),
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(FolderBinding)
                .where(
                    FolderBinding.storage_connection_id == connection_id,
                )
            )
            == 0
        )
    assert (
        len(
            PersistentFakeStorage(
                storage_path, root_id="synthetic-root-id", drive_id="synthetic-drive-id"
            ).objects
        )
        == 1
    )

    with app.app_context(), zou_db_factory.begin() as session:
        session.get(
            StorageConnection, connection_id
        ).allowed_root_id = "changed-root-id"
    wrong_root_preview = client.post(
        preview_url,
        json=preview_body,
        headers=headers(
            "vfx.sync.preview", idempotency=f"wrong-root-preview-{uuid4()}"
        ),
    )
    assert (
        wrong_root_preview.status_code == 409
        and wrong_root_preview.json["code"] == "root_unverified"
    )
    with app.app_context(), zou_db_factory.begin() as session:
        session.get(
            StorageConnection, connection_id
        ).allowed_root_id = "synthetic-root-id"

    apply_url = f"{prefix}/productions/{production_id}/sync-jobs"
    premature_apply = client.post(
        apply_url,
        json={
            "contract_version": "1.0",
            "preview_id": preview_before_activation.json["preview_id"],
            "config_revision": 1,
        },
        headers=headers("vfx.sync.apply", idempotency=f"inactive-apply-{uuid4()}"),
    )
    assert (
        premature_apply.status_code == 409
        and premature_apply.json["code"] == "connection_not_ready"
    )
    with zou_db_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.job_type == "sync_hierarchy",
                    Job.payload["production_id"].astext == str(production_id),
                )
            )
            == 0
        )

    activation_url = f"{prefix}/productions/{production_id}/activation"
    activation_key = f"activation-enable-{uuid4()}"
    activation = client.post(
        activation_url,
        json={"contract_version": "1.0"},
        headers={
            **headers("vfx.activation.write", idempotency=activation_key),
            "If-Match": created.headers["ETag"],
        },
    )
    assert (
        activation.status_code == 200
        and activation.json["activation"]["enabled"] is True
    )
    assert activation.json["activation"]["notification_epoch"] == 1
    replayed_activation = client.post(
        activation_url,
        json={"contract_version": "1.0"},
        headers={
            **headers("vfx.activation.write", idempotency=activation_key),
            "If-Match": created.headers["ETag"],
        },
    )
    assert (
        replayed_activation.status_code == 200
        and replayed_activation.json == activation.json
    )

    refreshed_config = client.get(config_url, headers=headers("vfx.config.read"))
    assert refreshed_config.status_code == 200
    assert refreshed_config.json["config_revision"] == 2
    stale_snapshot_preview = client.post(
        preview_url,
        json=preview_body,
        headers=headers(
            "vfx.sync.preview", idempotency=f"stale-snapshot-preview-{uuid4()}"
        ),
    )
    assert stale_snapshot_preview.status_code == 409
    assert stale_snapshot_preview.json["code"] == "stale_config"

    # Activation increments config_revision. Upload a new immutable snapshot
    # for the activated revision even if the hierarchy bytes are unchanged.
    fresh_snapshot_id = uuid4()
    fresh_snapshot_body = {
        **snapshot_body,
        "snapshot_id": str(fresh_snapshot_id),
        "config_revision": refreshed_config.json["config_revision"],
        "source_etag": hashlib.sha256(
            b"synthetic-source-etag-after-activation"
        ).hexdigest(),
    }
    fresh_started = client.post(
        start_url,
        json=fresh_snapshot_body,
        headers=headers(
            "vfx.sync.snapshot.write", idempotency=f"fresh-snapshot-start-{uuid4()}"
        ),
    )
    assert fresh_started.status_code == 201
    fresh_page_url = f"{prefix}/sync-snapshots/{fresh_snapshot_id}/pages/0"
    fresh_page = client.put(
        fresh_page_url,
        json=page_body,
        headers=headers(
            "vfx.sync.snapshot.write", idempotency=f"fresh-snapshot-page-{uuid4()}"
        ),
    )
    assert fresh_page.status_code == 200
    fresh_complete_url = f"{prefix}/sync-snapshots/{fresh_snapshot_id}/complete"
    fresh_completed = client.post(
        fresh_complete_url,
        json=complete_body,
        headers=headers(
            "vfx.sync.snapshot.write", idempotency=f"fresh-snapshot-complete-{uuid4()}"
        ),
    )
    assert fresh_completed.status_code == 200

    fresh_preview_key = f"active-preview-{uuid4()}"
    fresh_preview_body = {
        "contract_version": "1.0",
        "config_revision": refreshed_config.json["config_revision"],
        "snapshot_id": str(fresh_snapshot_id),
    }
    fresh_preview = client.post(
        preview_url,
        json=fresh_preview_body,
        headers=headers("vfx.sync.preview", idempotency=fresh_preview_key),
    )
    assert (
        fresh_preview.status_code == 200
        and fresh_preview.json["state"] == "preview_ready"
    ), (fresh_preview.json or {}).get("code")
    assert fresh_preview.json["summary"]["create"] == 2
    fresh_preview_replay = client.post(
        preview_url,
        json=fresh_preview_body,
        headers=headers("vfx.sync.preview", idempotency=fresh_preview_key),
    )
    assert fresh_preview_replay.json == fresh_preview.json
    apply_body = {
        "contract_version": "1.0",
        "preview_id": fresh_preview.json["preview_id"],
        "config_revision": refreshed_config.json["config_revision"],
    }
    apply_key = f"explicit-apply-{uuid4()}"
    applied = client.post(
        apply_url,
        json=apply_body,
        headers=headers("vfx.sync.apply", idempotency=apply_key),
    )
    assert applied.status_code == 202 and applied.json["state"] == "queued"
    assert (
        client.post(
            apply_url,
            json=apply_body,
            headers=headers("vfx.sync.apply", idempotency=apply_key),
        ).json
        == applied.json
    )
    job_id = applied.json["id"]
    status_url = f"{prefix}/sync-jobs/{job_id}"
    queued = client.get(status_url, headers=headers("vfx.sync.read"))
    assert queued.status_code == 200 and queued.json["state"] == "queued"
    assert queued.json["progress"] == {"completed": 0, "total": 1}
    assert (
        client.get(
            status_url, headers=headers("vfx.sync.read", bound_actor=wrong_actor_id)
        ).status_code
        == 403
    )
    assert (
        client.get(
            status_url,
            headers=headers("vfx.sync.read", bound_production=wrong_production_id),
        ).status_code
        == 404
    )
    with zou_db_factory() as session:
        persisted = session.get(Job, job_id)
        assert persisted is not None and persisted.state == "queued"
        assert (
            session.scalar(
                select(func.count())
                .select_from(FolderBinding)
                .where(
                    FolderBinding.storage_connection_id == connection_id,
                )
            )
            == 0
        )
    assert (
        len(
            PersistentFakeStorage(
                storage_path, root_id="synthetic-root-id", drive_id="synthetic-drive-id"
            ).objects
        )
        == 1
    )

    worker_environment = os.environ.copy()
    worker_environment.update(
        {
            # Zou's child process validates the host secret at startup. Keep
            # this generated/synthetic test key inside the disposable worker.
            "SECRET_KEY": "vfx-files-isolated-worker-test-secret-key-32-bytes",
            "VFX_FILES_ENABLED": "true",
            "VFX_FILES_WORKER_ENABLED": "true",
            "VFX_FILES_STORAGE_MODE": "fake",
            "VFX_FILES_FAKE_STORAGE_ROOT": str(storage_path),
            "VFX_FILES_FAKE_ROOT_ID": "synthetic-root-id",
            "VFX_FILES_FAKE_DRIVE_ID": "synthetic-drive-id",
            "VFX_FILES_STAGING_ROOT": str(tmp_path / "staging"),
            "VFX_FILES_TEST_PLUGIN_FOLDER": os.environ.get(
                "VFX_FILES_TEST_PLUGIN_FOLDER", "/opt/zou/plugins"
            ),
            "PYTHONPATH": os.pathsep.join(filter(None, (
                os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER", "/opt/zou/plugins"),
                os.environ.get("PYTHONPATH", ""),
            ))),
        }
    )
    # --once intentionally claims the oldest eligible job across the durable
    # queue. A previous interrupted test may have left another synthetic job
    # ahead of this one, so run bounded separate Worker processes until this
    # exact persisted Job completes; never infer success from another Job.
    for _ in range(10):
        child = subprocess.run(
            [sys.executable, "-m", "vfx_files.worker_runtime", "--once"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=worker_environment,
        )
        assert child.returncode == 0
        child_result = json.loads(child.stdout.splitlines()[-1])["result"]
        assert child_result in ("completed", "idle")
        with zou_db_factory() as session:
            persisted = session.get(Job, job_id)
            if persisted is not None and persisted.state == "completed":
                break
        assert child_result != "idle", "Worker reported idle before the target Job completed"
    finished = client.get(status_url, headers=headers("vfx.sync.read"))
    assert finished.status_code == 200 and finished.json["state"] == "completed"
    assert finished.json["progress"] == {"completed": 1, "total": 1}
    with zou_db_factory() as session:
        persisted_snapshot = session.get(KitsuSyncHierarchySnapshot, snapshot_id)
        persisted_page = session.get(KitsuSyncHierarchySnapshotPage, (snapshot_id, 0))
        fresh_persisted_snapshot = session.get(
            KitsuSyncHierarchySnapshot, fresh_snapshot_id
        )
        bindings = session.scalars(
            select(FolderBinding).where(
                FolderBinding.storage_connection_id == connection_id,
            )
        ).all()
        persisted_job = session.get(Job, job_id)
        assert (
            persisted_snapshot.state == "complete"
            and persisted_page.page_sha256 == page_digest
        )
        assert fresh_persisted_snapshot.state == "complete"
        assert fresh_persisted_snapshot.hierarchy_sha256 == persisted_snapshot.hierarchy_sha256
        assert fresh_persisted_snapshot.id != persisted_snapshot.id
        assert persisted_job.state == "completed" and persisted_job.payload[
            "progress"
        ] == {"completed": 1, "total": 1}
        assert len(bindings) == 11 and all(
            binding.state == "ready" and binding.provider_folder_id
            for binding in bindings
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(KitsuSyncManagementIdempotency)
                .where(
                    KitsuSyncManagementIdempotency.production_id == production_id,
                )
            )
            >= 11
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(KitsuSyncDesiredConfiguration)
                .where(
                    KitsuSyncDesiredConfiguration.production_id == production_id,
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(KitsuSyncSyncPreview)
                .where(
                    KitsuSyncSyncPreview.production_id == production_id,
                )
            )
            == 2
        )
    materialized = PersistentFakeStorage(
        storage_path, root_id="synthetic-root-id", drive_id="synthetic-drive-id"
    )
    assert materialized.objects["synthetic-root-id"].name == "FakeStorage root"
    expected_folder_ids = {binding.provider_folder_id for binding in bindings}
    assert len(expected_folder_ids) == 11
    assert expected_folder_ids.issubset(materialized.objects)
    assert {materialized.objects[folder_id].name for folder_id in expected_folder_ids} == {
        "00_Production", "01_Shared_Assets", "02_Shots", "03_Editorial", "04_Delivery",
        shot.name, task.name, "Working", "Publish", "Shared Outputs", "Final Delivery",
    }

    # Restarting the separate worker process reads the persisted queue and
    # bindings; it must not create duplicate provider folders.
    restarted = subprocess.run(
        [sys.executable, "-m", "vfx_files.worker_runtime", "--once"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=worker_environment,
    )
    assert restarted.returncode == 0
    assert json.loads(restarted.stdout.splitlines()[-1])["result"] in ("completed", "idle")
    restarted_storage = PersistentFakeStorage(
        storage_path, root_id="synthetic-root-id", drive_id="synthetic-drive-id"
    )
    assert expected_folder_ids.issubset(restarted_storage.objects)
    assert (
        client.get(status_url, headers=headers("vfx.sync.read")).json["state"]
        == "completed"
    )

    # Exercise the signed HTTP command routes and durable, actor-bound replay
    # records on the same PostgreSQL installation.
    with zou_db_factory.begin() as session:
        session.get(Job, job_id).state = "failed"
    retry_url = f"{prefix}/sync-jobs/{job_id}/retry"
    retry_body = {"contract_version": "1.0", "reason_code": "synthetic_provider_retry"}
    retry_key = f"sync-retry-{uuid4()}"
    retry = client.post(
        retry_url,
        json=retry_body,
        headers=headers("vfx.sync.retry", idempotency=retry_key),
    )
    assert retry.status_code == 202 and retry.json["state"] == "queued"
    assert (
        client.post(
            retry_url,
            json=retry_body,
            headers=headers("vfx.sync.retry", idempotency=retry_key),
        ).json
        == retry.json
    )
    with zou_db_factory.begin() as session:
        session.get(Job, retry.json["id"]).state = "reconcile_required"
    reconcile_url = f"{prefix}/sync-jobs/{retry.json['id']}/reconcile"
    reconcile_body = {
        "contract_version": "1.0",
        "reason_code": "synthetic_provider_readback",
    }
    reconcile_key = f"sync-reconcile-{uuid4()}"
    reconciled = client.post(
        reconcile_url,
        json=reconcile_body,
        headers=headers("vfx.sync.reconcile", idempotency=reconcile_key),
    )
    assert reconciled.status_code == 202 and reconciled.json["state"] == "queued"
    assert (
        client.post(
            reconcile_url,
            json=reconcile_body,
            headers=headers("vfx.sync.reconcile", idempotency=reconcile_key),
        ).json
        == reconciled.json
    )
    assert (
        client.post(
            reconcile_url,
            json=reconcile_body,
            headers=headers(
                "vfx.sync.reconcile",
                bound_actor=wrong_actor_id,
                idempotency=f"wrong-actor-{uuid4()}",
            ),
        ).status_code
        == 403
    )

    # Do not leave test-generated follow-up jobs claimable by another host run.
    with zou_db_factory.begin() as session:
        for generated_id in (retry.json["id"], reconciled.json["id"]):
            session.get(Job, generated_id).state = "cancelled"
        session.get(Job, job_id).state = "completed"
