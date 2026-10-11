"""Delete only the synthetic project created by seed_disposable_upload.py."""

import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from zou.app import create_app, db
from zou.app.models.entity import Entity
from zou.app.models.entity_type import EntityType
from zou.app.models.project import Project
from zou.app.models.task import Task, TaskPersonLink
from zou.app.models.task_type import TaskType

from vfx_files.models import Job, StorageConnection, UploadPart, UploadSession
from vfx_files.uploads.staging import FileSystemStaging


def main(seed_path):
    seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    project_id = UUID(seed["production_id"])
    task_id = UUID(seed["task_id"])
    entity_id = UUID(seed["entity_id"])
    task_type_id = UUID(seed["task_type_id"])
    entity_type_id = UUID(seed["entity_type_id"])
    connection_id = UUID(seed["storage_connection_id"])
    app = create_app()
    with app.app_context(), db.session.begin():
        uploads = db.session.scalars(select(UploadSession).where(UploadSession.task_id == task_id)).all()
        staging_root = Path("/tmp/vfx-kitsu-test-root/staging")
        staging = FileSystemStaging(staging_root)
        for upload in uploads:
            keys = db.session.scalars(select(UploadPart.staging_key).where(UploadPart.session_id == upload.id)).all()
            staging.delete_session_chunks(str(upload.id), keys)
        upload_ids = [row.id for row in uploads]
        if upload_ids:
            db.session.execute(UploadPart.__table__.delete().where(UploadPart.session_id.in_(upload_ids)))
            jobs = db.session.scalars(select(Job).where(Job.idempotency_key.in_([f"upload-finalize:{value}" for value in upload_ids]))).all()
            for job in jobs:
                db.session.delete(job)
            db.session.execute(UploadSession.__table__.delete().where(UploadSession.id.in_(upload_ids)))
        db.session.execute(StorageConnection.__table__.delete().where(StorageConnection.id == connection_id))
        db.session.execute(TaskPersonLink.__table__.delete().where(TaskPersonLink.task_id == task_id))
        db.session.execute(Task.__table__.delete().where(Task.id == task_id))
        db.session.execute(Entity.__table__.delete().where(Entity.id == entity_id))
        project = db.session.get(Project, project_id)
        if project is not None:
            project.team.clear()
            db.session.flush()
            db.session.delete(project)
        task_type = db.session.get(TaskType, task_type_id)
        entity_type = db.session.get(EntityType, entity_type_id)
        if task_type is not None:
            db.session.delete(task_type)
        if entity_type is not None:
            db.session.delete(entity_type)
    print("Removed one synthetic browser Production and its plugin upload rows.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: cleanup_disposable_upload.py SEED_JSON")
    main(sys.argv[1])
