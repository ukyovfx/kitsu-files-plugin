"""Create one disposable synthetic Kitsu task and browser session on the pinned host."""

import json
from uuid import uuid4

from zou.app import create_app, db
from zou.app.models.entity import Entity
from zou.app.models.entity_type import EntityType
from zou.app.models.person import Person
from zou.app.models.project import Project
from zou.app.models.task import Task, TaskPersonLink
from zou.app.models.task_type import TaskType
from zou.app.services.persons_service import create_access_token_for_raw_person

from vfx_files.models import StorageConnection


def main():
    app = create_app()
    app.config.update(
        SECRET_KEY="vfx-files-isolated-test-secret-key-32-bytes-minimum",
        VFX_FILES_ENABLED=True,
        VFX_FILES_WORKER_ENABLED=True,
        VFX_FILES_KITSU_ORIGIN="https://kitsu.test",
    )
    with app.app_context():
        person = Person.query.first()
        if person is None:
            raise RuntimeError("Pinned synthetic Zou host has no seeded test identity")
        suffix = uuid4().hex[:10]
        project = Project(name=f"VFX Files Browser Test {suffix}", team=[person])
        entity_type = EntityType(name=f"VFXShot{suffix}")
        task_type = TaskType(name=f"VFXTask{suffix}", for_entity="Shot")
        db.session.add_all([project, entity_type, task_type])
        db.session.flush()
        entity = Entity(name=f"SHOT{suffix}", project_id=project.id, entity_type_id=entity_type.id)
        db.session.add(entity)
        db.session.flush()
        task = Task(name="main", project_id=project.id, task_type_id=task_type.id, entity_id=entity.id)
        db.session.add(task)
        db.session.flush()
        db.session.add(TaskPersonLink(task_id=task.id, person_id=person.id))
        connection = StorageConnection(
            production_id=project.id,
            provider="google_drive",
            account_mode="shared_drive",
            drive_id="synthetic-drive-id",
            allowed_root_id="synthetic-root-id",
            credential_ref="synthetic-reference-only",
            max_file_bytes=1024 * 1024,
            quota_bytes=2 * 1024 * 1024,
            enabled=True,
        )
        db.session.add(connection)
        db.session.commit()
        print(json.dumps({
            "production_id": str(project.id),
            "task_id": str(task.id),
            "entity_id": str(entity.id),
            "task_type_id": str(task_type.id),
            "entity_type_id": str(entity_type.id),
            "storage_connection_id": str(connection.id),
            "actor_id": str(person.id),
            "cookie_name": app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"),
            "access_token": create_access_token_for_raw_person(person),
        }, sort_keys=True))


if __name__ == "__main__":
    main()
