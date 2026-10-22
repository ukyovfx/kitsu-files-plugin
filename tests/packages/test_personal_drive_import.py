"""M4: import files from a user's personal Google Drive into an Output version. Emulated Google + FakeStorage only."""

import hashlib
import io
import threading
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

BASE = "/plugins/vfx_files"
ORIGIN = "https://kitsu.test"
SCOPE = "openid https://www.googleapis.com/auth/drive.file"
FOLDER = "application/vnd.google-apps.folder"


class _R:
    def __init__(self, status_code, body=None, content=b""):
        self.status_code, self._body, self.content, self.headers = status_code, body or {}, content, {}

    def json(self):
        return self._body

    def iter_content(self, chunk_size):
        yield self.content


class GoogleAndDriveEmulator:
    """Google OAuth endpoints plus a read-only view of one personal Drive. Records every request method."""

    def __init__(self):
        self.subject = f"google-sub-{uuid4().hex}"
        self.refresh_token = f"rt-{uuid4().hex}"
        self.files = {}  # id -> dict(name, mime, data, trashed, hidden)
        self.drive_requests = []
        self.fail = {}  # file id -> "timeout" | "gone" | "corrupt"
        self.refresh_status = 200
        self.tokens = 0

    def add(self, name, data=b"", mime="image/x-exr", **extra):
        file_id = f"1{uuid4().hex}"
        self.files[file_id] = {"name": name, "mime": mime, "data": data, "trashed": False, **extra}
        return file_id

    def request(self, method, url, **kwargs):
        if url.endswith("/token"):
            grant = kwargs["data"]["grant_type"]
            if grant == "refresh_token" and self.refresh_status != 200:
                return _R(self.refresh_status, {"error": "invalid_grant"})
            self.tokens += 1
            body = {"access_token": f"at-{self.tokens}", "expires_in": 3600}
            if grant == "authorization_code":
                body.update(refresh_token=self.refresh_token, scope=SCOPE)
            return _R(200, body)
        if url.endswith("/userinfo"):
            return _R(200, {"sub": self.subject})
        if url.endswith("/revoke"):
            return _R(200)
        if "/drive/v3/files/" in url:
            self.drive_requests.append((method, url))
            file_id = url.rsplit("/", 1)[1]
            entry = self.files.get(file_id)
            if entry is None or entry.get("hidden") or self.fail.get(file_id) == "gone":
                return _R(404)
            if self.fail.get(file_id) == "timeout":
                return _R(503)
            if kwargs.get("params", {}).get("alt") == "media":
                data = entry["data"]
                if self.fail.get(file_id) == "corrupt":
                    data = data[:-1] + b"X"
                return _R(200, content=data)
            return _R(200, {"id": file_id, "name": entry["name"], "mimeType": entry["mime"], "trashed": entry["trashed"],
                            "size": str(len(entry["data"])) if not entry["mime"].startswith("application/vnd.google-apps.") else None,
                            "md5Checksum": hashlib.md5(entry["data"]).hexdigest(),
                            "parents": entry.get("parents", []), "capabilities": entry.get("capabilities", {})})
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture
def imp(authenticated_plugin_client, synthetic_upload_task, zou_db_factory, monkeypatch):
    from sqlalchemy import text
    from plugin.integrations.oauth import CredentialKeyring
    from plugin.models import KitsuSyncDesiredConfiguration
    from plugin.storage.fake import FakeStorage
    from zou.app.models.output_type import OutputType

    class Ctx:
        pass

    c = Ctx()
    app = authenticated_plugin_client.vfx_app
    production_id, _et, _tt, entity_id, task_id, connection_id, admin_id = synthetic_upload_task
    c.app, c.db, c.admin_client, c.monkeypatch = app, zou_db_factory, authenticated_plugin_client, monkeypatch
    c.user_ids = []
    c.production_id, c.entity_id, c.task_id, c.connection_id = production_id, entity_id, task_id, connection_id
    c.admin_id = admin_id
    c.google, c.storage = GoogleAndDriveEmulator(), FakeStorage(root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    for key, value in {
        "VFX_FILES_GOOGLE_CLIENT_ID": "synthetic-client-id", "VFX_FILES_GOOGLE_CLIENT_SECRET": "synthetic-client-secret",
        "VFX_FILES_GOOGLE_OAUTH_REDIRECT_URI": "https://files.example.invalid/api/plugins/vfx_files/oauth/google/callback",
        "VFX_FILES_KITSU_PLUGIN_RETURN_URI": "https://kitsu.example.invalid/api/plugins/vfx_files/frontend/",
        "VFX_FILES_GOOGLE_PICKER_API_KEY": "synthetic-browser-api-key", "VFX_FILES_GOOGLE_CLOUD_PROJECT_NUMBER": "123456789012",
    }.items():
        monkeypatch.setitem(app.config, key, value)
    monkeypatch.setitem(app.extensions, "vfx_files_credential_keyring", CredentialKeyring("m4-key", {"m4-key": b"m" * 32}))
    monkeypatch.setitem(app.extensions, "vfx_files_oauth_transport", c.google)
    monkeypatch.setitem(app.extensions, "vfx_files_storage_adapter_factory", lambda _connection: c.storage)
    with app.app_context(), zou_db_factory.begin() as session:
        output_type = OutputType(name=f"VFXImp{uuid4().hex[:10]}", short_name=f"imp{uuid4().hex[:8]}")
        session.add(output_type)
        session.flush()
        c.output_type_id = output_type.id
        desired = session.get(KitsuSyncDesiredConfiguration, production_id)
        selected = dict(desired.desired_config)
        selected["activation"] = {"enabled": True, "activated_at": "2026-10-13T00:00:00Z", "notification_epoch": 1}
        desired.notification_epoch = 1
        desired.desired_config = selected
    yield c
    with zou_db_factory.begin() as session:
        session.execute(text("UPDATE plugin_vfx_files_job SET state='completed', lease_owner=NULL, lease_expires_at=NULL "
                             "WHERE payload->>'task_id' = :t AND state IN ('queued','retry','leased')"), {"t": str(task_id)})
        for actor_id in c.user_ids:
            session.execute(text("UPDATE plugin_vfx_files_job SET state='completed', lease_owner=NULL, lease_expires_at=NULL "
                                 "WHERE job_type='revoke_personal_google' AND payload->>'actor_id' = :a "
                                 "AND state IN ('queued','retry','leased')"), {"a": actor_id})
            session.execute(text("DELETE FROM plugin_vfx_files_google_user_authorization_intent WHERE actor_id = :a"), {"a": actor_id})
            session.execute(text("DELETE FROM plugin_vfx_files_google_user_credential WHERE actor_id = :a"), {"a": actor_id})
        session.execute(text("DELETE FROM plugin_vfx_files_production_output_setting WHERE production_id = :p"), {"p": str(production_id)})


def _user(c, *, assigned=True, member=True, role="user"):
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import TaskPersonLink
    from zou.app.services.persons_service import create_access_token_for_raw_person

    with c.app.app_context(), c.db.begin() as session:
        person = Person(first_name=f"Imp{uuid4().hex[:8]}", last_name="Artist", role="user", active=True)
        session.add(person)
        session.flush()
        if member:
            session.add(ProjectPersonLink(project_id=c.production_id, person_id=person.id, role=role))
        if assigned:
            session.add(TaskPersonLink(task_id=c.task_id, person_id=person.id))
        person_id = person.id
        c.user_ids.append(str(person_id))
    with c.app.app_context():
        token = create_access_token_for_raw_person(Person.query.filter_by(id=person_id).one())
    client = c.app.test_client()
    client.set_cookie(c.app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), token)
    return person_id, client


