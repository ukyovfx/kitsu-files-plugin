"""Department Handoff HTTP acceptance on the pinned disposable Zou host."""

from uuid import UUID, uuid4
import hashlib
from types import SimpleNamespace

import pytest

pytest.importorskip("zou")
pytest.importorskip("flask_jwt_extended")


@pytest.fixture
def department_handoff_http(authenticated_plugin_client, synthetic_upload_task, zou_db_factory):
    from sqlalchemy import select

    from zou.app.models.department import Department
    from zou.app.models.entity import Entity
    from zou.app.models.person import DepartmentLink, Person
    from zou.app.models.project import Project, ProjectPersonLink, ProjectTaskTypeLink
    from zou.app.models.task import Task, TaskPersonLink
    from zou.app.models.task_type import TaskType
    from zou.app.services.persons_service import create_access_token_for_raw_person

    from plugin.models import Handoff, HandoffEvent, Publish

    client = authenticated_plugin_client
    app = client.vfx_app
    # Validate the authenticated harness before inserting the Department
    # graph. If the test cookie/configuration is invalid, fixture setup must
    # fail before it can leave a recipient Task behind without reaching yield.
    csrf_response = client.get("/plugins/vfx_files/csrf-token")
    csrf_payload = csrf_response.get_json(silent=True)
    assert (
        csrf_response.status_code == 200
        and isinstance(csrf_payload, dict)
        and isinstance(csrf_payload.get("csrf_token"), str)
    ), (
        "Kitsu CSRF fixture was unavailable before Department setup: "
        f"status={csrf_response.status_code}, "
        f"response_keys={sorted(csrf_payload) if isinstance(csrf_payload, dict) else []}"
    )
    production_id, entity_type_id, _source_type_id, entity_id, source_task_id, _connection_id, sender_id = synthetic_upload_task
    suffix = uuid4().hex[:10]
    publish_id = uuid4()
    artifact = b"immutable handoff Publish bytes"
    artifact_sha256 = hashlib.sha256(artifact).hexdigest()
    with zou_db_factory.begin() as session:
        recipient = Person(
            first_name="Synthetic", last_name=f"Department Artist {suffix}",
            email=f"department-{suffix}@example.invalid", role="user", active=True,
        )
        department = Department(name=f"Department {suffix}", color="#64748B")
        session.add_all([recipient, department])
        session.flush()
        task_type = TaskType(
            name=f"Task Type {suffix}", for_entity="Shot", department_id=department.id,
        )
        session.add(task_type)
        session.flush()
        target_task = Task(
            name=f"recipient-{suffix}", project_id=production_id,
            task_type_id=task_type.id, entity_id=entity_id,
        )
        session.add(target_task)
        session.flush()
        session.add_all([
            ProjectTaskTypeLink(project_id=production_id, task_type_id=task_type.id),
            ProjectPersonLink(project_id=production_id, person_id=recipient.id, role="user"),
            TaskPersonLink(task_id=target_task.id, person_id=recipient.id),
            DepartmentLink(department_id=department.id, person_id=recipient.id),
            Publish(
                id=publish_id, task_id=source_task_id, output_family_id=uuid4(), version=7,
                created_by=sender_id, state="completed", qc_state="passed",
                storage_connection_id=_connection_id,
                provider_artifact_id=f"synthetic-artifact-{suffix}",
                byte_count=len(artifact), sha256=artifact_sha256, manifest_sha256="b" * 64,
            ),
        ])
        session.flush()
        recipient_id, department_id, recipient_task_id = recipient.id, department.id, target_task.id
        source_entity_type_id = session.get(Entity, entity_id).entity_type_id
    # The test factory session that inserted the synthetic recipient is not
    # Zou's scoped session. Its token helper calls person.save(), so reload
    # the row into Zou's own session before creating the authenticated client.
    with app.app_context():
        recipient_for_token = Person.query.filter_by(id=recipient_id).one()
        recipient_token = create_access_token_for_raw_person(recipient_for_token)

    recipient_client = app.test_client()
    recipient_client.set_cookie(
        app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), recipient_token,
    )
    headers = {
        "Origin": "https://kitsu.test",
        "X-VFX-Files-CSRF": csrf_payload["csrf_token"],
        "Idempotency-Key": f"department-handoff:{suffix}",
    }
    yield {
        "app": app, "sender": client, "recipient": recipient_client, "headers": headers,
        "production_id": production_id, "source_task_id": source_task_id,
        "entity_type_id": source_entity_type_id,
        "sender_id": sender_id,
        "publish_id": publish_id, "recipient_id": recipient_id,
        "artifact": artifact, "artifact_sha256": artifact_sha256,
        "department_id": department_id, "recipient_task_id": recipient_task_id,
        "Handoff": Handoff, "HandoffEvent": HandoffEvent,
    }
    with zou_db_factory.begin() as session:
        if session.scalar(select(Handoff.id).where(Handoff.publish_id == publish_id).limit(1)) is not None:
            # Preserve the completed Publish and recipient graph together with
            # its append-only handoff history in the disposable host.
            return
        session.execute(Publish.__table__.delete().where(Publish.id == publish_id))
        session.execute(TaskPersonLink.__table__.delete().where(TaskPersonLink.task_id == recipient_task_id))
        session.execute(ProjectTaskTypeLink.__table__.delete().where(
            ProjectTaskTypeLink.task_type_id == task_type.id,
        ))
        session.execute(DepartmentLink.__table__.delete().where(
            DepartmentLink.department_id == department_id,
        ))
        session.execute(ProjectPersonLink.__table__.delete().where(
            ProjectPersonLink.project_id == production_id,
            ProjectPersonLink.person_id == recipient_id,
        ))
        target_task_ids = session.scalars(
            select(Task.id).where(Task.task_type_id == task_type.id)
        ).all()
        if target_task_ids:
            session.execute(Task.__table__.delete().where(Task.id.in_(target_task_ids)))
        session.execute(TaskType.__table__.delete().where(TaskType.id == task_type.id))
        session.execute(Department.__table__.delete().where(Department.id == department_id))


