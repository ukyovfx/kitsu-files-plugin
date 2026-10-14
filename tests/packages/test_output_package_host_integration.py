"""Real Zou/PostgreSQL API plus package worker test for the pinned host suite."""

import hashlib
import io
import json
import os
import subprocess
import sys
from uuid import UUID, uuid4


def _run_worker_until_ready(ready, *, attempts=30):
    """Use separate production Worker processes until this test's row completes."""
    for _ in range(attempts):
        if ready():
            return
        result = subprocess.run(
            [sys.executable, "-m", "plugin.worker_runtime", "--once"],
            check=False, capture_output=True, text=True, env=os.environ.copy(), timeout=30,
        )
        assert result.returncode in (0, 2), "worker process failed"
        if result.stdout:
            try:
                state = json.loads(result.stdout.splitlines()[-1]).get("result")
            except (ValueError, AttributeError):
                state = None
            if state == "idle":
                break
    assert ready(), "durable job did not reach its expected persisted state"

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")


def test_task_api_to_worker_creates_immutable_package_copy_and_verified_download(
    authenticated_plugin_client, synthetic_upload_task, zou_db_factory, monkeypatch, tmp_path,
):
    from sqlalchemy import select
    from plugin.models import KitsuSyncDesiredConfiguration, KitsuSyncEventOutbox, OutputPackage, OutputPackageDelivery, OutputPackageDeliveryEvent, OutputPackageFile, OutputPackagePointer
    from plugin.models import WorkingRevision
    from plugin.storage.fake import PersistentFakeStorage
    from zou.app.models.output_type import OutputType

    app = authenticated_plugin_client.vfx_app
    production_id, entity_type_id, _task_type_id, entity_id, task_id, connection_id, actor_id = synthetic_upload_task
    fake_state = tmp_path / "fake-storage.json"
    storage = PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    monkeypatch.setenv("VFX_FILES_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_WORKER_ENABLED", "true")
    monkeypatch.setenv("VFX_FILES_STORAGE_MODE", "fake")
    monkeypatch.setenv("VFX_FILES_STAGING_ROOT", str(tmp_path))
    monkeypatch.setenv("VFX_FILES_FAKE_STORAGE_ROOT", str(fake_state))
    monkeypatch.setenv("VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id")
    monkeypatch.setenv("VFX_FILES_FAKE_DRIVE_ID", "synthetic-drive-id")
    content = b"artist A working snapshot"
    digest = hashlib.sha256(content).hexdigest()
    with app.app_context():
        output_type = OutputType(name=f"VFXFilesOutput{uuid4().hex[:10]}", short_name=f"vfx{uuid4().hex[:8]}")
        with zou_db_factory.begin() as session:
            session.add(output_type)
            session.flush()
            output_type_id = output_type.id
    monkeypatch.setitem(
        app.extensions, "vfx_files_storage_adapter_factory",
        lambda _connection: PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="synthetic-drive-id"),
    )
    with zou_db_factory.begin() as session:
        desired = session.get(KitsuSyncDesiredConfiguration, production_id)
        selected = dict(desired.desired_config)
        selected["activation"] = {
            "enabled": True, "activated_at": "2026-10-13T00:00:00Z",
            "notification_epoch": 1,
        }
        desired.notification_epoch = 1
        desired.desired_config = selected
    client = authenticated_plugin_client
    origin = "https://kitsu.test"
    headers = {"Origin": origin, "X-VFX-Files-CSRF": client.get("/plugins/vfx_files/csrf-token").json["csrf_token"]}

    # First create a real immutable Working revision through the installed API,
    # then let an independent worker process transfer and publish its file ID.
    working_route = f"/plugins/vfx_files/tasks/{task_id}/working-revisions"
    working = client.post(
        working_route,
        json={"base_revision_id": None, "files": [{"name": "plate.1001.exr", "size": len(content), "sha256": digest}]},
        headers={**headers, "Idempotency-Key": f"working:{uuid4()}"},
    )
    assert working.status_code == 201, working.get_data(as_text=True)
    upload_id = working.json["files"][0]["upload_session_id"]
    for index, start in enumerate(range(0, len(content), 4)):
        chunk = content[start:start + 4]
        part = client.put(
            f"/plugins/vfx_files/uploads/{upload_id}/parts/{index}", data=chunk,
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(content)}",
                "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
            },
        )
        assert part.status_code == 200, part.get_data(as_text=True)
    finalized = client.post(f"/plugins/vfx_files/uploads/{upload_id}/finalize", json={}, headers=headers)
    assert finalized.status_code == 202, finalized.get_data(as_text=True)
    _run_worker_until_ready(lambda: client.get(working_route).json["revisions"][0]["state"] == "completed")
    working_readback = client.get(working_route)
    assert working_readback.status_code == 200
    working_revision = working_readback.json["revisions"][0]
    assert working_revision["version_label"] == "w001"
    working_file = working_revision["files"][0]
    assert working_revision["state"] == "completed"
    assert working_file["provider_object_id"]
    storage = PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    assert storage.verify(working_file["provider_object_id"], len(content), digest)

    # Use a completed DB Working Revision as the source provenance for the
    # Output Package. This also gives the package request a verifiable w001 FK.
    with zou_db_factory.begin() as session:
        source_revision = session.get(WorkingRevision, UUID(working_revision["id"]))
        assert source_revision is not None and source_revision.state == "completed"

    options = client.get(f"/plugins/vfx_files/tasks/{task_id}/output-package-options")
    assert options.status_code == 200, options.get_data(as_text=True)
    output_options = options.json
    assert output_options["task_id"] == str(task_id)
    assert output_options["production_id"] == str(production_id)
    assert output_options["storage_root_id"] == "synthetic-root-id"
    assert str(output_type_id) in {item["id"] for item in output_options["output_types"]}
    selected_output_type_id = next(item["id"] for item in output_options["output_types"] if item["id"] == str(output_type_id))

    idempotency_key = f"output-package:{uuid4()}"
    invalid_output_type = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": str(uuid4()), "package_type": "final-exr", "frame_start": 1001,
            "frame_end": 1001, "source_working_revision_id": working_revision["id"],
            "files": [{"provider_object_id": working_file["provider_object_id"], "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": f"invalid:{uuid4()}"},
    )
    assert invalid_output_type.status_code == 400
    assert "Output Type must be an existing Kitsu record" in invalid_output_type.get_data(as_text=True)

    unrelated_bytes = b"not part of w001"
    unrelated_digest = hashlib.sha256(unrelated_bytes).hexdigest()
    storage.upload(
        io.BytesIO(unrelated_bytes), "unrelated-source", len(unrelated_bytes),
        unrelated_digest, "synthetic-root-id", file_name="unrelated.exr",
    )
    wrong_working_source = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": selected_output_type_id, "package_type": "final-exr",
            "frame_start": 1001, "frame_end": 1001,
            "source_working_revision_id": working_revision["id"],
            "files": [{"provider_object_id": "unrelated-source", "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": f"wrong-working-source:{uuid4()}"},
    )
    assert wrong_working_source.status_code == 400
    assert "must belong to the selected Working revision" in wrong_working_source.get_data(as_text=True)

    response = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": selected_output_type_id,
            "package_type": "final-exr",
            "frame_start": 1001,
            "frame_end": 1001,
            "source_working_revision_id": working_revision["id"],
            "files": [{"provider_object_id": working_file["provider_object_id"], "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert response.status_code == 202, response.get_data(as_text=True)
    reservation = response.json
    replay = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": selected_output_type_id,
            "package_type": "final-exr",
            "frame_start": 1001,
            "frame_end": 1001,
            "source_working_revision_id": working_revision["id"],
            "files": [{"provider_object_id": working_file["provider_object_id"], "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert replay.status_code == 202
    assert replay.json["id"] == reservation["id"]
    assert replay.json["version"] == reservation["version"] == 1

    _run_worker_until_ready(lambda: client.get(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages?limit=10&offset=0"
    ).json[0]["state"] == "completed")

    with zou_db_factory.begin() as session:
        package = session.get(OutputPackage, UUID(reservation["id"]))
        file_row = session.scalar(select(OutputPackageFile).where(OutputPackageFile.package_id == package.id))
        pointer = session.get(OutputPackagePointer, (package.task_id, package.output_type_id, package.package_type))
        assert package.state == "completed"
        assert package.qc_state == "passed"
        assert package.provider_folder_id
        assert str(package.source_working_revision_id) == working_revision["id"]
        assert file_row.source_provider_object_id == working_file["provider_object_id"]
        outbox = session.scalars(select(KitsuSyncEventOutbox).where(
            KitsuSyncEventOutbox.production_id == production_id,
        ).order_by(KitsuSyncEventOutbox.created_at, KitsuSyncEventOutbox.event_type)).all()
        assert {row.event_type for row in outbox} == {"output_package.created", "output_package.ready"}
        assert all(row.storage_connection_id == connection_id and row.payload["activation_epoch"] == 1 for row in outbox)
        assert all(row.state == "queued" for row in outbox)
        assert file_row.provider_object_id != file_row.source_provider_object_id
        assert file_row.sha256 == digest
        assert pointer.latest_output_id == package.id
        storage = PersistentFakeStorage(fake_state, root_id="synthetic-root-id", drive_id="synthetic-drive-id")
        copied = storage.get(file_row.provider_object_id)
        assert copied.parent_id == package.provider_folder_id
        assert copied.name == "plate.1001.exr"
        assert storage.verify(copied.object_id, len(content), digest)
        package_file_id = file_row.id

    listed = client.get(f"/plugins/vfx_files/tasks/{task_id}/output-packages?limit=10&offset=0")
    assert listed.status_code == 200
    assert listed.json[0]["id"] == reservation["id"]
    assert listed.json[0]["qc_state"] == "passed"
    assert "files" not in listed.json[0]["manifest_summary"]
    assert listed.json[0]["latest_published"] is False
    assert listed.json[0]["latest_approved"] is False
    file_page = client.get(f"/plugins/vfx_files/output-packages/{reservation['id']}/files?limit=1&offset=0")
    assert file_page.status_code == 200
    assert file_page.json["next_offset"] is None
    download = client.get(f"/plugins/vfx_files/output-package-files/{package_file_id}/download")
    assert download.status_code == 200
    assert download.data == content

    # An Output Package is not formally Published or Approved merely because
    # its own package QC passed. Link only an exact single-file package copy
    # to the completed, approved native Publish that references the same w001
    # source file and checksum.
    from plugin.approvals.service import approve_publish
    from plugin.models import Publish, PublishPointer
    publish_id = uuid4()
    output_family_id = uuid4()
    publish_manifest_sha256 = hashlib.sha256(b"synthetic exact Publish manifest").hexdigest()
    with zou_db_factory.begin() as session:
        session.add(Publish(
            id=publish_id, task_id=task_id, output_family_id=output_family_id,
            version=1, source_revision_file_id=UUID(working_file["id"]),
            created_by=uuid4(), state="completed", byte_count=len(content),
            sha256=digest, storage_connection_id=connection_id,
            provider_artifact_id=working_file["provider_object_id"],
            manifest_sha256=publish_manifest_sha256, manifest_bytes=b"synthetic exact Publish manifest",
            native_output_file_id=uuid4(), native_revision=1, qc_state="passed",
        ))
        session.add(PublishPointer(
            task_id=task_id, output_family_id=output_family_id,
            latest_published_id=publish_id,
        ))
    approve_publish(
        zou_db_factory, actor_id=uuid4(), actor_role="supervisor", publish_id=publish_id,
        policy_id="synthetic-review-v1", policy_sha256="a" * 64,
        idempotency_key=f"synthetic-approval:{publish_id}",
    )

    package_id = reservation["id"]
    wrong_publish_id = uuid4()
    wrong_publish_manifest_sha256 = hashlib.sha256(b"synthetic mismatched Publish manifest").hexdigest()
    with zou_db_factory.begin() as session:
        session.add(Publish(
            id=wrong_publish_id, task_id=task_id, output_family_id=uuid4(),
            version=1, source_revision_file_id=UUID(working_file["id"]),
            created_by=uuid4(), state="completed", byte_count=len(content),
            sha256="f" * 64, storage_connection_id=connection_id,
            provider_artifact_id="unrelated-publish-artifact",
            manifest_sha256=wrong_publish_manifest_sha256, manifest_bytes=b"synthetic mismatched Publish manifest",
            native_output_file_id=uuid4(), native_revision=1, qc_state="passed",
        ))
    approve_publish(
        zou_db_factory, actor_id=uuid4(), actor_role="supervisor", publish_id=wrong_publish_id,
        policy_id="synthetic-review-v1", policy_sha256="a" * 64,
        idempotency_key=f"synthetic-approval:{wrong_publish_id}",
    )
    mismatched_link = client.post(
        f"/plugins/vfx_files/output-packages/{package_id}/publication-binding",
        json={"publish_id": str(wrong_publish_id)},
        headers={**headers, "Idempotency-Key": f"package-publication:{uuid4()}"},
    )
    assert mismatched_link.status_code == 409

    publication_key = f"package-publication:{uuid4()}"
    bound = client.post(
        f"/plugins/vfx_files/output-packages/{package_id}/publication-binding",
        json={"publish_id": str(publish_id)},
        headers={**headers, "Idempotency-Key": publication_key},
    )
    assert bound.status_code == 201, bound.get_data(as_text=True)
    link_replay = client.post(
        f"/plugins/vfx_files/output-packages/{package_id}/publication-binding",
        json={"publish_id": str(publish_id)},
        headers={**headers, "Idempotency-Key": publication_key},
    )
    assert link_replay.status_code == 200
    assert link_replay.json["id"] == bound.json["id"]
    relisted = client.get(f"/plugins/vfx_files/tasks/{task_id}/output-packages?limit=10&offset=0")
    assert relisted.status_code == 200
    assert relisted.json[0]["formal_publish"]["id"] == str(publish_id)
    assert relisted.json[0]["latest_published"] is True
    assert relisted.json[0]["latest_approved"] is True

    # Production Files recipient selection, immutable response history and
    # contract-v1 delivery outbox are exercised through the actual Plugin API.
    from zou.app.models.department import Department
    from zou.app.models.entity import Entity
    from zou.app.models.person import DepartmentLink, Person
    from zou.app.models.project import Project, ProjectPersonLink, ProjectTaskTypeLink
    from zou.app.models.task import Task, TaskPersonLink
    from zou.app.models.task_type import TaskType
    from zou.app.services.persons_service import create_access_token_for_raw_person
    with zou_db_factory.begin() as session:
        recipient_person = Person(
            first_name="Synthetic", last_name=f"FX Artist {uuid4().hex[:8]}",
            email=f"fx-artist-{uuid4().hex}@example.invalid", role="user", active=True,
        )
        # Zou 1.0.94 requires a color for Department rows; keep the synthetic
        # recipient fixture valid against the pinned PostgreSQL schema.
        department = Department(name=f"VFX Effects {uuid4().hex[:8]}", color="#64748B")
        session.add_all([recipient_person, department])
        session.flush()
        recipient_type = TaskType(
            name=f"VFX FX {uuid4().hex[:8]}", for_entity="Shot",
            department_id=department.id,
        )
        session.add(recipient_type)
        session.flush()
        session.add(ProjectTaskTypeLink(project_id=production_id, task_type_id=recipient_type.id))
        recipient_task = Task(
            name="fx", project_id=production_id, task_type_id=recipient_type.id,
            entity_id=entity_id,
        )
        session.add(recipient_task)
        session.flush()
        session.add_all([
            ProjectPersonLink(project_id=production_id, person_id=recipient_person.id, role="user"),
            TaskPersonLink(task_id=recipient_task.id, person_id=recipient_person.id),
            DepartmentLink(department_id=department.id, person_id=recipient_person.id),
        ])
        session.flush()
        recipient_task_id = recipient_task.id
        recipient_type_id = recipient_type.id
        recipient_person_id = recipient_person.id
        other_production = Project(name=f"VFX Other Production {uuid4()}", team=[session.get(Person, actor_id)])
        session.add(other_production)
        session.flush()
        other_production_id = other_production.id
        other_entity = Entity(
            name=f"OTHER{uuid4().hex[:8]}", project_id=other_production.id,
            entity_type_id=entity_type_id,
        )
        session.add(other_entity)
        session.flush()
        cross_production_task = Task(
            name="fx", project_id=other_production.id,
            task_type_id=recipient_type_id, entity_id=other_entity.id,
        )
        session.add(cross_production_task)
        session.flush()
        cross_production_task_id = cross_production_task.id

    with app.app_context():
        token_person = app.extensions["sqlalchemy"].session.get(Person, recipient_person_id)
        token = create_access_token_for_raw_person(token_person)

    recipient_client = app.test_client()
    recipient_client.set_cookie(app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), token)
    recipient_headers = {
        "Origin": origin,
        "X-VFX-Files-CSRF": recipient_client.get("/plugins/vfx_files/csrf-token").json["csrf_token"],
    }

    targets = client.get(f"/plugins/vfx_files/output-packages/{package_id}/delivery-targets")
    assert targets.status_code == 200, targets.get_data(as_text=True)
    selected_target = next(item for item in targets.json["targets"] if item["task_id"] == str(recipient_task_id))
    assert selected_target["task_type_id"] == str(recipient_type_id)
    assert selected_target["department_name"].startswith("VFX Effects ")

    delivery_key = f"delivery:{uuid4()}"
    def send(package, key):
        return client.post(
            f"/plugins/vfx_files/output-packages/{package}/deliveries",
            json={"recipient_task_id": str(recipient_task_id), "recipient_task_type_id": str(recipient_type_id)},
            headers={**headers, "Idempotency-Key": key},
        )

    created = send(package_id, delivery_key)
    assert created.status_code == 201, created.get_data(as_text=True)
    delivery_id = created.json["id"]
    replayed = send(package_id, delivery_key)
    assert replayed.status_code == 200, replayed.get_data(as_text=True)
    assert replayed.json["id"] == delivery_id
    key_conflict = client.post(
        f"/plugins/vfx_files/output-packages/{package_id}/deliveries",
        json={"recipient_task_id": str(cross_production_task_id), "recipient_task_type_id": str(recipient_type_id)},
        headers={**headers, "Idempotency-Key": delivery_key},
    )
    assert key_conflict.status_code == 409
    cross_production = client.post(
        f"/plugins/vfx_files/output-packages/{package_id}/deliveries",
        json={"recipient_task_id": str(cross_production_task_id), "recipient_task_type_id": str(recipient_type_id)},
        headers={**headers, "Idempotency-Key": f"cross-production:{uuid4()}"},
    )
    assert cross_production.status_code == 403

    incoming = recipient_client.get(f"/plugins/vfx_files/tasks/{recipient_task_id}/output-package-deliveries")
    assert incoming.status_code == 200, incoming.get_data(as_text=True)
    assert any(row["id"] == delivery_id and row["direction"] == "incoming" for row in incoming.json["deliveries"])
    recipient_files = recipient_client.get(f"/plugins/vfx_files/output-packages/{package_id}/files?limit=10&offset=0")
    assert recipient_files.status_code == 200, recipient_files.get_data(as_text=True)
    recipient_download = recipient_client.get(
        f"/plugins/vfx_files/output-package-files/{package_file_id}/download"
    )
    assert recipient_download.status_code == 200
    assert recipient_download.data == content

    def respond(status, key, feedback=None):
        payload = {"status": status}
        if feedback is not None:
            payload["feedback"] = feedback
        return recipient_client.post(
            f"/plugins/vfx_files/output-package-deliveries/{delivery_id}/respond",
            json=payload, headers={**recipient_headers, "Idempotency-Key": key},
        )

    invalid_feedback = recipient_client.post(
        f"/plugins/vfx_files/output-package-deliveries/{delivery_id}/respond",
        json={"status": "needs_fix", "feedback": {"unexpected": "object"}},
        headers={**recipient_headers, "Idempotency-Key": f"invalid-feedback:{uuid4()}"},
    )
    assert invalid_feedback.status_code == 400
    needs_fix = respond("needs_fix", f"response:{uuid4()}", "Missing plate edge fix")
    assert needs_fix.status_code == 200, needs_fix.get_data(as_text=True)
    assert needs_fix.json["state"] == "needs_fix"

    # Register a new immutable Package version from the same Working revision,
    # then re-deliver it on the same stable recipient delivery identity.
    version_two = client.post(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages",
        json={
            "output_type_id": selected_output_type_id, "package_type": "final-exr",
            "frame_start": 1001, "frame_end": 1001,
            "source_working_revision_id": working_revision["id"],
            "files": [{"provider_object_id": working_file["provider_object_id"], "frame_number": 1001, "file_format": "exr", "color_space": "ACEScg"}],
        },
        headers={**headers, "Idempotency-Key": f"output-package:{uuid4()}"},
    )
    assert version_two.status_code == 202, version_two.get_data(as_text=True)
    _run_worker_until_ready(lambda: client.get(
        f"/plugins/vfx_files/tasks/{task_id}/output-packages?limit=10&offset=0"
    ).json[0]["state"] == "completed")
    publish_two_id = uuid4()
    publish_two_manifest_sha256 = hashlib.sha256(b"synthetic second Publish manifest").hexdigest()
    with zou_db_factory.begin() as session:
        session.add(Publish(
            id=publish_two_id, task_id=task_id, output_family_id=output_family_id,
            version=2, source_revision_file_id=UUID(working_file["id"]),
            created_by=uuid4(), state="completed", byte_count=len(content),
            sha256=digest, storage_connection_id=connection_id,
            provider_artifact_id=working_file["provider_object_id"],
            manifest_sha256=publish_two_manifest_sha256, manifest_bytes=b"synthetic second Publish manifest",
            native_output_file_id=uuid4(), native_revision=2, qc_state="passed",
        ))
    approval_two_id = approve_publish(
        zou_db_factory, actor_id=uuid4(), actor_role="supervisor", publish_id=publish_two_id,
        policy_id="synthetic-review-v1", policy_sha256="a" * 64,
        idempotency_key=f"synthetic-approval:{publish_two_id}",
    )
    link_two = client.post(
        f"/plugins/vfx_files/output-packages/{version_two.json['id']}/publication-binding",
        json={"publish_id": str(publish_two_id)},
        headers={**headers, "Idempotency-Key": f"package-publication:{uuid4()}"},
    )
    assert link_two.status_code == 201, link_two.get_data(as_text=True)
    revised = send(version_two.json["id"], f"delivery-revision:{uuid4()}")
    assert revised.status_code == 201, revised.get_data(as_text=True)
    assert revised.json["id"] == delivery_id
    assert revised.json["revision"] == 2
    assert revised.json["state"] == "revised"
    accepted = respond("accepted", f"response:{uuid4()}")
    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    assert accepted.json["state"] == "accepted"

    from plugin.approvals.service import revoke_approval
    revoke_approval(
        zou_db_factory, actor_id=uuid4(), actor_role="supervisor", approval_id=approval_two_id,
        reason="Synthetic verification of pointer revocation",
        idempotency_key=f"synthetic-revoke:{publish_two_id}",
    )
    revoked_package = client.get(f"/plugins/vfx_files/tasks/{task_id}/output-packages?limit=10&offset=0")
    assert revoked_package.status_code == 200
    assert revoked_package.json[0]["id"] == version_two.json["id"]
    assert revoked_package.json[0]["latest_published"] is True
    assert revoked_package.json[0]["latest_approved"] is False

    with zou_db_factory.begin() as session:
        delivery = session.get(OutputPackageDelivery, UUID(delivery_id))
        history = session.scalars(select(OutputPackageDeliveryEvent).where(
            OutputPackageDeliveryEvent.delivery_id == delivery.id,
        ).order_by(OutputPackageDeliveryEvent.created_at, OutputPackageDeliveryEvent.id)).all()
        assert [row.event_type for row in history] == ["delivered", "needs_fix", "revised", "accepted"]
        events = session.scalars(select(KitsuSyncEventOutbox).where(
            KitsuSyncEventOutbox.production_id == production_id,
            KitsuSyncEventOutbox.event_type.like("file_delivery.%"),
        )).all()
        assert {row.event_type for row in events} == {
            "file_delivery.created", "file_delivery.needs_fix",
            "file_delivery.revised", "file_delivery.accepted",
        }
        for event in events:
            assert event.storage_connection_id == connection_id
            assert event.payload["source"]["id"] == str(task_id)
            assert event.payload["source"]["id"] != event.payload["source"]["task_type_id"]
            assert event.payload["delivery"]["id"] == delivery_id
            assert event.payload["activation_epoch"] == 1
            assert "Missing plate edge fix" not in str(event.payload)

    # The destination is a real assigned Artist in its Department; an
    # unassigned user from the same Production cannot read the delivery.
    with app.app_context():
        from zou.app.models.person import Person
        outsider = Person(
            first_name="Synthetic", last_name=f"Outsider {uuid4().hex[:8]}",
            email=f"outsider-{uuid4().hex}@example.invalid", role="user", active=True,
        )
        with zou_db_factory.begin() as session:
            session.add(outsider)
            session.flush()
            outsider_id = outsider.id
            session.add(ProjectPersonLink(project_id=production_id, person_id=outsider_id, role="user"))
        token_person = app.extensions["sqlalchemy"].session.get(Person, outsider_id)
        outsider_token = create_access_token_for_raw_person(token_person)
    outsider_client = app.test_client()
    outsider_client.set_cookie(app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), outsider_token)
    assert outsider_client.get(
        f"/plugins/vfx_files/output-packages/{version_two.json['id']}/files?limit=10&offset=0"
    ).status_code == 403
    with zou_db_factory.begin() as session:
        session.execute(ProjectPersonLink.__table__.delete().where(
            ProjectPersonLink.project_id == production_id,
            ProjectPersonLink.person_id == outsider_id,
        ))
        session.delete(session.get(Person, outsider_id))
        session.execute(Task.__table__.delete().where(Task.id == cross_production_task_id))
        session.execute(Entity.__table__.delete().where(Entity.id == other_entity.id))
        other_project = session.get(Project, other_production_id)
        if other_project is not None:
            other_project.team.clear()
            session.flush()
            session.delete(other_project)