def _hdr(client):
    return {"Origin": ORIGIN, "X-VFX-Files-CSRF": client.get(f"{BASE}/csrf-token").json["csrf_token"]}


def _link(c, client):
    started = client.post(f"{BASE}/productions/{c.production_id}/personal-google/authorizations", json={},
                          headers={**_hdr(client), "Idempotency-Key": f"personal-google:{uuid4()}"})
    assert started.status_code == 201
    state = parse_qs(urlsplit(started.json["authorization_url"]).query)["state"][0]
    done = client.get(f"{BASE}/oauth/google/callback", query_string={"state": state, "code": "synthetic-code"})
    assert "personal_google=connected" in done.headers["Location"]


def _register(c, client, files, *, package_type="final-exr", key=None, **extra):
    body = {"output_type_id": str(c.output_type_id), "package_type": package_type, "files": files, **extra}
    return client.post(f"{BASE}/tasks/{c.task_id}/personal-drive/output-packages", json=body,
                       headers={**_hdr(client), "Idempotency-Key": key or f"personal-import:{uuid4()}"})


def _lease(c, job_id):
    from sqlalchemy import func, select
    from plugin.jobs.queue import LeasedJob
    from plugin.models import Job

    worker_id = uuid4()
    with c.db.begin() as session:
        job = session.get(Job, UUID(str(job_id)))
        job.state, job.lease_owner = "leased", worker_id
        job.lease_generation += 1
        job.attempts += 1
        job.lease_expires_at = session.scalar(select(func.now())) + timedelta(minutes=2)
        return LeasedJob(job.id, job.job_type, dict(job.payload), worker_id, job.lease_generation, job.attempts)


