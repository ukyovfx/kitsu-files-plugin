"""Create a disposable Production and three browser identities on the pinned host."""

import base64
import binascii
import json
import os
import time
from datetime import timedelta
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from zou.app import create_app, db
from zou.app.models.entity import Entity
from zou.app.models.entity_type import EntityType
from zou.app.models.person import Person
from zou.app.models.project import Project, ProjectPersonLink
from zou.app.models.file_status import FileStatus
from zou.app.models.output_type import OutputType
from zou.app.models.task import Task, TaskPersonLink
from zou.app.models.task_type import TaskType
from zou.app.services.persons_service import create_access_token_for_raw_person

from vfx_files.models import FolderBinding, OutputFamilyMapping, StorageConnection
from vfx_files.storage.fake import PersistentFakeStorage


def _validated_disposable_root_url(environ=None):
    env = os.environ if environ is None else environ
    if env.get("VFX_FILES_DISPOSABLE_TEST_MODE") != "1":
        raise RuntimeError("Refusing to seed without disposable test mode")

    raw_url = env.get("VFX_FILES_E2E_ROOT_URL")
    if not raw_url:
        raise RuntimeError("A disposable test root URL is required")

    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Invalid disposable test root URL") from exc

    if (
        parsed.scheme not in {"https", "http"}
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError("Disposable test root URL must be a credential-free origin")
    if port is not None and not 1 <= port <= 65535:
        raise RuntimeError("Invalid disposable test root URL port")

    hostname = hostname.casefold().rstrip(".")
    is_loopback = False
    try:
        is_loopback = ip_address(hostname).is_loopback
    except ValueError:
        pass
    is_reserved_test_name = hostname == "localhost" or hostname.endswith(
        (".localhost", ".test", ".example", ".invalid")
    )
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
        raise RuntimeError("HTTPS is required except for loopback test hosts")
    has_disposable_host_ack = (
        env.get("VFX_FILES_DISPOSABLE_HOST_ACK")
        == "DISPOSABLE_PINNED_KITSU_ZOU_ONLY"
    )
    if not (is_loopback or is_reserved_test_name) and not has_disposable_host_ack:
        raise RuntimeError("A non-reserved target requires disposable host acknowledgement")

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _token_has_short_expiry(token, now=None):
    current_time = time.time() if now is None else now
    parts = token.split(".")
    if len(parts) != 3:
        return False
    payload = parts[1].replace("-", "+").replace("_", "/")
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, UnicodeError, binascii.Error):
        return False
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    return (
        isinstance(expiry, (int, float))
        and not isinstance(expiry, bool)
        and current_time < expiry <= current_time + 901
    )


def _new_synthetic_manager(person_type, suffix):
    return person_type(
        first_name="Synthetic",
        last_name=f"Manager{suffix}",
        email=f"manager-{suffix}@example.invalid",
        role="manager",
    )