def test_department_handoff_http_options_create_replay_inbox_and_revocation(
    department_handoff_http, zou_db_factory, monkeypatch,
):
    from sqlalchemy import func, select
    from zou.app.models.person import DepartmentLink
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import TaskPersonLink

    data = department_handoff_http
    sender, recipient = data["sender"], data["recipient"]
    class FakeStorage:
        def get(self, object_id):
            assert object_id.startswith("synthetic-artifact-")
            return SimpleNamespace(trashed=False, parent_id="synthetic-publish-folder")

        def assert_parent_within_root(self, parent_id):
            assert parent_id == "synthetic-publish-folder"

        def verify(self, object_id, byte_count, sha256_hex):
            return object_id.startswith("synthetic-artifact-") and byte_count == len(data["artifact"]) and sha256_hex == data["artifact_sha256"]

        def download_to(self, object_id, target):
            target.write(data["artifact"])
            return len(data["artifact"]), data["artifact_sha256"]

    # The HTTP route is registered from Zou's installed plugin package, while
    # this repository's `plugin.resources` module contains the same source for
    # direct test imports. Patch the runtime module that owns the route.
    monkeypatch.setattr("vfx_files.resources._storage_adapter", lambda _connection: FakeStorage())
    publish_id = data["publish_id"]
    target_task_id = data["recipient_task_id"]
    production_id = data["production_id"]

    options = sender.get(f"/plugins/vfx_files/tasks/{data['source_task_id']}/publish-options")
    assert options.status_code == 200, options.get_data(as_text=True)
    target = next(item for item in options.json["department_targets"] if item["task_id"] == str(target_task_id))
    assert target["department_id"] == str(data["department_id"])
    assert target["eligible_recipient_count"] == 1
    assert target["eligible_recipients"][0]["id"] == str(data["recipient_id"])

    create_route = f"/plugins/vfx_files/publishes/{publish_id}/handoffs"
    body = {
        "recipient_department_id": str(data["department_id"]),
        "recipient_task_id": str(target_task_id),
        "recipient_person_id": str(data["recipient_id"]),
        "note": "Review the immutable version 7 render.",
    }
    created = sender.post(create_route, json=body, headers=data["headers"])
    assert created.status_code == 201, created.get_data(as_text=True)
    replay = sender.post(create_route, json=body, headers=data["headers"])
    assert replay.status_code == 200, replay.get_data(as_text=True)
    assert replay.json["id"] == created.json["id"]
    assert replay.json["replayed"] is True

    with zou_db_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(data["Handoff"])
            .where(data["Handoff"].id == UUID(created.json["id"]))
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(data["HandoffEvent"])
            .where(data["HandoffEvent"].handoff_id == UUID(created.json["id"]))
        ) == 1

    inbox = recipient.get(
        f"/plugins/vfx_files/overview?production_id={production_id}&task_id={target_task_id}"
    )
    assert inbox.status_code == 200, inbox.get_data(as_text=True)
    handoff = next(item for item in inbox.json["handoffs"] if item["id"] == created.json["id"])
    assert handoff["can_respond"] is True
    assert handoff["can_download"] is True
    assert handoff["publish_id"] == str(publish_id)
    assert handoff["publish_version"] == 7
    assert handoff["sender_note"] == body["note"]
    assert [event["type"] for event in handoff["events"]] == ["created"]
    download = recipient.get(f"/plugins/vfx_files/handoffs/{created.json['id']}/download")
    assert download.status_code == 200, download.get_data(as_text=True)
    assert download.data == data["artifact"]
    assert download.headers["Cache-Control"] == "no-store"

    # Membership is authoritative at response time, even for an inbox row
    # previously visible to the same authenticated identity.
    with zou_db_factory.begin() as session:
        session.execute(DepartmentLink.__table__.delete().where(
            DepartmentLink.department_id == data["department_id"],
            DepartmentLink.person_id == data["recipient_id"],
        ))
    denied = recipient.post(
        f"/plugins/vfx_files/handoffs/{created.json['id']}/respond",
        json={"target": "accepted"},
        headers={
            "Origin": "https://kitsu.test",
            "X-VFX-Files-CSRF": recipient.get("/plugins/vfx_files/csrf-token").json["csrf_token"],
            "Idempotency-Key": f"response-after-revocation:{uuid4()}",
        },
    )
    assert denied.status_code == 403
    assert recipient.get(f"/plugins/vfx_files/handoffs/{created.json['id']}/download").status_code == 403
    with zou_db_factory.begin() as session:
        session.add(DepartmentLink(
            department_id=data["department_id"], person_id=data["recipient_id"],
        ))
    for model, filters, replacement in (
        (TaskPersonLink, {"task_id": data["recipient_task_id"], "person_id": data["recipient_id"]}, TaskPersonLink(
            task_id=data["recipient_task_id"], person_id=data["recipient_id"],
        )),
        (ProjectPersonLink, {"project_id": data["production_id"], "person_id": data["recipient_id"]}, ProjectPersonLink(
            project_id=data["production_id"], person_id=data["recipient_id"], role="user",
        )),
    ):
        delete_membership = model.__table__.delete()
        for column, value in filters.items():
            delete_membership = delete_membership.where(getattr(model, column) == value)
        with zou_db_factory.begin() as session:
            session.execute(delete_membership)
        revoked = recipient.post(
            f"/plugins/vfx_files/handoffs/{created.json['id']}/respond",
            json={"target": "accepted"},
            headers={
                "Origin": "https://kitsu.test",
                "X-VFX-Files-CSRF": recipient.get("/plugins/vfx_files/csrf-token").json["csrf_token"],
                "Idempotency-Key": f"response-after-{model.__tablename__}:{uuid4()}",
            },
        )
        assert revoked.status_code == 403
        assert recipient.get(f"/plugins/vfx_files/handoffs/{created.json['id']}/download").status_code == 403
        with zou_db_factory.begin() as session:
            session.add(replacement)
    accepted = recipient.post(
        f"/plugins/vfx_files/handoffs/{created.json['id']}/respond",
        json={"target": "needs_fix", "feedback": "Please include the missing slate."},
        headers={
            "Origin": "https://kitsu.test",
            "X-VFX-Files-CSRF": recipient.get("/plugins/vfx_files/csrf-token").json["csrf_token"],
            "Idempotency-Key": f"response-needs-fix:{uuid4()}",
        },
    )
    assert accepted.status_code == 201, accepted.get_data(as_text=True)
    assert accepted.json["state"] == "needs_fix"
    with zou_db_factory() as session:
        events = session.scalars(
            select(data["HandoffEvent"])
            .where(data["HandoffEvent"].handoff_id == UUID(created.json["id"]))
            .order_by(data["HandoffEvent"].created_at, data["HandoffEvent"].id)
        ).all()
        assert [event.event_type for event in events] == ["created", "needs_fix"]
        assert events[-1].feedback == "Please include the missing slate."