def _worker(c, storage=None):
    from plugin.integrations.personal_google import open_personal_source
    from plugin.packages.worker import make_output_package_handler

    def personal(actor_id, production_id):
        return open_personal_source(c.db, actor_id=actor_id, production_id=production_id, settings=c.app.config,
                                    extensions=c.app.extensions, transport=c.google)

    return make_output_package_handler(c.db, lambda _c: storage or c.storage, personal)


def _package(c, package_id):
    from sqlalchemy import select
    from plugin.models import OutputPackage, OutputPackageFile

    with c.db.begin() as session:
        package = session.get(OutputPackage, UUID(str(package_id)))
        rows = session.scalars(select(OutputPackageFile).where(OutputPackageFile.package_id == package.id)
                               .order_by(OutputPackageFile.ordinal)).all()
        return (package.state, package.qc_state, package.source_kind, package.qc_report, package.provider_folder_id,
                [(r.file_name, r.provider_object_id, r.sha256, r.byte_count, r.frame_number) for r in rows])


def _entry(file_id, **extra):
    return {"drive_file_id": file_id, **extra}


def test_single_file_import_copies_bytes_to_production_and_never_writes_the_original(imp):
    c = imp
    user_id, user = _user(c)
    _link(c, user)
    data = b"personal-exr-frame-bytes"
    original = c.google.add("plate.1001.exr", data)
    before = dict(c.google.files[original])

    response = _register(c, user, [_entry(original, file_format="exr")], package_type="tracking-data")
    assert response.status_code == 202, response.get_data(as_text=True)
    assert _package(c, response.json["id"])[:3] == ("verifying", "pending", "personal")
    assert _worker(c)(_lease(c, response.json["job_id"])).state == "completed"

    state, qc, kind, report, folder_id, files = _package(c, response.json["id"])
    assert (state, qc, kind) == ("completed", "passed", "personal")
    name, target_id, digest, size, _frame = files[0]
    assert digest == hashlib.sha256(data).hexdigest() and size == len(data) and name == "plate.1001.exr"
    copied = c.storage.get(target_id)
    assert copied.parent_id == folder_id and c.storage.verify(target_id, len(data), digest)
    assert target_id != original and original not in c.storage.objects  # the original lives only in the personal Drive
    # Original protection: only GETs, and the personal file is byte-for-byte unchanged.
    assert {method for method, _url in c.google.drive_requests} == {"GET"}
    assert c.google.files[original] == before
    listed = user.get(f"{BASE}/tasks/{c.task_id}/output-packages").json[0]
    assert listed["state"] == "completed" and listed["source_kind"] == "personal" and listed["version"] == 1
    assert user.get(f"{BASE}/output-package-files/{_file_id(c, response.json['id'])}/download").status_code == 200


def _file_id(c, package_id):
    from sqlalchemy import select
    from plugin.models import OutputPackageFile

    with c.db.begin() as session:
        return session.scalar(select(OutputPackageFile.id).where(OutputPackageFile.package_id == UUID(str(package_id))))


