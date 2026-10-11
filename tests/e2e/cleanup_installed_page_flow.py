"""Remove one identified synthetic installed-page test Production and its local bytes."""

import os
import shutil
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, text
from zou.app import create_app, db
from zou.app.models.entity import Entity
from zou.app.models.project import Project, ProjectPersonLink
from zou.app.models.task import Task, TaskPersonLink
from zou.app.models.task_type import TaskType
from zou.app.models.person import Person
from zou.app.models.output_file import OutputFile
from zou.app.models.output_type import OutputType
from zou.app.models.file_status import FileStatus
from zou.app.models.working_file import WorkingFile
from zou.app.models.event import ApiEvent

from vfx_files.models import (
    ApprovalEvent,
    FolderBinding,
    Handoff,
    HandoffEvent,
    Job,
    LatestWorkingPointer,
    NativeCreateEvent,
    OutputFamilyMapping,
    PinnedDependency,
    PluginState,
    Publish,
    PublishCheckpoint,
    PublishPointer,
    ReconciliationFinding,
    SharedTemplateConfiguration,
    StorageConnection,
    UploadPart,
    UploadSession,
    WorkingDependency,
    WorkingRevision,
    WorkingRevisionFile,
)


def _remove_runtime_directory(value, required_prefix):
    if not value:
        return
    path = Path(value).resolve()
    if path.parent != Path("/tmp") or not path.name.startswith(required_prefix):
        raise RuntimeError("Refusing to remove a path outside the task-owned synthetic test directory")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def main():
    project_id = UUID(os.environ["VFX_FILES_LIVE_PRODUCTION_ID"])
    app = create_app()
    with app.app_context():
        project = db.session.get(Project, project_id)
        if project is None:
            return
        if not project.name.startswith("VFX Files Installed E2E "):
            raise RuntimeError("The target Production is not owned by the installed-page E2E")
        # Audit rows are immutable during application operation. This cleanup
        # runs only after the synthetic Production prefix check above and
        # temporarily disables user triggers while deleting its exact rows;
        # FK triggers remain enabled. The transaction restores trigger state
        # automatically if cleanup fails before re-enabling them below.
        audit_tables = (
            "plugin_vfx_files_publish_checkpoint",
            "plugin_vfx_files_native_create_event",
            "plugin_vfx_files_approval_event",
            "plugin_vfx_files_pinned_dependency",
            "plugin_vfx_files_handoff_event",
        )
        for table_name in audit_tables:
            db.session.execute(text(f'ALTER TABLE "{table_name}" DISABLE TRIGGER USER'))
        tasks = Task.query.filter_by(project_id=project_id).all()
        task_ids = [row.id for row in tasks]
        entities = Entity.query.filter_by(project_id=project_id).all()
        memberships = ProjectPersonLink.query.filter_by(project_id=project_id).all()
        connections = StorageConnection.query.filter_by(production_id=project_id).all()
        connection_ids = [row.id for row in connections]
        task_type_ids = {row.task_type_id for row in tasks}
        revisions = db.session.scalars(select(WorkingRevision).where(WorkingRevision.task_id.in_(task_ids))).all() if task_ids else []
        revision_ids = [row.id for row in revisions]
        revision_files = db.session.scalars(select(WorkingRevisionFile).where(WorkingRevisionFile.revision_id.in_(revision_ids))).all() if revision_ids else []
        revision_file_ids = [row.id for row in revision_files]
        uploads = db.session.scalars(select(UploadSession).where(UploadSession.task_id.in_(task_ids))).all() if task_ids else []
        upload_ids = [row.id for row in uploads]
        publishes = db.session.scalars(select(Publish).where(Publish.task_id.in_(task_ids))).all() if task_ids else []
        publish_ids = [row.id for row in publishes]
        native_output_ids = [row.native_output_file_id for row in publishes if row.native_output_file_id]
        native_working_sources = [(row.source_working_file_id, row.source_revision_file_id) for row in publishes if row.source_working_file_id and row.source_revision_file_id]
        handoffs = db.session.scalars(select(Handoff).where(Handoff.task_id.in_(task_ids))).all() if task_ids else []
        handoff_ids = [row.id for row in handoffs]

        for model in (UploadPart,):
            if upload_ids:
                db.session.execute(delete(model).where(model.session_id.in_(upload_ids)))
        if upload_ids:
            db.session.execute(delete(Job).where(Job.idempotency_key.in_([f"upload-finalize:{item}" for item in upload_ids])))
            db.session.execute(delete(UploadSession).where(UploadSession.id.in_(upload_ids)))
        if revision_file_ids:
            db.session.execute(delete(UploadSession).where(UploadSession.working_revision_file_id.in_(revision_file_ids)))
            db.session.execute(delete(WorkingDependency).where(WorkingDependency.revision_id.in_(revision_ids)))
        if task_ids:
            for output_id in native_output_ids:
                output = db.session.get(OutputFile, output_id)
                if output is not None:
                    db.session.delete(output)
            db.session.flush()
            for working_id, revision_file_id in native_working_sources:
                working = db.session.get(WorkingFile, working_id)
                if working is not None and working.name == f"vfx-files-{revision_file_id.hex}":
                    db.session.delete(working)
            db.session.flush()
            db.session.execute(delete(LatestWorkingPointer).where(LatestWorkingPointer.task_id.in_(task_ids)))
            db.session.execute(delete(Job).where(Job.payload["task_id"].astext.in_([str(item) for item in task_ids])))
            db.session.execute(delete(HandoffEvent).where(HandoffEvent.handoff_id.in_(handoff_ids)))
            db.session.execute(delete(Handoff).where(Handoff.id.in_(handoff_ids)))
            db.session.execute(delete(TaskPersonLink).where(TaskPersonLink.task_id.in_(task_ids)))
            db.session.execute(delete(PublishCheckpoint).where(PublishCheckpoint.publish_id.in_(publish_ids)))
            db.session.execute(delete(ApprovalEvent).where(ApprovalEvent.publish_id.in_(publish_ids)))
            db.session.execute(delete(NativeCreateEvent).where(NativeCreateEvent.publish_id.in_(publish_ids)))
            db.session.execute(delete(PinnedDependency).where(PinnedDependency.publish_id.in_(publish_ids)))
            db.session.execute(delete(PublishPointer).where(PublishPointer.task_id.in_(task_ids)))
            db.session.execute(delete(Publish).where(Publish.id.in_(publish_ids)))
            db.session.execute(delete(ReconciliationFinding).where(ReconciliationFinding.kitsu_object_id.in_(publish_ids)))
            # Publish.source_revision_file_id is a restrictive FK; keep the
            # immutable source rows until the synthetic Publish row is gone.
            if revision_file_ids:
                db.session.execute(delete(WorkingRevisionFile).where(WorkingRevisionFile.id.in_(revision_file_ids)))
            db.session.execute(delete(WorkingRevision).where(WorkingRevision.id.in_(revision_ids)))
        if connection_ids:
            db.session.execute(delete(SharedTemplateConfiguration).where(SharedTemplateConfiguration.storage_connection_id.in_(connection_ids)))
            db.session.execute(delete(FolderBinding).where(FolderBinding.storage_connection_id.in_(connection_ids)))
            db.session.execute(delete(OutputFamilyMapping).where(OutputFamilyMapping.production_id == project_id))
            db.session.execute(delete(StorageConnection).where(StorageConnection.id.in_(connection_ids)))
        for task in tasks:
            db.session.delete(task)
        db.session.flush()
        remaining_entities = {row.id: row for row in entities}
        while remaining_entities:
            parent_ids = {row.parent_id for row in remaining_entities.values() if row.parent_id is not None}
            leaves = [entity_id for entity_id in remaining_entities if entity_id not in parent_ids]
            if not leaves:
                raise RuntimeError("Synthetic entity hierarchy contains a cycle; refusing cleanup")
            for entity_id in leaves:
                db.session.delete(remaining_entities.pop(entity_id))
            db.session.flush()
        for task_type in db.session.scalars(select(TaskType).where(TaskType.id.in_(task_type_ids))).all():
            if not Task.query.filter_by(task_type_id=task_type.id).first():
                db.session.delete(task_type)
        suffix = project.name.removeprefix("VFX Files Installed E2E ")
        for output_type in db.session.scalars(select(OutputType).where(OutputType.name == f"VFXOutput{suffix}")).all():
            if not db.session.query(OutputFile.id).filter_by(output_type_id=output_type.id).first():
                db.session.delete(output_type)
        for file_status in db.session.scalars(select(FileStatus).where(FileStatus.name == f"VFXStatus{suffix}")).all():
            if not db.session.query(OutputFile.id).filter_by(file_status_id=file_status.id).first():
                db.session.delete(file_status)
        artist_ids = {
            UUID(value)
            for value in (os.environ.get("VFX_FILES_LIVE_ARTIST_A_ID"), os.environ.get("VFX_FILES_LIVE_ARTIST_B_ID"), os.environ.get("VFX_FILES_LIVE_SUPERVISOR_ID"))
            if value
        }
        if not artist_ids:
            suffix = project.name.removeprefix("VFX Files Installed E2E ")
            artist_ids = {
                person.id
                for person in Person.query.filter(
                    Person.email.in_((
                        f"artist-a-{suffix}@example.invalid",
                        f"artist-b-{suffix}@example.invalid",
                        f"supervisor-{suffix}@example.invalid",
                    ))
                ).all()
            }
        db.session.execute(delete(ApiEvent).where(ApiEvent.project_id == project_id))
        db.session.delete(project)
        db.session.flush()
        for row in memberships:
            person = db.session.get(Person, row.person_id)
            if person is not None and person.id in artist_ids:
                db.session.delete(person)
        for table_name in audit_tables:
            db.session.execute(text(f'ALTER TABLE "{table_name}" ENABLE TRIGGER USER'))
        db.session.commit()

    _remove_runtime_directory(os.environ.get("VFX_FILES_LIVE_STAGING_ROOT"), "vfx-files-installed-page-e2e-")
    fake_path = os.environ.get("VFX_FILES_LIVE_FAKE_STORAGE_PATH")
    _remove_runtime_directory(fake_path, "vfx-files-installed-page-e2e-")
    if fake_path:
        storage_blobs = Path(fake_path).with_suffix("")
        storage_blobs = storage_blobs.parent / (storage_blobs.name + "-objects")
        _remove_runtime_directory(str(storage_blobs), "vfx-files-installed-page-e2e-")


if __name__ == "__main__":
    main()
