"""M2-1: Drive links for Output versions; no Plugin byte-proxy against real Drive. FakeStorage only."""

from datetime import timedelta
from hashlib import sha256
import io
import pathlib
from uuid import UUID, uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

ORIGIN = "https://kitsu.test"
BASE = "/plugins/vfx_files"
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2] / "plugin"


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
    from sqlalchemy import text
    from plugin.models import KitsuSyncDesiredConfiguration
    from plugin.storage.fake import FakeStorage
    from zou.app.models.output_type import OutputType

    app = authenticated_plugin_client.vfx_app
    production_id, _et, _tt, entity_id, task_id, connection_id, admin_id = synthetic_upload_task
    c = Ctx()
    c.app, c.db, c.admin_client, c.monkeypatch = app, zou_db_factory, authenticated_plugin_client, monkeypatch
    c.production_id, c.entity_id, c.task_id, c.connection_id, c.admin_id = production_id, entity_id, task_id, connection_id, admin_id
    c.storage = FakeStorage(root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    c.storage.create_folder(c.storage.root_id, "source-folder", {"display_name": "Shared Inputs"})
    monkeypatch.setitem(app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: c.storage)
    with app.app_context(), zou_db_factory.begin() as session:
        output_type = OutputType(name=f"VFXLink{uuid4().hex[:10]}", short_name=f"lnk{uuid4().hex[:8]}")
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
    with zou_db_factory.begin() as session:  # never leave claimable jobs for other tests' workers
        session.execute(text(
            "UPDATE plugin_vfx_files_job SET state = 'completed', lease_owner = NULL, lease_expires_at = NULL "
            "WHERE payload->>'task_id' = :t AND state IN ('queued','retry','leased')"), {"t": str(task_id)})


def _person(c, *, role="user", member=True, assigned=True):
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import TaskPersonLink

    with c.app.app_context(), c.db.begin() as session:
        person = Person(first_name=f"Lnk{uuid4().hex[:8]}", last_name=role.title(), role="user", active=True)
        session.add(person)
        session.flush()
        if member:
            session.add(ProjectPersonLink(project_id=c.production_id, person_id=person.id, role=role))
        if assigned:
            session.add(TaskPersonLink(task_id=c.task_id, person_id=person.id))
        return person.id


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


def _register(c, actor_id, count=2, *, complete=True):
    from plugin.packages.service import reserve_output_package
    from plugin.packages.worker import make_output_package_handler

    files = []
    for index in range(count):
        content = f"frame-{uuid4()}".encode()
        object_id = f"src-{uuid4().hex}"
        c.storage.upload(io.BytesIO(content), object_id, len(content), sha256(content).hexdigest(), "source-folder",
                         file_name=f"plate.{1001 + index}.exr")
        files.append({"source_provider_object_id": object_id, "file_name": f"plate.{1001 + index}.exr",
                      "byte_count": len(content), "sha256": sha256(content).hexdigest(), "frame_number": 1001 + index,
                      "file_format": "exr", "color_space": "ACEScg", "is_proxy": False})
    reservation = reserve_output_package(
        c.db, task_id=c.task_id, production_id=c.production_id, entity_id=c.entity_id,
        output_type_id=c.output_type_id, package_type=c.package_type, actor_id=actor_id,
        idempotency_key=f"k-{uuid4()}", files=files, storage_connection_id=c.connection_id,
        frame_start=1001, frame_end=1000 + count, color_space="ACEScg",
    )
    if complete:
        assert make_output_package_handler(c.db, lambda _c: c.storage)(_lease(c, reservation.job_id)).state == "completed"
    return reservation


def _package_ids(c, package_id):
    from sqlalchemy import select
    from plugin.models import OutputPackage, OutputPackageFile

    with c.db.begin() as session:
        package = session.get(OutputPackage, UUID(str(package_id)))
        rows = session.scalars(select(OutputPackageFile).where(
            OutputPackageFile.package_id == package.id).order_by(OutputPackageFile.ordinal)).all()
        return package.provider_folder_id, [(row.id, row.provider_object_id) for row in rows]


def _version_row(client, c, package_id):
    return next(item for item in client.get(f"{BASE}/tasks/{c.task_id}/output-packages").json if item["id"] == str(package_id))


def _real_drive(c):
    c.monkeypatch.setitem(c.app.config, "VFX_FILES_STORAGE_MODE", "google_drive")


def test_real_drive_mode_gives_plain_drive_links_and_no_byte_download(ctx):
    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    package = _register(c, artist_id)
    folder_id, files = _package_ids(c, package.package_id)
    _real_drive(c)

    row = _version_row(artist, c, package.package_id)
    assert row["folder_drive_url"] == f"https://drive.google.com/drive/folders/{folder_id}"
    page = artist.get(f"{BASE}/output-packages/{package.package_id}/files").json
    assert page["folder_drive_url"] == row["folder_drive_url"]
    assert [item["drive_url"] for item in page["files"]] == [f"https://drive.google.com/file/d/{pid}/view" for _fid, pid in files]
    assert all(item["download_url"] is None for item in page["files"])

    # The Plugin never serves the bytes for real Drive, even to the registrant or an admin, and never touches Drive.
    class NoRead(type(c.storage)):
        def download_to(self, *args, **kwargs):
            raise AssertionError("bytes must not be proxied")

    clone = NoRead.__new__(NoRead)
    clone.__dict__ = c.storage.__dict__
    c.monkeypatch.setitem(c.app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: clone)
    before = len(c.storage.calls)
    for client in (artist, c.admin_client):
        response = client.get(f"{BASE}/output-package-files/{files[0][0]}/download")
        assert response.status_code == 403 and b"Drive" in response.data
    assert len(c.storage.calls) == before


def test_fake_storage_keeps_the_byte_download_and_creates_no_drive_links(ctx):
    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    package = _register(c, artist_id, count=1)
    _folder_id, files = _package_ids(c, package.package_id)

    row = _version_row(artist, c, package.package_id)
    assert row["folder_drive_url"] is None
    page = artist.get(f"{BASE}/output-packages/{package.package_id}/files").json
    assert page["files"][0]["drive_url"] is None
    assert page["files"][0]["download_url"].endswith(f"/output-package-files/{files[0][0]}/download")
    assert artist.get(f"{BASE}/output-package-files/{files[0][0]}/download").status_code == 200


def test_unauthorized_users_get_neither_links_nor_bytes(ctx):
    from zou.app.models.person import Person
    from zou.app.models.project import Project, ProjectPersonLink

    c = ctx
    owner_id = _person(c)
    package = _register(c, owner_id, count=1)
    _folder_id, files = _package_ids(c, package.package_id)
    _real_drive(c)
    outsider = _user_client(c.app, _person(c, member=False, assigned=False))
    unassigned = _user_client(c.app, _person(c, assigned=False))
    with c.app.app_context(), c.db.begin() as session:
        foreign = Project(name=f"Foreign {uuid4()}")
        person = Person(first_name=f"Fx{uuid4().hex[:8]}", last_name="Manager", role="user", active=True)
        session.add_all([foreign, person])
        session.flush()
        session.add(ProjectPersonLink(project_id=foreign.id, person_id=person.id, role="manager"))
        foreign_id = person.id
    foreign_manager = _user_client(c.app, foreign_id)
    for client in (outsider, unassigned, foreign_manager):
        for url in (f"{BASE}/tasks/{c.task_id}/output-packages",
                    f"{BASE}/output-packages/{package.package_id}/files",
                    f"{BASE}/output-package-files/{files[0][0]}/download"):
            response = client.get(url)
            assert response.status_code == 403 and b"drive.google.com" not in response.data


def test_deleting_deleted_and_unfinished_versions_have_no_drive_link(ctx):
    from plugin.packages.deletion import make_output_package_deletion_handler

    c = ctx
    artist_id = _person(c)
    artist = _user_client(c.app, artist_id)
    unfinished = _register(c, artist_id, count=1, complete=False)
    package = _register(c, artist_id, count=1)
    _real_drive(c)
    folder_id, files = _package_ids(c, package.package_id)

    assert _version_row(artist, c, package.package_id)["folder_drive_url"]
    unfinished_row = _version_row(artist, c, unfinished.package_id)
    assert unfinished_row["folder_drive_url"] is None
    assert artist.get(f"{BASE}/output-packages/{unfinished.package_id}/files").json["files"][0]["drive_url"] is None

    digest = artist.get(f"{BASE}/output-packages/{package.package_id}/deletion").json["impact_sha256"]
    response = artist.post(f"{BASE}/output-packages/{package.package_id}/deletion",
                           json={"confirm": True, "impact_sha256": digest}, headers=_headers(artist))
    assert response.status_code == 202

    def assert_linkless():
        row = _version_row(artist, c, package.package_id)
        assert row["deleted"] and row["folder_drive_url"] is None
        page = artist.get(f"{BASE}/output-packages/{package.package_id}/files").json
        assert page["folder_drive_url"] is None and page["files"][0]["drive_url"] is None
        assert artist.get(f"{BASE}/output-package-files/{files[0][0]}/download").status_code == 410

    assert_linkless()  # deletion requested / in progress
    c.monkeypatch.setitem(c.app.config, "VFX_FILES_STORAGE_MODE", "fake")
    handler = make_output_package_deletion_handler(c.db, lambda _c: c.storage)
    assert handler(_lease(c, UUID(response.json["job_id"]))).state == "completed"
    _real_drive(c)
    assert_linkless()  # deleted
    # History and the version stay visible.
    assert _version_row(artist, c, package.package_id)["version"] >= 1


def test_drive_url_is_built_only_from_a_well_formed_provider_id():
    from plugin.resources import _DRIVE_ID, _drive_view_url

    assert _DRIVE_ID.pattern == r"^[A-Za-z0-9_-]{10,200}$"
    # Not real-Drive mode in a bare unit context -> never a URL (needs an app context for _setting).
    from flask import Flask

    app = Flask("unit")
    app.config["VFX_FILES_STORAGE_MODE"] = "google_drive"
    with app.app_context():
        assert _drive_view_url("1AbCdEfGhIjKlMnOpQrStUv_-xyz") == "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv_-xyz/view"
        assert _drive_view_url("1AbCdEfGhIjKlMnOpQrStUv_-xyz", folder=True) == "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv_-xyz"
        for bad in (None, "", "short", "has/slash/in/it/1234567", "id?usp=sharing12345", "a b c d e f g h i j", "x" * 201,
                    "../../1234567890", "javascript:alert(1)//", 12345678901234):
            assert _drive_view_url(bad) is None and _drive_view_url(bad, folder=True) is None
    app.config["VFX_FILES_STORAGE_MODE"] = "fake"
    with app.app_context():
        assert _drive_view_url("1AbCdEfGhIjKlMnOpQrStUv_-xyz") is None


def test_links_are_navigation_only_no_permission_or_public_link_is_ever_created():
    forbidden = ("/permissions", "anyoneWithLink", "'anyone'", '"anyone"', "allowFileDiscovery", "webViewLink")
    for path in (PLUGIN_ROOT / "storage").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(word in text for word in forbidden), f"{path.name} must not manage Drive sharing"
    assert "/permissions" not in (PLUGIN_ROOT / "resources.py").read_text(encoding="utf-8")