def test_exr_sequence_reports_missing_frames_and_rejects_bad_frame_metadata(imp):
    c = imp
    _uid, user = _user(c)
    _link(c, user)
    ids = {frame: c.google.add(f"shot.{frame}.exr", f"frame-{frame}".encode()) for frame in (1001, 1002, 1004, 1005)}
    entries = [_entry(file_id, frame_number=frame) for frame, file_id in ids.items()]

    ok = _register(c, user, entries, frame_start=1001, frame_end=1005, color_space="ACEScg")
    assert ok.status_code == 202
    assert _worker(c)(_lease(c, ok.json["job_id"])).state == "completed"
    state, qc, _kind, report, _folder, files = _package(c, ok.json["id"])
    assert (state, qc) == ("completed", "failed") and report["missing_frames"] == [1003]  # gaps are surfaced, not hidden
    assert [f[4] for f in files] == [1001, 1002, 1004, 1005]

    assert _register(c, user, [_entry(ids[1001], frame_number=1001), _entry(ids[1002], frame_number=1001)],
                     frame_start=1001, frame_end=1002).status_code == 400  # duplicate frame
    assert _register(c, user, [_entry(ids[1001], frame_number=9999)], frame_start=1001, frame_end=1002).status_code == 400
    assert _register(c, user, [_entry(ids[1001], frame_number=1001), _entry(ids[1002])], frame_start=1001, frame_end=1002).status_code == 400
    assert _register(c, user, [_entry(ids[1001], frame_number=1001)]).status_code == 400  # frames without a range


def test_selection_is_reverified_with_the_users_own_grant(imp):
    c = imp
    _uid, user = _user(c)
    _link(c, user)
    good = c.google.add("a.exr", b"abc")
    folder = c.google.add("dir", mime=FOLDER)
    doc = c.google.add("notes", mime="application/vnd.google-apps.document")
    invisible = c.google.add("secret.exr", b"zzz", hidden=True)  # exists, but this grant cannot see it (not picked / other account)
    slash = c.google.add("a/b.exr", b"abc")
    twin_a, twin_b = c.google.add("Same.exr", b"1"), c.google.add("same.EXR", b"2")

    def code(entries, **extra):
        response = _register(c, user, entries, package_type="tracking-data", **extra)
        return response.status_code, response.get_data(as_text=True)

    assert code([_entry(folder)])[0] == 400 and code([_entry(doc)])[0] == 400
    assert code([_entry(invisible)])[0] == 404 and code([_entry("1" + "x" * 30)])[0] == 404  # forged/unknown IDs
    assert code([_entry("not a drive id")])[0] == 400 and code([_entry("../../etc")])[0] == 400
    assert code([_entry(slash)])[0] == 400  # path separators in names are refused
    assert code([_entry(twin_a), _entry(twin_b)])[0] == 409  # case-insensitive duplicate names
    assert code([_entry(good), _entry(good)])[0] == 400  # same file twice
    assert code([])[0] == 400 and code([5])[0] == 400
    assert code([_entry(c.google.add("big.exr", b"x" * 2048))])[0] == 413  # per-file cap (fixture cap is 1024)
    c.monkeypatch.setitem(c.app.config, "VFX_FILES_PERSONAL_IMPORT_MAX_FILES", "1")
    assert code([_entry(good), _entry(c.google.add("b.exr", b"b"))])[0] == 413


def test_access_control_policy_and_boundaries(imp):
    from plugin.integrations.user_oauth import set_personal_google_policy
    from zou.app.models.person import Person
    from zou.app.models.project import Project, ProjectPersonLink

    c = imp
    owner_id, owner = _user(c)
    source = c.google.add("a.exr", b"abc")
    # Not linked yet.
    unlinked = _register(c, owner, [_entry(source)], package_type="tracking-data")
    assert unlinked.status_code == 403 and b"personal_google_not_linked" in unlinked.data
    _link(c, owner)
    _o, outsider = _user(c, member=False, assigned=False)
    _u, unassigned = _user(c, assigned=False)
    for client in (outsider, unassigned):
        assert _register(c, client, [_entry(source)], package_type="tracking-data").status_code == 403
    with c.app.app_context(), c.db.begin() as session:
        foreign = Project(name=f"Foreign {uuid4()}")
        person = Person(first_name=f"Fx{uuid4().hex[:8]}", last_name="Manager", role="user", active=True)
        session.add_all([foreign, person])
        session.flush()
        session.add(ProjectPersonLink(project_id=foreign.id, person_id=person.id, role="manager"))
        foreign_id = person.id
    from zou.app.services.persons_service import create_access_token_for_raw_person
    with c.app.app_context():
        token = create_access_token_for_raw_person(Person.query.filter_by(id=foreign_id).one())
    foreign_client = c.app.test_client()
    foreign_client.set_cookie(c.app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), token)
    assert _register(c, foreign_client, [_entry(source)], package_type="tracking-data").status_code == 403
    body = {"output_type_id": str(c.output_type_id), "package_type": "tracking-data", "files": [_entry(source)]}
    assert owner.post(f"{BASE}/tasks/{c.task_id}/personal-drive/output-packages", json=body,
                      headers={"Idempotency-Key": "k"}).status_code == 403  # cookie-only, no Origin/CSRF
    # A grant belongs to its owner: another member cannot import with it.
    other_id, other = _user(c)
    assert _register(c, other, [_entry(source)], package_type="tracking-data").status_code == 403
    # Admin blocks personal Google: the owner can no longer register.
    with c.db.begin() as session:
        set_personal_google_policy(session, c.production_id, "blocked", uuid4())
    blocked = _register(c, owner, [_entry(source)], package_type="tracking-data")
    assert blocked.status_code == 403 and b"personal_google_blocked" in blocked.data
    assert not c.google.drive_requests or {m for m, _ in c.google.drive_requests} == {"GET"}


