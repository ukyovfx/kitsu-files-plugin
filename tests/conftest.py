import os
import importlib
import sys
from uuid import uuid4

import pytest


def _person_with_role(Person, *roles):
    """Return a deterministic host identity while handling Zou Choice values."""
    people = Person.query.order_by(Person.id).all()
    return next(
        (person for role in roles for person in people if getattr(person.role, "code", person.role) == role),
        people[0] if people else None,
    )


if plugin_folder := os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER"):
    if plugin_folder not in sys.path:
        sys.path.insert(0, plugin_folder)
    # Zou imports an installed plugin by its manifest ID (`vfx_files`). The
    # test tree's service modules use the local `plugin` package name; alias
    # the model module so SQLAlchemy sees one class/table registry in-host.
    try:
        sys.modules["plugin.models"] = importlib.import_module("vfx_files.models")
    except ImportError:
        pass


@pytest.fixture(scope="session")
def zou_app():
    pytest.importorskip("zou")
    from zou.app import config, create_app

    plugin_folder = os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER")
    if plugin_folder:
        config.PLUGIN_FOLDER = plugin_folder

    app = create_app()
    context = app.app_context()
    context.push()
    yield app
    context.pop()


@pytest.fixture(scope="session")
def zou_db_factory(zou_app):
    from sqlalchemy.orm import sessionmaker
    from zou.app import db

    return sessionmaker(bind=db.engine, expire_on_commit=False)


@pytest.fixture
def authenticated_plugin_client(zou_app):
    from zou.app.models.person import Person
    from zou.app.services.persons_service import create_access_token_for_raw_person

    plugin_folder = os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER")
    if not plugin_folder:
        pytest.skip("Set VFX_FILES_TEST_PLUGIN_FOLDER to the installed disposable plugin folder")
    app = zou_app
    app.config.update(
        TESTING=True,
        SECRET_KEY="vfx-files-isolated-test-secret-key-32-bytes-minimum",
        VFX_FILES_ENABLED=True,
        VFX_FILES_KITSU_ORIGIN="https://kitsu.test",
    )
    with app.app_context():
        # The pinned host accumulates synthetic test actors across runs. Pick
        # the same privileged fixture identity as synthetic_upload_task instead
        # of whichever person happens to sort first (often a prior reviewer).
        person = _person_with_role(Person, "admin", "manager")
        token = create_access_token_for_raw_person(person)
        cookie_name = app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie")
        token_locations = app.config.get("JWT_TOKEN_LOCATION")
    client = app.test_client()
    client.set_cookie(cookie_name, token)
    client.vfx_files_token = token
    client.vfx_files_token_locations = token_locations
    client.vfx_app = app
    return client


