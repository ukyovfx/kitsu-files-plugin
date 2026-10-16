from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

from sqlalchemy import func, select
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


def test_handoff_response_idempotency_key_cannot_replay_against_another_handoff(handoff_environment):
    factory, task_id, publish_id, sender, recipient = handoff_environment
    first = create_handoff(
        factory, task_id=task_id, publish_id=publish_id, sender_id=sender,
        recipient_id=recipient, idempotency_key=idempotency_key("handoff:first"),
    )
    second = create_handoff(
        factory, task_id=task_id, publish_id=publish_id, sender_id=sender,
        recipient_id=uuid4(), idempotency_key=idempotency_key("handoff:second"),
    )
    response_key = idempotency_key("handoff:response")
    transition_handoff(
        factory, actor_id=recipient, handoff_id=first.id, target="accepted",
        feedback=None, idempotency_key=response_key,
    )

    with pytest.raises(PublishConflict):
        transition_handoff(
            factory, actor_id=recipient, handoff_id=second.id, target="accepted",
            feedback=None, idempotency_key=response_key,
        )


def test_department_handoff_note_and_create_replay_survive_terminal_response(handoff_environment):
    factory, task_id, publish_id, sender, recipient = handoff_environment
    department_id, recipient_task_id = uuid4(), uuid4()
    key = idempotency_key("handoff:department-note")
    handoff = create_handoff(
        factory, task_id=task_id, publish_id=publish_id, sender_id=sender,
        recipient_department_id=department_id, recipient_task_id=recipient_task_id,
        recipient_person_id=recipient, sender_note="Review the final edge detail",
        idempotency_key=key,
    )
    transition_handoff(
        factory, actor_id=recipient, handoff_id=handoff.id, target="accepted",
        feedback=None, idempotency_key=idempotency_key("handoff:department-note-accept"),
        authorize_department_recipient=lambda *_args: True,
    )

    replay = create_handoff(
        factory, task_id=task_id, publish_id=publish_id, sender_id=sender,
        recipient_department_id=department_id, recipient_task_id=recipient_task_id,
        recipient_person_id=recipient, sender_note="Review the final edge detail",
        idempotency_key=key,
    )
    assert replay.id == handoff.id
    assert replay.state == "accepted"
    assert replay.replayed is True
    with factory() as session:
        stored = session.get(Handoff, handoff.id)
        events = session.scalars(select(HandoffEvent).where(HandoffEvent.handoff_id == handoff.id)).all()
        assert stored.sender_note == "Review the final edge detail"
        assert [event.event_type for event in events].count("created") == 1
    with pytest.raises(PublishConflict):
        create_handoff(
            factory, task_id=task_id, publish_id=publish_id, sender_id=sender,
            recipient_department_id=department_id, recipient_task_id=recipient_task_id,
            recipient_person_id=recipient, sender_note="Changed request",
            idempotency_key=key,
        )


def test_concurrent_department_create_reuses_one_pending_handoff(handoff_environment):
    factory, task_id, publish_id, sender, recipient = handoff_environment
    department_id, recipient_task_id = uuid4(), uuid4()

    def create(index):
        return create_handoff(
            factory, task_id=task_id, publish_id=publish_id, sender_id=sender,
            recipient_department_id=department_id, recipient_task_id=recipient_task_id,
            recipient_person_id=recipient, idempotency_key=idempotency_key(f"handoff:concurrent:{index}"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(create, (1, 2)))
    assert first.id == second.id
    with factory() as session:
        count = session.scalar(select(func.count()).select_from(Handoff).where(Handoff.id == first.id))
        assert count == 1


def test_concurrent_department_responses_are_owned_by_first_committed_decision(handoff_environment):
    factory, task_id, publish_id, sender, recipient = handoff_environment
    department_id, recipient_task_id = uuid4(), uuid4()
    handoff = create_handoff(
        factory, task_id=task_id, publish_id=publish_id, sender_id=sender,
        recipient_department_id=department_id, recipient_task_id=recipient_task_id,
        idempotency_key=idempotency_key("handoff:concurrent-response-create"),
    )
    second_recipient = uuid4()

    def respond(args):
        actor_id, target = args
        try:
            return transition_handoff(
                factory, actor_id=actor_id, handoff_id=handoff.id, target=target,
                feedback="Please check the matte." if target == "needs_fix" else None,
                idempotency_key=idempotency_key(f"handoff:concurrent-response:{target}"),
                authorize_department_recipient=lambda *_args: True,
            ).state
        except PublishConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(respond, ((recipient, "accepted"), (second_recipient, "needs_fix"))))
    assert states.count("conflict") == 1
    assert len([state for state in states if state in {"accepted", "needs_fix"}]) == 1
    with factory() as session:
        stored = session.get(Handoff, handoff.id)
        events = session.scalars(
            select(HandoffEvent).where(HandoffEvent.handoff_id == handoff.id)
        ).all()
        assert stored.state in {"accepted", "needs_fix"}
        assert [event.event_type for event in events].count("created") == 1
        assert len([event for event in events if event.event_type in {"accepted", "needs_fix"}]) == 1


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
    with pytest.raises(HandoffDenied):
        transition_handoff(
            factory, actor_id=sender, handoff_id=first.id, target="accepted", feedback=None,
            idempotency_key=idempotency_key("handoff:department-self-accept"),
            authorize_department_recipient=lambda *_args: True,
        )
    accepted = transition_handoff(
        factory, actor_id=recipient, handoff_id=first.id, target="accepted", feedback=None,
        idempotency_key=idempotency_key("handoff:department-accept"),
        authorize_department_recipient=lambda _session, _record, _actor: True,
    )
    assert accepted.state == "accepted"