def test_worker_failures_are_safe_and_retry_without_duplicates(imp):
    from plugin.integrations.user_oauth import set_personal_google_policy
    from sqlalchemy import select
    from plugin.models import GoogleUserCredential

    c = imp
    user_id, user = _user(c)
    _link(c, user)
    data = b"payload-for-retry"
    source = c.google.add("clip.mov", data)
    response = _register(c, user, [_entry(source)], package_type="low-res-video")
    job_id, package_id = response.json["job_id"], response.json["id"]
    handler = _worker(c)

    # 1. Transient provider error -> retry; nothing completed.
    from plugin.storage.base import RetryableStorageFailure

    c.google.fail[source] = "timeout"
    with pytest.raises(RetryableStorageFailure):  # the worker loop turns this into a delayed retry
        handler(_lease(c, job_id))
    assert _package(c, package_id)[0] == "verifying"
    # 2. Truncated/corrupt transfer -> detected by size/MD5, not completed, never marked verified.
    c.google.fail[source] = "corrupt"
    result = handler(_lease(c, job_id))
    assert result.state == "reconcile_required" and result.error_code == "output_package_source_readback_failed"
    assert _package(c, package_id)[0] == "verifying"  # only the job reports reconcile_required; the package is never completed
    # 3. Grant revoked at Google meanwhile -> blocked (no guessing), recoverable after relinking.
    c.google.fail.clear()
    c.google.refresh_status = 400
    from plugin.integrations.user_oauth import _credential_aad

    keyring = c.app.extensions["vfx_files_credential_keyring"]
    with c.db.begin() as session:  # expire the cached access token so the next use must refresh at Google
        row = session.get(GoogleUserCredential, user_id)
        secret = keyring.decrypt(row.key_id, row.nonce, row.ciphertext, _credential_aad(user_id, row.google_subject))
        secret["access_token_expires_at"] = "2000-01-01T00:00:00+00:00"
        row.key_id, row.nonce, row.ciphertext = keyring.encrypt(secret, _credential_aad(user_id, row.google_subject))
    revoked = handler(_lease(c, job_id))
    assert (revoked.state, revoked.error_code) == ("blocked_authorization", "personal_source_permission_denied")
    assert _package(c, package_id)[0] != "completed"
    # 4. Block policy -> blocked with a clear code.
    with c.db.begin() as session:
        session.get(GoogleUserCredential, user_id).state = "active"
        set_personal_google_policy(session, c.production_id, "blocked", uuid4())
    result = handler(_lease(c, job_id))
    assert result.state == "blocked_authorization" and result.error_code == "personal_source_personal_google_blocked"
    # 5. Everything restored -> the same package completes once; replays are idempotent.
    c.google.refresh_status = 200
    with c.db.begin() as session:
        set_personal_google_policy(session, c.production_id, "allowed", uuid4())
    assert handler(_lease(c, job_id)).state == "completed"
    assert handler(_lease(c, job_id)).state == "completed"
    state, qc, _kind, _report, folder_id, files = _package(c, package_id)
    assert state == "completed" and qc == "passed"
    children = [obj for obj in c.storage.objects.values() if obj.parent_id == folder_id]
    assert len(children) == 1 and children[0].object_id == files[0][1]  # exactly one copy despite the retries
    assert {method for method, _url in c.google.drive_requests} == {"GET"}