def test_department_handoff_rejects_cross_production_and_empty_inbox(department_handoff_http, zou_db_factory):
    from uuid import uuid4

    from zou.app.models.department import Department
    from zou.app.models.entity import Entity
    from zou.app.models.person import DepartmentLink, Person
    from zou.app.models.project import Project, ProjectTaskTypeLink
    from zou.app.models.task import Task
    from zou.app.models.task_type import TaskType

    data = department_handoff_http
    with zou_db_factory.begin() as session:
        source = session.get(Task, data["source_task_id"])
        task_type = session.get(TaskType, session.get(Task, data["recipient_task_id"]).task_type_id)
        foreign_production = Project(
            name=f"Foreign {uuid4()}", team=[session.get(Person, data["sender_id"])],
        )
        unrelated_department = Department(name=f"Unrelated {uuid4().hex[:8]}", color="#64748B")
        session.add(unrelated_department)
        session.flush()
        unrelated_department_id = unrelated_department.id
        session.add(foreign_production)
        session.flush()
        foreign_entity = Entity(
            name=f"Foreign Shot {uuid4().hex[:8]}",
            project_id=foreign_production.id,
            entity_type_id=data["entity_type_id"],
        )
        session.add(foreign_entity)
        session.flush()
        foreign_entity_id = foreign_entity.id
        session.add(ProjectTaskTypeLink(
            project_id=foreign_production.id, task_type_id=task_type.id,
        ))
        foreign_task = Task(
            name="foreign-recipient", project_id=foreign_production.id,
            task_type_id=task_type.id, entity_id=foreign_entity.id,
        )
        session.add(foreign_task)
        session.flush()
        foreign_task_id = foreign_task.id
        foreign_production_id = foreign_production.id

    wrong_production = data["sender"].post(
        f"/plugins/vfx_files/publishes/{data['publish_id']}/handoffs",
        json={
            "recipient_department_id": str(data["department_id"]),
            "recipient_task_id": str(foreign_task_id),
        },
        headers={**data["headers"], "Idempotency-Key": f"foreign:{uuid4()}"},
    )
    assert wrong_production.status_code == 403, wrong_production.get_data(as_text=True)

    wrong_department = data["sender"].post(
        f"/plugins/vfx_files/publishes/{data['publish_id']}/handoffs",
        json={
            "recipient_department_id": str(unrelated_department_id),
            "recipient_task_id": str(data["recipient_task_id"]),
        },
        headers={**data["headers"], "Idempotency-Key": f"wrong-department:{uuid4()}"},
    )
    assert wrong_department.status_code == 403, wrong_department.get_data(as_text=True)

    with zou_db_factory.begin() as session:
        session.execute(DepartmentLink.__table__.delete().where(
            DepartmentLink.department_id == data["department_id"],
        ))
    options = data["sender"].get(
        f"/plugins/vfx_files/tasks/{data['source_task_id']}/publish-options"
    )
    assert options.status_code == 200
    target = next(item for item in options.json["department_targets"] if item["task_id"] == str(data["recipient_task_id"]))
    assert target["eligible_recipient_count"] == 0
    no_recipient = data["sender"].post(
        f"/plugins/vfx_files/publishes/{data['publish_id']}/handoffs",
        json={
            "recipient_department_id": str(data["department_id"]),
            "recipient_task_id": str(data["recipient_task_id"]),
        },
        headers={**data["headers"], "Idempotency-Key": f"empty:{uuid4()}"},
    )
    assert no_recipient.status_code == 409
    with zou_db_factory.begin() as session:
        session.execute(ProjectTaskTypeLink.__table__.delete().where(
            ProjectTaskTypeLink.project_id == foreign_production_id,
            ProjectTaskTypeLink.task_type_id == task_type.id,
        ))
        session.execute(Task.__table__.delete().where(Task.id == foreign_task_id))
        session.execute(Entity.__table__.delete().where(Entity.id == foreign_entity_id))
        session.execute(Department.__table__.delete().where(Department.id == unrelated_department_id))
        foreign = session.get(Project, foreign_production_id)
        if foreign is not None:
            foreign.team.clear()
            session.flush()
            session.delete(foreign)


