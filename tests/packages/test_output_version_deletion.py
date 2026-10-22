"""Real Zou/PostgreSQL tests for Output version deletion, using FakeStorage only."""

from datetime import timedelta
from hashlib import sha256
import io
from uuid import UUID, uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

ORIGIN = "https://kitsu.test"
BASE = "/plugins/vfx_files"


def _headers(client):
    csrf = client.get(f"{BASE}/csrf-token").json["csrf_token"]
    return {"Origin": ORIGIN, "X-VFX-Files-CSRF": csrf}


def _user_client(app, person_id):
    from zou.app.models.person import Person
    from zou.app.services.persons_service import create_access_token_for_raw_person

    with app.app_context():
        token = create_access_token_for_raw_person(Person.query.filter_by(id=person_id).one())
    client = app.test_client()
    client.set_cookie(app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), token)
    return client


class Ctx:
    pass


@pytest.fixture
def ctx(authenticated_plugin_client, synthetic_upload_task, zou_db_factory, monkeypatch):
    from plugin.models import KitsuSyncDesiredConfiguration
    from plugin.storage.fake import FakeStorage
    from zou.app.models.output_type import OutputType

    app = authenticated_plugin_client.vfx_app
    production_id, entity_type_id, task_type_id, entity_id, task_id, connection_id, admin_id = synthetic_upload_task
    c = Ctx()
    c.app, c.db, c.admin_client = app, zou_db_factory, authenticated_plugin_client
    c.production_id, c.entity_id, c.task_id, c.connection_id, c.admin_id = production_id, entity_id, task_id, connection_id, admin_id
    c.entity_type_id, c.task_type_id = entity_type_id, task_type_id
    c.storage = FakeStorage(root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    c.storage.create_folder(c.storage.root_id, "source-folder", {"display_name": "Shared Inputs"})
    monkeypatch.setitem(app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: c.storage)
    with app.app_context(), zou_db_factory.begin() as session:
        output_type = OutputType(name=f"VFXDel{uuid4().hex[:10]}", short_name=f"del{uuid4().hex[:8]}")
        session.add(output_type)
        session.flush()
        c.output_type_id = output_type.id
        desired = session.get(KitsuSyncDesiredConfiguration, production_id)
        selected = dict(desired.desired_config)
        selected["activation"] = {"enabled": True, "activated_at": "2026-10-13T00:00:00Z", "notification_epoch": 1}
        desired.notification_epoch = 1
        desired.desired_config = selected
    c.package_type = f"final-{uuid4().hex[:8]}"
    yield c
    # These tests drive jobs by hand; never leave claimable work for other tests' real workers.
    from sqlalchemy import text

    with zou_db_factory.begin() as session:
        session.execute(text(
            "UPDATE plugin_vfx_files_job SET state = 'completed', lease_owner = NULL, lease_expires_at = NULL "
            "WHERE payload->>'task_id' = :task_id AND state IN ('queued','retry','leased')"
        ), {"task_id": str(task_id)})


def _person(c, *, role="user", member=True, assigned=True, production_id=None):
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import TaskPersonLink

    with c.app.app_context(), c.db.begin() as session:
        person = Person(first_name=f"Del{uuid4().hex[:8]}", last_name=role.title(), role="user", active=True)
        session.add(person)
        session.flush()
        if member:
            session.add(ProjectPersonLink(project_id=production_id or c.production_id, person_id=person.id, role=role))
        if assigned:
            session.add(TaskPersonLink(task_id=c.task_id, person_id=person.id))
        return person.id


def _source(c, name, content):
    digest = sha256(content).hexdigest()
    object_id = f"src-{uuid4().hex}"
    c.storage.upload(io.BytesIO(content), object_id, len(content), digest, "source-folder", file_name=name)
    return {
        "source_provider_object_id": object_id, "file_name": name, "byte_count": len(content),
        "sha256": digest, "frame_number": None, "file_format": "exr", "color_space": "ACEScg", "is_proxy": False,
    }


def _lease(c, job_id):
    from sqlalchemy import func, select
    from plugin.jobs.queue import LeasedJob
    from plugin.models import Job

    worker_id = uuid4()
    with c.db.begin() as session:
        job = session.get(Job, job_id)
        job.state, job.lease_owner = "leased", worker_id
        job.lease_generation += 1
        job.attempts += 1
        job.lease_expires_at = session.scalar(select(func.now())) + timedelta(minutes=2)
        return LeasedJob(job.id, job.job_type, dict(job.payload), worker_id, job.lease_generation, job.attempts)


def _register(c, actor_id, count=1, *, sequence=False, package_type=None):
    """Reserve and complete one Output version through the real verification worker."""
    from plugin.packages.service import reserve_output_package
    from plugin.packages.worker import make_output_package_handler

    files = []
    for index in range(count):
        item = _source(c, f"plate.{1001 + index}.exr" if sequence else f"plate-{uuid4().hex[:6]}.exr",
                       f"frame-{uuid4()}".encode())
        if sequence:
            item["frame_number"] = 1001 + index
        files.append(item)
    reservation = reserve_output_package(
        c.db, task_id=c.task_id, production_id=c.production_id, entity_id=c.entity_id,
        output_type_id=c.output_type_id, package_type=package_type or c.package_type, actor_id=actor_id,
        idempotency_key=f"k-{uuid4()}", files=files, storage_connection_id=c.connection_id,
        frame_start=1001 if sequence else None, frame_end=1000 + count if sequence else None,
        color_space="ACEScg",
    )
    result = make_output_package_handler(c.db, lambda _c: c.storage)(_lease(c, reservation.job_id))
    assert result.state == "completed"
    return reservation


def _deletion_handler(c, storage=None):
    from plugin.packages.deletion import make_output_package_deletion_handler

    return make_output_package_deletion_handler(c.db, lambda _c: storage or c.storage)


def _impact(client, package_id):
    response = client.get(f"{BASE}/output-packages/{package_id}/deletion")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.json


def _delete(client, package_id, *, digest=None, confirm=True):
    if digest is None:
        digest = _impact(client, package_id)["impact_sha256"]
    return client.post(
        f"{BASE}/output-packages/{package_id}/deletion",
        json={"confirm": confirm, "impact_sha256": digest}, headers=_headers(client),
    )


def _run_delete(c, package_id, response):
    result = _deletion_handler(c)(_lease(c, UUID(response.json["job_id"])))
    assert result.state == "completed", result
    return result


def _package_files(c, package_id):
    from sqlalchemy import select
    from plugin.models import OutputPackage, OutputPackageFile

    with c.db.begin() as session:
        package = session.get(OutputPackage, UUID(str(package_id)))
        rows = session.scalars(select(OutputPackageFile).where(
            OutputPackageFile.package_id == package.id).order_by(OutputPackageFile.ordinal)).all()
        return package.provider_folder_id, [(row.id, row.provider_object_id, row.file_name) for row in rows]


def test_registrant_deletes_own_past_versions_without_touching_others_or_reusing_numbers(ctx):
    from sqlalchemy import select
    from plugin.models import OutputPackage, OutputPackageDeletion, OutputPackageFile

    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    v1, v2, v3 = (_register(c, artist_id) for _ in range(3))
    assert (v1.version, v2.version, v3.version) == (1, 2, 3)
    folder1, files1 = _package_files(c, v1.package_id)
    folder2, files2 = _package_files(c, v2.package_id)
    source_ids = {key for key in c.storage.objects if key.startswith("src-")}

    assert _impact(artist, v1.package_id)["can_delete"] is True
    response = _delete(artist, v1.package_id)
    assert response.status_code == 202 and response.json["state"] == "deleting"
    # Downloads stop as soon as the deletion is recorded, before the worker runs.
    assert artist.get(f"{BASE}/output-package-files/{files1[0][0]}/download").status_code == 410
    _run_delete(c, v1.package_id, response)

    assert files1[0][1] not in c.storage.objects and folder1 not in c.storage.objects
    assert files2[0][1] in c.storage.objects and folder2 in c.storage.objects
    assert source_ids <= set(c.storage.objects)  # the copy sources are never deleted
    assert artist.get(f"{BASE}/output-package-files/{files2[0][0]}/download").status_code == 200

    # A past, non-latest version is deletable; the newest can go too, and v1 stays a visible tombstone.
    response2 = _delete(artist, v3.package_id)
    _run_delete(c, v3.package_id, response2)
    listed = {row["version"]: row for row in artist.get(f"{BASE}/tasks/{c.task_id}/output-packages").json}
    assert listed[1]["deleted"] and listed[1]["deletion_state"] == "deleted" and listed[1]["deleted_at"]
    assert listed[3]["deleted"] and not listed[2]["deleted"]
    assert listed[1]["created_by"] == str(artist_id) and listed[1]["deleted_by"] == str(artist_id)
    assert listed[2]["can_delete"] is True and listed[1]["can_delete"] is False
    files_page = artist.get(f"{BASE}/output-packages/{v1.package_id}/files").json
    assert files_page["deleted"] and files_page["files"][0]["download_url"] is None
    assert files_page["files"][0]["provider_object_id"] is None

    # Number reuse is impossible: deleted rows still count toward the version sequence.
    v4 = _register(c, artist_id)
    assert v4.version == 4
    with c.db.begin() as session:
        assert sorted(session.scalars(select(OutputPackage.version).where(
            OutputPackage.task_id == c.task_id, OutputPackage.package_type == c.package_type))) == [1, 2, 3, 4]
        assert session.scalar(select(OutputPackageFile.id).where(OutputPackageFile.package_id == v1.package_id)) is not None
        deletion = session.scalar(select(OutputPackageDeletion).where(OutputPackageDeletion.package_id == v1.package_id))
        assert deletion.requested_by == artist_id and deletion.completed_at is not None
        assert deletion.result == {"files_deleted": 1, "files_already_absent": 0, "files_retained_shared": 0, "folder": "deleted"}


def test_other_users_outsiders_and_other_productions_are_rejected_server_side(ctx):
    from sqlalchemy import select
    from plugin.models import Job, OutputPackageDeletion
    from zou.app.models.project import Project

    c = ctx
    owner_id, other_id = _person(c), _person(c)
    outsider_id = _person(c, member=False, assigned=False)
    unassigned_member_id = _person(c, assigned=False)
    package = _register(c, owner_id)
    digest = _impact(c.admin_client, package.package_id)["impact_sha256"]
    for person_id in (other_id, outsider_id, unassigned_member_id):
        client = _user_client(c.app, person_id)
        assert client.post(f"{BASE}/output-packages/{package.package_id}/deletion",
                           json={"confirm": True, "impact_sha256": digest}, headers=_headers(client)).status_code == 403
        preview = client.get(f"{BASE}/output-packages/{package.package_id}/deletion")
        if person_id == other_id:  # a Task-assigned colleague may look, but is not offered the action
            assert preview.status_code == 200 and preview.json["can_delete"] is False
        else:
            assert preview.status_code == 403

    # A member of a different Production cannot delete through their own Production's scope.
    with c.app.app_context(), c.db.begin() as session:
        from zou.app.models.person import Person
        from zou.app.models.project import ProjectPersonLink

        foreign = Project(name=f"Foreign {uuid4()}")
        session.add(foreign)
        person = Person(first_name=f"Fx{uuid4().hex[:8]}", last_name="Manager", role="user", active=True)
        session.add(person)
        session.flush()
        session.add(ProjectPersonLink(project_id=foreign.id, person_id=person.id, role="manager"))
        foreign_manager_id = person.id
    foreign_client = _user_client(c.app, foreign_manager_id)
    assert foreign_client.post(f"{BASE}/output-packages/{package.package_id}/deletion",
                               json={"confirm": True, "impact_sha256": digest},
                               headers=_headers(foreign_client)).status_code == 403
    # Cookie-only (no Origin/CSRF) is rejected even for the registrant.
    owner = _user_client(c.app, owner_id)
    assert owner.post(f"{BASE}/output-packages/{package.package_id}/deletion",
                      json={"confirm": True, "impact_sha256": digest}).status_code == 403
    assert owner.post(f"{BASE}/output-packages/not-a-uuid/deletion",
                      json={"confirm": True, "impact_sha256": digest}, headers=_headers(owner)).status_code == 404

    with c.db.begin() as session:
        assert session.scalar(select(OutputPackageDeletion.id).where(OutputPackageDeletion.package_id == package.package_id)) is None
        assert session.scalar(select(Job.id).where(Job.job_type == "delete_output_package",
                                                   Job.payload["package_id"].astext == str(package.package_id))) is None
    assert any(key.startswith("fake-") for key in c.storage.objects)

    # The Production administrator can delete someone else's version.
    response = _delete(c.admin_client, package.package_id)
    assert response.status_code == 202
    _run_delete(c, package.package_id, response)


def test_production_policy_is_admin_managed_and_applied_on_the_server(ctx):
    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    package = _register(c, artist_id)

    assert artist.get(f"{BASE}/productions/{c.production_id}/output-delete-policy").json == {
        "policy": "registrant_and_admin", "policies": ["registrant_and_admin", "admin_only"], "can_change": False}
    # Artists cannot change the setting; invalid values are rejected for admins.
    assert artist.post(f"{BASE}/productions/{c.production_id}/output-delete-policy",
                       json={"policy": "admin_only"}, headers=_headers(artist)).status_code == 403
    assert c.admin_client.post(f"{BASE}/productions/{c.production_id}/output-delete-policy",
                               json={"policy": "everyone"}, headers=_headers(c.admin_client)).status_code == 400
    response = c.admin_client.post(f"{BASE}/productions/{c.production_id}/output-delete-policy",
                                   json={"policy": "admin_only"}, headers=_headers(c.admin_client))
    assert response.status_code == 200 and response.json == {"policy": "admin_only"}

    digest = _impact(artist, package.package_id)
    assert digest["can_delete"] is False and digest["policy"] == "admin_only"
    assert _delete(artist, package.package_id).status_code == 403
    listed = artist.get(f"{BASE}/tasks/{c.task_id}/output-packages").json
    assert listed[0]["can_delete"] is False
    assert _impact(c.admin_client, package.package_id)["can_delete"] is True

    c.admin_client.post(f"{BASE}/productions/{c.production_id}/output-delete-policy",
                        json={"policy": "registrant_and_admin"}, headers=_headers(c.admin_client))
    assert _delete(artist, package.package_id).status_code == 202


def test_confirmation_and_impact_digest_are_required_and_history_survives_deletion(ctx):
    from sqlalchemy import select
    from plugin.models import (
        OutputPackage, OutputPackageDelivery, OutputPackageDeliveryEvent, OutputPackagePointer,
    )
    from zou.app.models.task import Task

    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    package = _register(c, artist_id)

    assert _delete(artist, package.package_id, confirm=False).status_code == 400
    assert artist.post(f"{BASE}/output-packages/{package.package_id}/deletion", json={"confirm": True},
                       headers=_headers(artist)).status_code == 400
    reviewed = _impact(artist, package.package_id)
    assert reviewed["impact"]["deliveries"] == [] and reviewed["impact"]["version"] == 1

    # Add a sent-and-reviewed Delivery after the impact was shown; the stale confirmation is refused.
    with c.app.app_context(), c.db.begin() as session:
        recipient = Task(name="comp", project_id=c.production_id, task_type_id=c.task_type_id, entity_id=c.entity_id)
        session.add(recipient)
        session.flush()
        delivery = OutputPackageDelivery(
            package_id=package.package_id, production_id=c.production_id, recipient_task_id=recipient.id,
            recipient_task_type_id=c.task_type_id, state="accepted", idempotency_key=f"d-{uuid4()}", created_by=artist_id,
        )
        session.add(delivery)
        session.flush()
        session.add(OutputPackageDeliveryEvent(
            delivery_id=delivery.id, actor_id=artist_id, event_type="accepted", feedback="OK", idempotency_key=f"e-{uuid4()}"))
        delivery_id = delivery.id
        recipient_id = recipient.id
    stale = _delete(artist, package.package_id, digest=reviewed["impact_sha256"])
    assert stale.status_code == 409 and "impact_changed" in stale.get_data(as_text=True)

    fresh = _impact(artist, package.package_id)
    assert fresh["impact"]["deliveries"] == [{
        "id": str(delivery_id), "recipient_task_id": str(recipient_id), "revision": 1, "state": "accepted"}]
    response = _delete(artist, package.package_id, digest=fresh["impact_sha256"])
    assert response.status_code == 202
    _run_delete(c, package.package_id, response)

    with c.db.begin() as session:
        stored = session.get(OutputPackage, package.package_id)
        assert (stored.version, stored.created_by, stored.task_id, stored.state) == (1, artist_id, c.task_id, "completed")
        kept = session.get(OutputPackageDelivery, delivery_id)
        assert (kept.state, kept.recipient_task_id) == ("accepted", recipient_id)
        events = session.scalars(select(OutputPackageDeliveryEvent).where(
            OutputPackageDeliveryEvent.delivery_id == delivery_id)).all()
        assert [(e.event_type, e.feedback) for e in events] == [("accepted", "OK")]
        pointer = session.get(OutputPackagePointer, (c.task_id, c.output_type_id, c.package_type))
        assert pointer.latest_output_id == package.package_id
        from plugin.models import OutputPackageDeletion
        deletion = session.scalar(select(OutputPackageDeletion).where(OutputPackageDeletion.package_id == package.package_id))
        assert deletion.impact_snapshot["deliveries"][0]["state"] == "accepted"


def test_duplicate_requests_and_replays_are_idempotent(ctx):
    from sqlalchemy import func, select
    from plugin.models import Job, OutputPackageDeletion

    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    package = _register(c, artist_id)
    first = _delete(artist, package.package_id)
    second = _delete(artist, package.package_id)
    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json["job_id"] == second.json["job_id"]
    handler = _deletion_handler(c)
    leased = _lease(c, UUID(first.json["job_id"]))
    assert handler(leased).state == "completed"
    assert handler(leased).state == "completed"  # lost completion response replay
    assert handler(_lease(c, UUID(first.json["job_id"]))).state == "completed"
    third = _delete(artist, package.package_id, digest="0" * 64)  # after completion the digest no longer matters
    assert third.status_code == 202 and third.json["state"] == "deleted"
    with c.db.begin() as session:
        assert session.scalar(select(func.count()).select_from(OutputPackageDeletion).where(
            OutputPackageDeletion.package_id == package.package_id)) == 1
        assert session.scalar(select(func.count()).select_from(Job).where(
            Job.job_type == "delete_output_package", Job.payload["package_id"].astext == str(package.package_id))) == 1


def test_drive_failures_never_report_success_and_recover_on_retry(ctx):
    from sqlalchemy import select
    from plugin.models import Job, OutputPackageDeletion
    from plugin.storage.base import AmbiguousOutcome, PermissionFailure, RetryableStorageFailure

    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    package = _register(c, artist_id, count=3, sequence=True)
    folder_id, files = _package_files(c, package.package_id)
    target_ids = [row[1] for row in files]
    response = _delete(artist, package.package_id)
    job_id = UUID(response.json["job_id"])

    def state():
        with c.db.begin() as session:
            return session.scalar(select(OutputPackageDeletion.state).where(OutputPackageDeletion.package_id == package.package_id))

    # Permission failure: nothing is reported deleted, downloads stay blocked, files remain.
    c.storage.permission_denied_delete_ids.add(target_ids[1])
    with pytest.raises(PermissionFailure):
        _deletion_handler(c)(_lease(c, job_id))
    assert state() == "deleting"
    assert artist.get(f"{BASE}/output-package-files/{files[2][0]}/download").status_code == 410
    c.storage.permission_denied_delete_ids.clear()

    # Ambiguous provider outcome on a delete: still not success; the next attempt completes it.
    c.storage.fail_delete_ids.add(target_ids[2])
    with pytest.raises(AmbiguousOutcome):
        _deletion_handler(c)(_lease(c, job_id))
    assert state() == "deleting"

    # The provider deleted the object but the response was lost: the retry sees it absent.
    c.storage.fail_delete_after_ids.add(target_ids[2])
    with pytest.raises(AmbiguousOutcome):
        _deletion_handler(c)(_lease(c, job_id))
    assert target_ids[2] not in c.storage.objects and state() == "deleting"

    # Timeouts are retryable failures.
    class Timeout(type(c.storage)):
        def delete(self, object_id):
            raise RetryableStorageFailure(5, "timeout")

    with pytest.raises(RetryableStorageFailure):
        _deletion_handler(c, storage=_clone_as(Timeout, c.storage))(_lease(c, job_id))
    assert state() == "deleting"

    assert _deletion_handler(c)(_lease(c, job_id)).state == "completed"
    assert state() == "deleted"
    assert folder_id not in c.storage.objects and not set(target_ids) & set(c.storage.objects)
    with c.db.begin() as session:
        deletion = session.scalar(select(OutputPackageDeletion).where(OutputPackageDeletion.package_id == package.package_id))
        assert deletion.result["files_deleted"] + deletion.result["files_already_absent"] == 3

    # A terminal job failure is recoverable by repeating the authorized request.
    package2 = _register(c, artist_id)
    response2 = _delete(artist, package2.package_id)
    with c.db.begin() as session:
        job = session.get(Job, UUID(response2.json["job_id"]))
        job.state, job.attempts, job.last_error_code = "failed", 5, "provider_permission_denied"
    again = _delete(artist, package2.package_id)
    assert again.status_code == 202 and again.json["job_id"] == response2.json["job_id"]
    with c.db.begin() as session:
        job = session.get(Job, UUID(response2.json["job_id"]))
        assert (job.state, job.attempts, job.last_error_code) == ("queued", 0, None)


def _clone_as(cls, storage):
    clone = cls.__new__(cls)
    clone.__dict__ = storage.__dict__
    return clone


def test_exr_sequence_deletes_only_its_own_files_and_never_recurses(ctx):
    from plugin.storage.base import StorageObject

    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    sequence = _register(c, artist_id, count=5, sequence=True)
    sibling = _register(c, artist_id, count=2, sequence=True, package_type=f"other-{uuid4().hex[:6]}")
    folder_id, files = _package_files(c, sequence.package_id)
    sibling_folder, sibling_files = _package_files(c, sibling.package_id)
    outside = set(c.storage.objects) - {folder_id} - {row[1] for row in files}

    # An unrelated file someone placed in the version folder keeps the folder (no recursive delete).
    c.storage.objects["foreign-note"] = StorageObject("foreign-note", folder_id, 3, sha256(b"abc").hexdigest(),
                                                      "synthetic-drive-id", "text/plain", name="notes.txt")
    c.storage._bytes["foreign-note"] = b"abc"
    response = _delete(artist, sequence.package_id)
    _run_delete(c, sequence.package_id, response)
    assert not {row[1] for row in files} & set(c.storage.objects)
    assert "foreign-note" in c.storage.objects and folder_id in c.storage.objects
    assert outside - {"foreign-note"} <= set(c.storage.objects)
    assert sibling_folder in c.storage.objects and all(row[1] in c.storage.objects for row in sibling_files)
    from sqlalchemy import select
    from plugin.models import OutputPackageDeletion
    with c.db.begin() as session:
        result = session.scalar(select(OutputPackageDeletion.result).where(OutputPackageDeletion.package_id == sequence.package_id))
    assert result["files_deleted"] == 5 and result["folder"] == "retained_not_empty"

    # Without foreign content the whole version folder goes with its files.
    response2 = _delete(artist, sibling.package_id)
    _run_delete(c, sibling.package_id, response2)
    assert sibling_folder not in c.storage.objects and not {row[1] for row in sibling_files} & set(c.storage.objects)


def test_shared_or_misowned_objects_are_protected_and_ownership_mismatch_deletes_nothing(ctx):
    from sqlalchemy import select
    from plugin.models import LinkShare, OutputPackage, OutputPackageDeletion, OutputPackageFile

    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    shared = _register(c, artist_id, count=3, sequence=True)
    folder_id, files = _package_files(c, shared.package_id)

    # Another (still-verifying) version references one copy as a provider object and another as a source;
    # a Task link share references the third. None of them may be deleted by this version.
    other = _register(c, artist_id, count=1, package_type=f"x-{uuid4().hex[:6]}")
    with c.db.begin() as session:
        loose = OutputPackage(
            id=uuid4(), production_id=c.production_id, entity_id=c.entity_id, task_id=c.task_id,
            output_type_id=c.output_type_id, package_type=f"y-{uuid4().hex[:6]}", version=1, state="verifying",
            manifest_bytes=b"{}", manifest_sha256="0" * 64, created_by=artist_id)
        session.add(loose)
        session.flush()
        session.add_all([
            OutputPackageFile(id=uuid4(), package_id=loose.id, ordinal=0, file_name="a", provider_object_id=files[0][1],
                              source_provider_object_id="elsewhere-1", byte_count=1),
            OutputPackageFile(id=uuid4(), package_id=loose.id, ordinal=1, file_name="b", provider_object_id="elsewhere-2",
                              source_provider_object_id=files[1][1], byte_count=1),
            LinkShare(production_id=c.production_id, source_task_id=c.task_id, source_entity_id=c.entity_id,
                      source_entity_type="shot", actor_id=artist_id, storage_connection_id=c.connection_id,
                      object_id=files[2][1], request_idempotency_key=f"s-{uuid4()}", request_sha256="1" * 64, state="created"),
        ])
    response = _delete(artist, shared.package_id)
    _run_delete(c, shared.package_id, response)
    assert all(row[1] in c.storage.objects for row in files) and folder_id in c.storage.objects
    with c.db.begin() as session:
        result = session.scalar(select(OutputPackageDeletion.result).where(OutputPackageDeletion.package_id == shared.package_id))
    assert result["files_deleted"] == 0 and result["files_retained_shared"] == 3 and result["folder"] == "retained_not_empty"

    # A tampered or foreign object under a recorded ID blocks the entire operation before any delete call.
    victim = _register(c, artist_id, count=3, sequence=True)
    victim_folder, victim_files = _package_files(c, victim.package_id)
    renamed = c.storage.objects[victim_files[1][1]]
    c.storage.objects[renamed.object_id] = type(renamed)(
        renamed.object_id, "some-other-folder", renamed.size, renamed.sha256, renamed.drive_id, renamed.mime_type, name=renamed.name)
    response = _delete(artist, victim.package_id)
    calls_before = len([call for call in c.storage.calls if call[0] == "delete"])
    result = _deletion_handler(c)(_lease(c, UUID(response.json["job_id"])))
    assert (result.state, result.error_code) == ("failed", "output_delete_target_ownership_mismatch")
    assert len([call for call in c.storage.calls if call[0] == "delete"]) == calls_before
    assert all(row[1] in c.storage.objects for row in victim_files)
    with c.db.begin() as session:
        assert session.scalar(select(OutputPackageDeletion.state).where(OutputPackageDeletion.package_id == victim.package_id)) == "deleting"
    # The other registered version is entirely unaffected.
    assert other.package_id and any(key.startswith("fake-") for key in c.storage.objects)


def test_worker_authorization_and_history_immutability(ctx):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from plugin.models import OutputPackageDeletion
    from plugin.worker_runtime import _authorize_job

    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    package = _register(c, artist_id)
    payload = {"job_type": "delete_output_package", "package_id": str(package.package_id), "actor_id": str(artist_id)}
    with c.app.app_context():
        assert _authorize_job(payload) is False  # no recorded deletion yet
        response = _delete(artist, package.package_id)
        assert _authorize_job(payload) is True
        assert _authorize_job({**payload, "actor_id": str(uuid4())}) is False
        assert _authorize_job({**payload, "package_id": "bad"}) is False
    _run_delete(c, package.package_id, response)
    with c.app.app_context():
        assert _authorize_job(payload) is False  # finished deletions are not runnable again
    for statement in (
        "UPDATE plugin_vfx_files_output_package_deletion SET requested_by = gen_random_uuid()",
        "UPDATE plugin_vfx_files_output_package_deletion SET state = 'deleting'",
        "DELETE FROM plugin_vfx_files_output_package_deletion",
    ):
        with pytest.raises(DBAPIError):
            with c.db.begin() as session:
                session.execute(text(statement + f" WHERE package_id = '{package.package_id}'"))
