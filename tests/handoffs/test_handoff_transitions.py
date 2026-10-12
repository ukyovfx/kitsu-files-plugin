from uuid import uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from plugin.handoffs.service import HandoffDenied, create_handoff, transition_handoff
from plugin.models import Handoff, HandoffEvent, Publish
from plugin.publishes.checkpoints import PublishConflict


def idempotency_key(name):
    return f"{name}:{uuid4()}"


@pytest.fixture
def handoff_environment(zou_db_factory):
    factory = zou_db_factory
    task_id, publish_id = uuid4(), uuid4()
    sender, recipient = uuid4(), uuid4()
    with factory.begin() as session:
        session.add(Publish(id=publish_id, task_id=task_id, output_family_id=uuid4(), version=1, created_by=sender, state="completed", qc_state="passed", manifest_sha256="c" * 64))
    yield factory, task_id, publish_id, sender, recipient
    # Audit rows are immutable; the disposable integration DB retains fixtures.


def test_handoff_accept_and_needs_fix_are_idempotent(handoff_environment):
    factory, task_id, publish_id, sender, recipient = handoff_environment
    create_key = idempotency_key("handoff:create")
    accept_key = idempotency_key("handoff:accepted")
    handoff = create_handoff(factory, task_id=task_id, publish_id=publish_id, sender_id=sender, recipient_id=recipient, idempotency_key=create_key)
    accepted = transition_handoff(factory, actor_id=recipient, handoff_id=handoff.id, target="accepted", feedback=None, idempotency_key=accept_key)
    repeated = transition_handoff(factory, actor_id=recipient, handoff_id=handoff.id, target="accepted", feedback=None, idempotency_key=accept_key)
    assert accepted.state == repeated.state == "accepted"
    with pytest.raises(PublishConflict):
        transition_handoff(factory, actor_id=recipient, handoff_id=handoff.id, target="needs_fix", feedback="please revise", idempotency_key=idempotency_key("handoff:fix"))


def test_handoff_sender_cannot_respond_and_task_status_is_not_part_of_transition(handoff_environment):
    factory, task_id, publish_id, sender, recipient = handoff_environment
    handoff = create_handoff(factory, task_id=task_id, publish_id=publish_id, sender_id=sender, recipient_id=recipient, idempotency_key=idempotency_key("handoff:create"))
    with pytest.raises(HandoffDenied):
        transition_handoff(factory, actor_id=sender, handoff_id=handoff.id, target="accepted", feedback=None, idempotency_key=idempotency_key("handoff:sender"))
    result = transition_handoff(factory, actor_id=recipient, handoff_id=handoff.id, target="needs_fix", feedback="Update the render notes", idempotency_key=idempotency_key("handoff:fix"))
    assert result.state == "needs_fix"


def test_handoff_needs_fix_requires_feedback(handoff_environment):
    factory, task_id, publish_id, sender, recipient = handoff_environment
    handoff = create_handoff(factory, task_id=task_id, publish_id=publish_id, sender_id=sender, recipient_id=recipient, idempotency_key=idempotency_key("handoff:create"))
    with pytest.raises(ValueError):
        transition_handoff(factory, actor_id=recipient, handoff_id=handoff.id, target="needs_fix", feedback=None, idempotency_key=idempotency_key("handoff:fix"))


def test_department_handoff_targets_exact_task_and_requires_live_authorizer(handoff_environment):
    factory, task_id, publish_id, sender, recipient = handoff_environment
    department_id, receiving_task_id = uuid4(), uuid4()
    first = create_handoff(
        factory, task_id=task_id, publish_id=publish_id, sender_id=sender,
        recipient_department_id=department_id, recipient_task_id=receiving_task_id,
        recipient_person_id=recipient, idempotency_key=idempotency_key("handoff:department"),
    )
    duplicate = create_handoff(
        factory, task_id=task_id, publish_id=publish_id, sender_id=sender,
        recipient_department_id=department_id, recipient_task_id=receiving_task_id,
        recipient_person_id=recipient, idempotency_key=idempotency_key("handoff:department-retry"),
    )
    assert duplicate.id == first.id
    with pytest.raises(HandoffDenied):
        transition_handoff(factory, actor_id=recipient, handoff_id=first.id, target="accepted", feedback=None, idempotency_key=idempotency_key("handoff:department-deny"))
    accepted = transition_handoff(
        factory, actor_id=recipient, handoff_id=first.id, target="accepted", feedback=None,
        idempotency_key=idempotency_key("handoff:department-accept"),
        authorize_department_recipient=lambda _session, _record, _actor: True,
    )
    assert accepted.state == "accepted"
