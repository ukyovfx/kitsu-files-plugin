from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

from sqlalchemy import select

from plugin.models import OutputFamilyMapping, Publish, PublishPointer
from plugin.publishes.checkpoints import advance_latest_published
from plugin.publishes.service import reserve_publish


@pytest.fixture
def publish_scope(zou_db_factory):
    from zou.app.models.entity import Entity
    from zou.app.models.entity_type import EntityType
    from zou.app.models.project import Project
    from zou.app.models.task import Task
    from zou.app.models.task_type import TaskType

    project_id, entity_type_id, task_type_id, entity_id, task_id = (uuid4() for _ in range(5))
    family_id = uuid4()
    family = OutputFamilyMapping(
        id=family_id,
        production_id=project_id,
        family_key=f"synthetic-{family_id}",
        output_type_id=uuid4(),
        task_type_id=task_type_id,
        name="Comp",
        representation="main",
        extension="exr",
        file_status_id=uuid4(),
        enabled=True,
    )
    with zou_db_factory.begin() as session:
        session.add_all([
            Project(id=project_id, name=f"Publish Test {project_id}"),
            EntityType(id=entity_type_id, name=f"ET{entity_type_id.hex[:8]}"),
            TaskType(id=task_type_id, name=f"TT{task_type_id.hex[:8]}", for_entity="Shot"),
        ])
        session.flush()
        session.add(Entity(id=entity_id, name=f"SHOT{entity_id.hex[:8]}", project_id=project_id, entity_type_id=entity_type_id))
        session.flush()
        session.add(Task(id=task_id, name="main", project_id=project_id, task_type_id=task_type_id, entity_id=entity_id))
        session.add(family)

    yield zou_db_factory, task_id, family_id, project_id, entity_id, task_type_id, entity_type_id

    with zou_db_factory.begin() as session:
        session.execute(PublishPointer.__table__.delete().where(PublishPointer.task_id == task_id))
        session.execute(Publish.__table__.delete().where(Publish.task_id == task_id))
        session.execute(OutputFamilyMapping.__table__.delete().where(OutputFamilyMapping.id == family_id))
        session.execute(Task.__table__.delete().where(Task.id == task_id))
        session.execute(Entity.__table__.delete().where(Entity.id == entity_id))
        session.execute(Project.__table__.delete().where(Project.id == project_id))
        session.execute(TaskType.__table__.delete().where(TaskType.id == task_type_id))
        session.execute(EntityType.__table__.delete().where(EntityType.id == entity_type_id))


def test_concurrent_publish_reservations_are_unique_and_monotone(publish_scope):
    factory, task_id, family_id, _production_id, _entity_id, _task_type_id, _entity_type_id = publish_scope
    actors = (uuid4(), uuid4())

    def reserve(actor_id):
        return reserve_publish(factory, task_id=task_id, output_family_id=family_id, source_working_file_id=None, actor_id=actor_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(reserve, actors))

    assert sorted(result.version for result in versions) == [1, 2]
    with factory() as session:
        rows = session.scalars(select(Publish).where(Publish.task_id == task_id, Publish.output_family_id == family_id).order_by(Publish.version)).all()
        assert [row.version for row in rows] == [1, 2]
        pointer = session.get(PublishPointer, (task_id, family_id))
        assert pointer.latest_published_id is None
        assert pointer.latest_approved_id is None


def test_late_completion_of_older_publish_cannot_move_latest_pointer_backwards(publish_scope):
    factory, task_id, family_id, _production_id, _entity_id, _task_type_id, _entity_type_id = publish_scope
    older_id, newer_id = uuid4(), uuid4()
    with factory.begin() as session:
        session.add_all([
            Publish(
                id=older_id, task_id=task_id, output_family_id=family_id, version=1,
                created_by=uuid4(), state="completed", qc_state="passed",
            ),
            Publish(
                id=newer_id, task_id=task_id, output_family_id=family_id, version=2,
                created_by=uuid4(), state="completed", qc_state="passed",
            ),
        ])

    advance_latest_published(factory, newer_id)
    advance_latest_published(factory, older_id)

    with factory() as session:
        pointer = session.get(PublishPointer, (task_id, family_id))
        assert pointer.latest_published_id == newer_id