def test_source_vanishing_after_registration_is_reconciled_not_copied(imp):
    c = imp
    _uid, user = _user(c)
    _link(c, user)
    source = c.google.add("gone.exr", b"abc")
    response = _register(c, user, [_entry(source)], package_type="tracking-data")
    c.google.files[source]["data"] = b"abcd"  # the original changed size after it was registered
    result = _worker(c)(_lease(c, response.json["job_id"]))
    assert (result.state, result.error_code) == ("reconcile_required", "output_package_source_unavailable")
    c.google.files[source]["hidden"] = True  # no longer visible to this grant: needs a human (relink/re-pick), never guessed
    hidden = _worker(c)(_lease(c, response.json["job_id"]))
    assert (hidden.state, hidden.error_code) == ("blocked_authorization", "personal_source_permission_denied")
    assert _package(c, response.json["id"])[0] == "verifying"


def test_stuck_copy_can_be_retried_by_the_registrant_or_an_admin_only(imp):
    from sqlalchemy import select
    from plugin.models import Job

    c = imp
    _uid, user = _user(c)
    _link(c, user)
    source = c.google.add("a.exr", b"abc")
    response = _register(c, user, [_entry(source)], package_type="tracking-data")
    with c.db.begin() as session:
        job = session.get(Job, UUID(response.json["job_id"]))
        job.state, job.attempts, job.last_error_code = "failed", 5, "provider_permission_denied"
    row = user.get(f"{BASE}/tasks/{c.task_id}/output-packages").json[0]
    assert row["stuck"] is True and row["retry_available"] is True and row["job_error_code"] == "provider_permission_denied"
    _other_id, other = _user(c)
    assert other.post(f"{BASE}/output-packages/{response.json['id']}/retry", json={}, headers=_hdr(other)).status_code == 403
    assert user.post(f"{BASE}/output-packages/{response.json['id']}/retry", json={}).status_code == 403  # CSRF required
    retried = user.post(f"{BASE}/output-packages/{response.json['id']}/retry", json={}, headers=_hdr(user))
    assert retried.status_code == 202 and retried.json["requeued"] is True
    with c.db.begin() as session:
        assert session.get(Job, UUID(response.json["job_id"])).state == "queued"
    assert _worker(c)(_lease(c, response.json["job_id"])).state == "completed"
    assert user.post(f"{BASE}/output-packages/{response.json['id']}/retry", json={}, headers=_hdr(user)).status_code == 409
    assert c.admin_client.post(f"{BASE}/output-packages/{response.json['id']}/retry", json={}, headers=_hdr(c.admin_client)).status_code == 409


def test_idempotent_registration_and_version_numbers(imp):
    c = imp
    _uid, user = _user(c)
    _link(c, user)
    source = c.google.add("a.exr", b"abc")
    key = f"personal-import:{uuid4()}"
    first = _register(c, user, [_entry(source)], package_type="tracking-data", key=key)
    again = _register(c, user, [_entry(source)], package_type="tracking-data", key=key)
    assert (first.status_code, again.status_code) == (202, 202) and first.json["id"] == again.json["id"]
    assert _register(c, user, [_entry(c.google.add("b.exr", b"b"))], package_type="tracking-data", key=key).status_code == 409
    second = _register(c, user, [_entry(source)], package_type="tracking-data")
    assert second.json["version"] == 2


def test_picker_config_has_public_ids_only_and_requires_a_linked_allowed_member(imp):
    from plugin.integrations.user_oauth import set_personal_google_policy

    c = imp
    _uid, user = _user(c)
    url = f"{BASE}/productions/{c.production_id}/personal-google/picker-config"
    assert user.get(url).status_code == 409  # not linked yet
    _link(c, user)
    config = user.get(url)
    assert config.status_code == 200
    assert config.json["client_id"] == "synthetic-client-id" and config.json["app_id"] == "123456789012"
    assert config.json["api_key"] == "synthetic-browser-api-key" and config.json["scope"] == SCOPE
    assert config.json["subject_sha256"] == hashlib.sha256(c.google.subject.encode()).hexdigest()
    text = config.get_data(as_text=True)
    for secret in (c.google.refresh_token, "synthetic-client-secret", "at-1", c.google.subject):
        assert secret not in text
    _o, outsider = _user(c, member=False, assigned=False)
    assert outsider.get(url).status_code == 403
    c.monkeypatch.setitem(c.app.config, "VFX_FILES_GOOGLE_CLOUD_PROJECT_NUMBER", "")
    assert user.get(url).status_code == 503
    c.monkeypatch.setitem(c.app.config, "VFX_FILES_GOOGLE_CLOUD_PROJECT_NUMBER", "123456789012")
    with c.db.begin() as session:
        set_personal_google_policy(session, c.production_id, "blocked", uuid4())
    assert user.get(url).status_code == 403