def main():
    root_url = _validated_disposable_root_url()
    fake_path = os.environ.get("VFX_FILES_FAKE_STORAGE_ROOT")
    if not fake_path:
        raise RuntimeError("FakeStorage path is required")
    app = create_app()
    app.config.update(
        VFX_FILES_ENABLED=True,
        VFX_FILES_WORKER_ENABLED=True,
        VFX_FILES_STORAGE_MODE="fake",
        VFX_FILES_STAGING_ROOT=os.environ.get("VFX_FILES_STAGING_ROOT"),
        VFX_FILES_FAKE_STORAGE_ROOT=fake_path,
        VFX_FILES_FAKE_ROOT_ID="synthetic-root-id",
        VFX_FILES_FAKE_DRIVE_ID="synthetic-drive-id",
        VFX_FILES_KITSU_ORIGIN=os.environ.get("VFX_FILES_KITSU_ORIGIN"),
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=15),
    )
    with app.app_context():
        suffix = uuid4().hex[:10]
        manager = _new_synthetic_manager(Person, suffix)
        artist_a = Person(
            first_name="Synthetic",
            last_name=f"ArtistA{suffix}",
            email=f"artist-a-{suffix}@example.invalid",
            role="user",
        )
        artist_b = Person(
            first_name="Synthetic",
            last_name=f"ArtistB{suffix}",
            email=f"artist-b-{suffix}@example.invalid",
            role="user",
        )
        supervisor = Person(
            first_name="Synthetic",
            last_name=f"Supervisor{suffix}",
            email=f"supervisor-{suffix}@example.invalid",
            role="supervisor",
        )
        sequence_type = EntityType.query.filter_by(name="Sequence").one_or_none()
        created_sequence_type = sequence_type is None
        if sequence_type is None:
            sequence_type = EntityType(name="Sequence")
        shot_type = EntityType(name=f"VFXShot{suffix}")
        task_type = TaskType(name=f"VFXComp{suffix}", for_entity="Shot")
        output_type = OutputType(name=f"VFXOutput{suffix}", short_name=f"VFXOut{suffix}")
        file_status = FileStatus(name=f"VFXStatus{suffix}", color="#33AA77")
        project = Project(name=f"VFX Files Installed E2E {suffix}", team=[manager, artist_a, artist_b, supervisor])
        db.session.add_all(
            [
                manager,
                artist_a,
                artist_b,
                supervisor,
                sequence_type,
                shot_type,
                task_type,
                output_type,
                file_status,
                project,
            ]
        )
        db.session.flush()
        memberships = {
            manager.id: "manager",
            artist_a.id: "user",
            artist_b.id: "user",
            supervisor.id: "supervisor",
        }
        for person_id, role in memberships.items():
            membership = ProjectPersonLink.query.filter_by(project_id=project.id, person_id=person_id).one()
            membership.role = role
        sequence = Entity(name=f"SEQ{suffix}", project_id=project.id, entity_type_id=sequence_type.id)
        db.session.add(sequence)
        db.session.flush()
        shot = Entity(
            name=f"SH{suffix}",
            project_id=project.id,
            entity_type_id=shot_type.id,
            parent_id=sequence.id,
        )
        db.session.add(shot)
        db.session.flush()
        task = Task(name="comp", project_id=project.id, task_type_id=task_type.id, entity_id=shot.id)
        db.session.add(task)
        db.session.flush()
        db.session.add_all([
            TaskPersonLink(task_id=task.id, person_id=artist_a.id),
            TaskPersonLink(task_id=task.id, person_id=artist_b.id),
            TaskPersonLink(task_id=task.id, person_id=supervisor.id),
        ])
        connection = StorageConnection(
            production_id=project.id,
            provider="google_drive",
            account_mode="shared_drive",
            drive_id="synthetic-drive-id",
            allowed_root_id="synthetic-root-id",
            credential_ref="synthetic-test-reference",
            max_file_bytes=1024 * 1024,
            quota_bytes=8 * 1024 * 1024,
            enabled=True,
        )
        db.session.add(connection)
        db.session.flush()
        db.session.add(FolderBinding(
            storage_connection_id=connection.id,
            allowed_root_id=connection.allowed_root_id,
            canonical_scope_key=f"production:{project.id}:publish:synthetic-main",
            parent_provider_id=connection.allowed_root_id,
            provider_folder_id="template-publish",
            state="ready",
        ))
        db.session.add(OutputFamilyMapping(
            production_id=project.id,
            family_key="synthetic-main",
            output_type_id=output_type.id,
            task_type_id=task_type.id,
            name="Synthetic Main",
            representation="main",
            extension="exr",
            file_status_id=file_status.id,
            publish_folder_id="template-publish",
            enabled=True,
        ))
        db.session.commit()

        storage = PersistentFakeStorage(fake_path, root_id="synthetic-root-id", drive_id="synthetic-drive-id")
        folders = [
            ("synthetic-root-id", "template-root", "Shared Production Template"),
            ("template-root", "template-shots", "02_Shots"),
            ("template-shots", "template-sequence", "{sequence}"),
            ("template-sequence", "template-shot", "{shot}"),
            ("template-shot", "template-working", "Working"),
            ("template-working", "template-task", "{task}"),
            ("template-shot", "template-input", "Input"),
            ("template-shot", "template-publish", "Publish"),
            ("template-shot", "template-review", "Review"),
            ("template-shot", "template-handoff", "Handoff"),
        ]
        for parent_id, folder_id, name in folders:
            storage.create_folder(parent_id, folder_id, {"display_name": name})

        cookie_name = app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie")
        tokens = {
            "manager_token": create_access_token_for_raw_person(manager),
            "artist_a_token": create_access_token_for_raw_person(artist_a),
            "artist_b_token": create_access_token_for_raw_person(artist_b),
            "supervisor_token": create_access_token_for_raw_person(supervisor),
        }
        if not all(_token_has_short_expiry(token) for token in tokens.values()):
            raise RuntimeError("Refusing to emit a test identity without a short token expiry")
        print(json.dumps({
            "production_id": str(project.id),
            "task_id": str(task.id),
            "sequence_id": str(sequence.id),
            "shot_id": str(shot.id),
            "task_type_id": str(task_type.id),
            "entity_type_id": str(shot_type.id),
            "sequence_type_created": created_sequence_type,
            "storage_connection_id": str(connection.id),
            "artist_a_id": str(artist_a.id),
            "artist_b_id": str(artist_b.id),
            "supervisor_id": str(supervisor.id),
            "manager_id": str(manager.id),
            "cookie_name": cookie_name,
            **tokens,
            "root_url": root_url,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
