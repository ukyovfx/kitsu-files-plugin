"""Create a disposable Production and three browser identities on the pinned host."""

import json
import os
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
from flask_jwt_extended import create_access_token, get_jti

from seeder_safety import (
    create_short_lived_person_token,
    new_synthetic_manager as _new_synthetic_manager,
    token_expiry_config,
    token_has_short_expiry as _token_has_short_expiry,
    validated_disposable_root_url as _validated_disposable_root_url,
)
from vfx_files.models import FolderBinding, OutputFamilyMapping, StorageConnection
from vfx_files.storage.fake import PersistentFakeStorage


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
        JWT_ACCESS_TOKEN_EXPIRES=token_expiry_config(),
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
            existing = storage.get(folder_id)
            if existing is not None:
                if (
                    existing.parent_id != parent_id
                    or existing.name != name
                    or existing.mime_type != "folder"
                    or existing.trashed
                ):
                    raise RuntimeError(
                        f"Synthetic template object {folder_id} does not match the expected folder"
                    )
                continue
            storage.create_folder(parent_id, folder_id, {"display_name": name})

        cookie_name = app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie")
        tokens = {
            "manager_token": create_short_lived_person_token(manager, create_access_token, get_jti),
            "artist_a_token": create_short_lived_person_token(artist_a, create_access_token, get_jti),
            "artist_b_token": create_short_lived_person_token(artist_b, create_access_token, get_jti),
            "supervisor_token": create_short_lived_person_token(supervisor, create_access_token, get_jti),
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