def test_policy_decision_is_serialized_even_before_any_settings_row_exists(imp):
    """M4-0: first-ever policy change vs. the grant-saving transaction (no row to lock yet)."""
    from sqlalchemy import select
    from plugin.integrations.user_oauth import _policy_lock, get_personal_google_policy, set_personal_google_policy
    from plugin.models import GoogleUserCredential, ProductionOutputSetting

    c = imp
    with c.db.begin() as session:
        session.execute(ProductionOutputSetting.__table__.delete().where(ProductionOutputSetting.production_id == c.production_id))

    # (a) A policy writer must wait while the saving transaction holds the lock...
    holder = c.db()
    holder.begin()
    _policy_lock(holder, c.production_id)
    assert get_personal_google_policy(holder, c.production_id, lock=True) == "allowed"  # absent row -> default allowed
    done = threading.Event()

    def writer():
        with c.db.begin() as session:
            set_personal_google_policy(session, c.production_id, "blocked", uuid4())
        done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    time.sleep(0.6)
    assert not done.is_set()  # blocked behind the decision, not interleaved with it
    holder.commit()
    thread.join(5)
    assert done.is_set()
    with c.db.begin() as session:
        assert get_personal_google_policy(session, c.production_id) == "blocked"

    # (b) ...and a completion that starts after the admin's uncommitted block waits, then is refused.
    with c.db.begin() as session:
        session.execute(ProductionOutputSetting.__table__.delete().where(ProductionOutputSetting.production_id == c.production_id))
    user_id, user = _user(c)
    started = user.post(f"{BASE}/productions/{c.production_id}/personal-google/authorizations", json={},
                        headers={**_hdr(user), "Idempotency-Key": f"personal-google:{uuid4()}"})
    state = parse_qs(urlsplit(started.json["authorization_url"]).query)["state"][0]
    admin_tx = c.db()
    admin_tx.begin()
    set_personal_google_policy(admin_tx, c.production_id, "blocked", uuid4())  # holds the lock, not yet committed
    outcome = {}

    def finish():
        outcome["response"] = user.get(f"{BASE}/oauth/google/callback", query_string={"state": state, "code": "synthetic-code"})

    thread = threading.Thread(target=finish)
    thread.start()
    time.sleep(0.8)
    assert "response" not in outcome  # waiting for the admin's transaction
    admin_tx.commit()
    thread.join(10)
    assert "error=personal_google_blocked" in outcome["response"].headers["Location"]
    with c.db.begin() as session:
        assert session.get(GoogleUserCredential, user_id) is None


def test_restart_after_target_reservation_completes_for_production_sources_too(imp):
    """Regression: a retry after a target ID was reserved must not be treated as a manifest mismatch."""
    from plugin.packages.service import reserve_output_package
    from plugin.packages.worker import make_output_package_handler
    from plugin.storage.fake import FakeStorage

    c = imp
    c.storage.create_folder(c.storage.root_id, "inputs", {"display_name": "Inputs"})
    content = b"production-source-bytes"
    digest = hashlib.sha256(content).hexdigest()
    c.storage.upload(io.BytesIO(content), "src-production-1", len(content), digest, "inputs", file_name="a.exr")
    reservation = reserve_output_package(
        c.db, task_id=c.task_id, production_id=c.production_id, entity_id=c.entity_id, output_type_id=c.output_type_id,
        package_type="tracking-data", actor_id=c.admin_id, idempotency_key=f"k-{uuid4()}",
        storage_connection_id=c.connection_id,
        files=[{"source_provider_object_id": "src-production-1", "file_name": "a.exr", "byte_count": len(content),
                "sha256": digest, "frame_number": None, "file_format": "exr", "color_space": None, "is_proxy": False}])

    class CrashOnce(FakeStorage):
        crashed = False

        def upload(self, *args, **kwargs):
            if not CrashOnce.crashed:
                CrashOnce.crashed = True
                raise RuntimeError("worker died mid-upload")
            return super().upload(*args, **kwargs)

    c.storage.__class__ = CrashOnce
    handler = make_output_package_handler(c.db, lambda _c: c.storage)
    with pytest.raises(RuntimeError):
        handler(_lease(c, reservation.job_id))
    assert _package(c, reservation.package_id)[0] == "verifying"
    assert handler(_lease(c, reservation.job_id)).state == "completed"  # was: reconcile_required (metadata mismatch)
    state, _qc, kind, _report, folder_id, files = _package(c, reservation.package_id)
    assert (state, kind) == ("completed", "production") and len([o for o in c.storage.objects.values() if o.parent_id == folder_id]) == 1