def test_lighting_compositing_editorial_department_handoff_chain(department_handoff_http, zou_db_factory):
    from zou.app.models.department import Department
    from zou.app.models.person import DepartmentLink
    from zou.app.models.project import ProjectTaskTypeLink
    from zou.app.models.task import Task, TaskPersonLink
    from zou.app.models.task_type import TaskType

    from plugin.models import Publish

    data = department_handoff_http
    stages = {}
    with zou_db_factory.begin() as session:
        source = session.get(Task, data["source_task_id"])
        source_type = session.get(TaskType, source.task_type_id)
        lighting = Department(name=f"Lighting {uuid4().hex[:8]}", color="#64748B")
        compositing = Department(name=f"Compositing {uuid4().hex[:8]}", color="#64748B")
        editorial = Department(name=f"Editorial {uuid4().hex[:8]}", color="#64748B")
        session.add_all([lighting, compositing, editorial])
        session.flush()
        source_type.department_id = lighting.id
        comp_type = TaskType(
            name=f"Compositing {uuid4().hex[:8]}", for_entity="Shot", department_id=compositing.id,
        )
        editorial_type = TaskType(
            name=f"Editorial {uuid4().hex[:8]}", for_entity="Shot", department_id=editorial.id,
        )
        session.add_all([comp_type, editorial_type])
        session.flush()
        comp_task = Task(
            name="Compositing", project_id=data["production_id"],
            task_type_id=comp_type.id, entity_id=source.entity_id,
        )
        editorial_task = Task(
            name="Editorial", project_id=data["production_id"],
            task_type_id=editorial_type.id, entity_id=source.entity_id,
        )
        session.add_all([comp_task, editorial_task])
        session.flush()
        session.add_all([
            ProjectTaskTypeLink(project_id=data["production_id"], task_type_id=source_type.id),
            ProjectTaskTypeLink(project_id=data["production_id"], task_type_id=comp_type.id),
            ProjectTaskTypeLink(project_id=data["production_id"], task_type_id=editorial_type.id),
            TaskPersonLink(task_id=comp_task.id, person_id=data["recipient_id"]),
            TaskPersonLink(task_id=editorial_task.id, person_id=data["recipient_id"]),
            DepartmentLink(department_id=compositing.id, person_id=data["recipient_id"]),
            DepartmentLink(department_id=editorial.id, person_id=data["recipient_id"]),
        ])
        comp_publish_id = uuid4()
        session.add(Publish(
            id=comp_publish_id, task_id=comp_task.id, output_family_id=uuid4(), version=2,
            created_by=data["sender_id"], state="completed", qc_state="passed",
            manifest_sha256="c" * 64,
        ))
        stages = {
            "compositing": (compositing.id, comp_task.id, comp_publish_id),
            "editorial": (editorial.id, editorial_task.id, None),
        }

    def create_and_respond(
        publish_id, department_id, recipient_task_id, response, feedback=None,
        designated_person=True,
    ):
        key = f"chain:{uuid4()}"
        payload = {
            "recipient_department_id": str(department_id),
            "recipient_task_id": str(recipient_task_id),
        }
        if designated_person:
            payload["recipient_person_id"] = str(data["recipient_id"])
        created = data["sender"].post(
            f"/plugins/vfx_files/publishes/{publish_id}/handoffs",
            json=payload,
            headers={**data["headers"], "Idempotency-Key": key},
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        inbox = data["recipient"].get(
            f"/plugins/vfx_files/overview?production_id={data['production_id']}&task_id={recipient_task_id}"
        )
        assert inbox.status_code == 200, inbox.get_data(as_text=True)
        row = next(item for item in inbox.json["handoffs"] if item["id"] == created.json["id"])
        assert row["can_respond"] is True
        assert row["response_ownership"] == (
            "designated_person" if designated_person else "first_authorized_responder"
        )
        responded = data["recipient"].post(
            f"/plugins/vfx_files/handoffs/{created.json['id']}/respond",
            json={"target": response, "feedback": feedback},
            headers={
                "Origin": "https://kitsu.test",
                "X-VFX-Files-CSRF": data["recipient"].get("/plugins/vfx_files/csrf-token").json["csrf_token"],
                "Idempotency-Key": f"response:{uuid4()}",
            },
        )
        assert responded.status_code == 201, responded.get_data(as_text=True)
        assert responded.json["state"] == response
        return created.json["id"]

    first = create_and_respond(
        data["publish_id"], *stages["compositing"][:2], "accepted",
        designated_person=False,
    )
    second = create_and_respond(
        stages["compositing"][2], *stages["editorial"][:2], "needs_fix",
        feedback="Please add the final slate.",
    )
    assert first != second