@pytest.fixture
def synthetic_upload_task(authenticated_plugin_client, zou_db_factory, tmp_path):
    from sqlalchemy import select
    from zou.app.models.entity import Entity
    from zou.app.models.entity_type import EntityType
    from zou.app.models.person import Person
    from zou.app.models.project import Project, ProjectPersonLink
    from zou.app.models.task import Task, TaskPersonLink
    from zou.app.models.task_type import TaskType
    from plugin.models import ApprovalEvent, FolderBinding, Handoff, HandoffEvent, Job, LatestWorkingPointer, NativeCreateEvent, OutputFamilyMapping, PinnedDependency, Publish, PublishCheckpoint, PublishPointer, ReconciliationFinding, SharedTemplateConfiguration, StorageConnection, UploadPart, UploadSession, WorkingDependency, WorkingRevision, WorkingRevisionFile

    app = authenticated_plugin_client.vfx_app
    with app.app_context():
        actor = _person_with_role(Person, "admin", "manager")
        actor_id = actor.id
        entity_type = EntityType(name=f"VFX{uuid4().hex[:8]}")
        task_type = TaskType(name=f"VFXTask{uuid4().hex[:8]}", for_entity="Shot")
        with zou_db_factory.begin() as session:
            actor = session.get(Person, actor_id)
            project = Project(name=f"VFX Files Test {uuid4()}", team=[actor])
            session.add_all([project, entity_type, task_type])
            session.flush()
            manager_membership = session.get(ProjectPersonLink, (project.id, actor.id))
            if manager_membership is not None:
                manager_membership.role = "manager"
            entity = Entity(name=f"SHOT{uuid4().hex[:8]}", project_id=project.id, entity_type_id=entity_type.id)
            session.add(entity)
            session.flush()
            task = Task(name="main", project_id=project.id, task_type_id=task_type.id, entity_id=entity.id)
            connection = StorageConnection(
                production_id=project.id, provider="google_drive", account_mode="shared_drive",
                drive_id="synthetic-drive-id", allowed_root_id="synthetic-root-id",
                credential_ref="synthetic-reference-only", max_file_bytes=1024,
                quota_bytes=4096, enabled=True,
            )
            session.add_all([task, connection])
            session.flush()
            session.add(TaskPersonLink(task_id=task.id, person_id=actor.id))
            values = (project.id, entity_type.id, task_type.id, entity.id, task.id, connection.id, actor.id)

    app.config.update(VFX_FILES_STAGING_ROOT=str(tmp_path), VFX_FILES_MAX_CHUNK_BYTES=4)
    yield values

    project_id, entity_type_id, task_type_id, entity_id, task_id, connection_id, _actor_id = values
    with zou_db_factory.begin() as session:
        publishes = session.scalars(select(Publish).where(Publish.task_id == task_id)).all()
        publish_ids = [row.id for row in publishes]
        if publish_ids:
            immutable_evidence = session.scalar(
                select(PublishCheckpoint.id).where(PublishCheckpoint.publish_id.in_(publish_ids)).limit(1)
            )
            if immutable_evidence is not None:
                # Completed Publish evidence is intentionally immutable. Keep
                # its synthetic Kitsu graph together in the disposable host.
                return
            native_output_ids = [row.native_output_file_id for row in publishes if row.native_output_file_id]
            native_source_ids = [row.source_working_file_id for row in publishes if row.source_working_file_id]
            handoffs = session.scalars(select(Handoff).where(Handoff.publish_id.in_(publish_ids))).all()
            handoff_ids = [row.id for row in handoffs]
            if handoff_ids:
                session.execute(HandoffEvent.__table__.delete().where(HandoffEvent.handoff_id.in_(handoff_ids)))
            session.execute(ApprovalEvent.__table__.delete().where(ApprovalEvent.publish_id.in_(publish_ids)))
            session.execute(NativeCreateEvent.__table__.delete().where(NativeCreateEvent.publish_id.in_(publish_ids)))
            session.execute(PublishCheckpoint.__table__.delete().where(PublishCheckpoint.publish_id.in_(publish_ids)))
            session.execute(PinnedDependency.__table__.delete().where(PinnedDependency.publish_id.in_(publish_ids)))
            session.execute(Handoff.__table__.delete().where(Handoff.id.in_(handoff_ids)))
            session.execute(ReconciliationFinding.__table__.delete().where(ReconciliationFinding.kitsu_object_id.in_(publish_ids)))
            session.execute(PublishPointer.__table__.delete().where(PublishPointer.task_id == task_id))
            if native_output_ids:
                from zou.app.models.output_file import OutputFile
                session.execute(OutputFile.__table__.delete().where(OutputFile.id.in_(native_output_ids)))
            if native_source_ids:
                from zou.app.models.working_file import WorkingFile
                session.execute(WorkingFile.__table__.delete().where(WorkingFile.id.in_(native_source_ids)))
            for job in session.scalars(select(Job).where(Job.payload["task_id"].astext == str(task_id))).all():
                session.delete(job)
            session.execute(Publish.__table__.delete().where(Publish.id.in_(publish_ids)))
        session.execute(OutputFamilyMapping.__table__.delete().where(OutputFamilyMapping.production_id == project_id))
        revisions = session.scalars(select(WorkingRevision).where(WorkingRevision.task_id == task_id)).all()
        revision_ids = [row.id for row in revisions]
        if revision_ids:
            session.execute(LatestWorkingPointer.__table__.delete().where(LatestWorkingPointer.task_id == task_id))
            session.execute(WorkingDependency.__table__.delete().where(WorkingDependency.revision_id.in_(revision_ids)))
        uploads = session.scalars(select(UploadSession).where(UploadSession.task_id == task_id)).all()
        upload_ids = [row.id for row in uploads]
        if upload_ids:
            session.execute(UploadPart.__table__.delete().where(UploadPart.session_id.in_(upload_ids)))
            for upload_id in upload_ids:
                job = session.scalar(select(Job).where(Job.idempotency_key == f"upload-finalize:{upload_id}"))
                if job:
                    session.delete(job)
            session.execute(UploadSession.__table__.delete().where(UploadSession.id.in_(upload_ids)))
        if revision_ids:
            session.execute(WorkingRevisionFile.__table__.delete().where(WorkingRevisionFile.revision_id.in_(revision_ids)))
            session.execute(WorkingRevision.__table__.delete().where(WorkingRevision.id.in_(revision_ids)))
        folder_jobs = session.scalars(select(Job).where(Job.job_type == "provision_task_folders")).all()
        for job in folder_jobs:
            if job.payload.get("task_id") == str(task_id):
                session.delete(job)
        session.execute(
            SharedTemplateConfiguration.__table__.delete().where(
                SharedTemplateConfiguration.storage_connection_id == connection_id
            )
        )
        session.execute(FolderBinding.__table__.delete().where(FolderBinding.storage_connection_id == connection_id))
        session.execute(StorageConnection.__table__.delete().where(StorageConnection.id == connection_id))
        session.execute(TaskPersonLink.__table__.delete().where(TaskPersonLink.task_id == task_id))
        session.execute(Task.__table__.delete().where(Task.id == task_id))
        session.execute(Entity.__table__.delete().where(Entity.id == entity_id))
        project = session.get(Project, project_id)
        if project is not None:
            project.team.clear()
            session.flush()
        session.execute(Project.__table__.delete().where(Project.id == project_id))
        session.execute(TaskType.__table__.delete().where(TaskType.id == task_type_id))
        session.execute(EntityType.__table__.delete().where(EntityType.id == entity_type_id))