@pytest.mark.parametrize("hidden", [False, True])
def test_nested_personal_files_preserve_paths_targets_and_delete_compatibility(imp, hidden):
    from plugin.packages.deletion import _target_matches_path
    c=imp
    _uid,user=_user(c);_link(c,user)
    root=c.google.add('plates',mime=FOLDER,capabilities={'canListChildren':True})
    nested=c.google.add('beauty',mime=FOLDER,parents=[root],capabilities={'canListChildren':True})
    frame=c.google.add('shot.1001.exr',b'frame',parents=[nested])
    response=_register(c,user,[_entry(frame,root_folder_id=root)],package_type='tracking-data')
    assert response.status_code==202,response.get_data(as_text=True)
    job=_lease(c,response.json['job_id'])
    assert _worker(c)(job).state=='completed'
    state,qc,kind,report,folder,files=_package(c,response.json['id'])
    name,target,digest,size,_=files[0]
    assert name=='plates/beauty/shot.1001.exr'
    obj=c.storage.get(target)
    assert obj.name=='shot.1001.exr' and c.storage.get(obj.parent_id).name=='beauty'
    assert _target_matches_path(c.storage,obj,folder,name)
    assert not _target_matches_path(c.storage,obj,folder,'other/shot.1001.exr')
    assert _worker(c)(_lease(c,response.json['job_id'])).state=='completed'
    assert len([x for x in c.storage.objects.values() if x.name=='shot.1001.exr'])==1

    from plugin.models import OutputPackage
    from plugin.packages.deletion import build_delete_impact, impact_digest, request_package_deletion, make_output_package_deletion_handler
    from plugin.storage.base import AmbiguousOutcome
    with c.db.begin() as session:
        package=session.get(OutputPackage,UUID(response.json['id']))
        digest=impact_digest(build_delete_impact(session,package))
    deletion=request_package_deletion(c.db,package_id=UUID(response.json['id']),actor_id=c.admin_id,actor_role='admin',expected_impact_sha256=digest)
    handler=make_output_package_deletion_handler(c.db,lambda _connection:c.storage)
    if hidden:
        original_get=c.storage.get
        c.monkeypatch.setattr(c.storage,'get',lambda id:None if id==target else original_get(id))
        with pytest.raises(AmbiguousOutcome,match='lookup and parent listing disagree'):
            handler(_lease(c,deletion.job_id))
        assert target in c.storage.objects
    else:
        assert handler(_lease(c,deletion.job_id)).state=='completed'
        assert c.storage.get(target) is None and frame in c.google.files



def test_folder_preview_is_grant_scoped_and_registration_rechecks_ancestry(imp):
    c=imp;_uid,user=_user(c);_link(c,user)
    root=c.google.add('plates',mime=FOLDER,capabilities={'canListChildren':True})
    frame=c.google.add('a.exr',b'abc',parents=[root])
    original_request=c.google.request
    def request(method,url,**kwargs):
        if url.endswith('/drive/v3/files'):
            assert method=='GET' and kwargs['params']['q']==f"'{root}' in parents and trashed = false"
            return _R(200,{'files':[{'id':frame,'parents':[root]}]})
        return original_request(method,url,**kwargs)
    c.google.request=request
    preview=user.post(f'{BASE}/tasks/{c.task_id}/personal-drive/selection-preview',json={'files':[_entry(root)]},headers=_hdr(user))
    assert preview.status_code==200,preview.get_data(as_text=True)
    assert preview.json['scope_limited'] is True
    assert preview.json['files'][0]['name']=='plates/a.exr'
    c.google.files[frame]['parents']=[]
    assert _register(c,user,[_entry(frame,root_folder_id=root)],package_type='tracking-data').status_code==404
