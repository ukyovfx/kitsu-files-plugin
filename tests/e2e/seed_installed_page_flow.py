"""Create a disposable Production, Department Tasks, and synthetic browser identities."""

import json
import os
from uuid import uuid4

from zou.app import create_app, db
from zou.app.models.entity import Entity
from zou.app.models.entity_type import EntityType
from zou.app.models.department import Department
from zou.app.models.person import DepartmentLink, Person
from zou.app.models.project import Project, ProjectPersonLink, ProjectTaskTypeLink
from zou.app.models.project_status import ProjectStatus
from zou.app.models.file_status import FileStatus
from zou.app.models.output_type import OutputType
from zou.app.models.task import Task, TaskPersonLink
from zou.app.models.task_type import TaskType
from zou.app.utils.auth import encrypt_password

from seeder_safety import (
    new_synthetic_manager as _new_synthetic_manager,
    validated_disposable_root_url as _validated_disposable_root_url,
    validated_synthetic_login_password as _validated_synthetic_login_password,
)
from vfx_files.models import FolderBinding, OutputFamilyMapping, StorageConnection
from vfx_files.storage.fake import PersistentFakeStorage


def main():
    root_url = _validated_disposable_root_url()
    test_password = _validated_synthetic_login_password()
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
        artist_c = Person(
            first_name="Synthetic",
            last_name=f"ArtistC{suffix}",
            email=f"artist-c-{suffix}@example.invalid",
            role="user",
        )
        supervisor = Person(
            first_name="Synthetic",
            last_name=f"Supervisor{suffix}",
            email=f"supervisor-{suffix}@example.invalid",
            role="supervisor",
        )
        synthetic_people = [manager, artist_a, artist_b, artist_c, supervisor]
        for person in synthetic_people:
            person.active = True
            person.archived = False
            person.password = encrypt_password(test_password)

        open_status = ProjectStatus.query.filter(
            ProjectStatus.name.in_(("Open", "Active", "open"))
        ).order_by(ProjectStatus.name).first()
        if open_status is None:
            open_status = ProjectStatus(name="Open", color="#33AA77")
            db.session.add(open_status)
            db.session.flush()
        sequence_type = EntityType.query.filter_by(name="Sequence").one_or_none()
        created_sequence_type = sequence_type is None
        if sequence_type is None:
            sequence_type = EntityType(name="Sequence")
        shot_type = EntityType(name=f"VFXShot{suffix}")
        source_department = Department(name=f"VFXLighting{suffix}", color="#64748B")
        recipient_department = Department(name=f"VFXCompositing{suffix}", color="#64748B")
        db.session.add_all([source_department, recipient_department])
        db.session.flush()
        task_type = TaskType(
            name=f"VFXLighting{suffix}", for_entity="Shot", department_id=source_department.id,
        )
        recipient_task_type = TaskType(
            name=f"VFXCompositing{suffix}", for_entity="Shot", department_id=recipient_department.id,
        )
        output_type = OutputType(name=f"VFXOutput{suffix}", short_name=f"VFXOut{suffix}")
        file_status = FileStatus(name=f"VFXStatus{suffix}", color="#33AA77")
        project = Project(
            name=f"VFX Files Installed E2E {suffix}",
            team=synthetic_people,
            project_status_id=open_status.id,
        )
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
                recipient_task_type,
                project,
            ]
        )
        db.session.flush()
        memberships = {
            manager.id: "manager",
            artist_a.id: "user",
            artist_b.id: "user",
            artist_c.id: "user",
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
        recipient_task = Task(
            name="compositing-review", project_id=project.id,
            task_type_id=recipient_task_type.id, entity_id=shot.id,
        )
        db.session.add(recipient_task)
        db.session.flush()
        db.session.add_all([
            ProjectTaskTypeLink(project_id=project.id, task_type_id=task_type.id),
            ProjectTaskTypeLink(project_id=project.id, task_type_id=recipient_task_type.id),
            DepartmentLink(department_id=recipient_department.id, person_id=artist_a.id),
            DepartmentLink(department_id=recipient_department.id, person_id=artist_c.id),
            TaskPersonLink(task_id=recipient_task.id, person_id=artist_a.id),
            TaskPersonLink(task_id=recipient_task.id, person_id=artist_c.id),
        ])
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
        print(json.dumps({
            "production_id": str(project.id),
            "production_name": project.name,
            "task_id": str(task.id),
            "recipient_task_id": str(recipient_task.id),
            "sequence_id": str(sequence.id),
            "shot_id": str(shot.id),
            "task_type_id": str(task_type.id),
            "recipient_department_id": str(recipient_department.id),
            "recipient_department_name": recipient_department.name,
            "entity_type_id": str(shot_type.id),
            "sequence_type_created": created_sequence_type,
            "storage_connection_id": str(connection.id),
            "artist_a_id": str(artist_a.id),
            "artist_b_id": str(artist_b.id),
            "artist_c_id": str(artist_c.id),
            "supervisor_id": str(supervisor.id),
            "manager_id": str(manager.id),
            "login_emails": {
                "manager": manager.email,
                "artist_a": artist_a.email,
                "artist_b": artist_b.email,
                "artist_c": artist_c.email,
                "supervisor": supervisor.email,
            },
            "cookie_name": cookie_name,
            "root_url": root_url,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
