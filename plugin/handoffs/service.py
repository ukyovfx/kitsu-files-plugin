from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select

from ..models import Handoff, HandoffEvent, Publish
from ..publishes.checkpoints import PublishConflict


class HandoffDenied(Exception):
    pass


@dataclass(frozen=True)
class HandoffRef:
    id: UUID
    state: str
    publish_id: UUID


def create_handoff(session_factory, *, task_id: UUID, publish_id: UUID, sender_id: UUID, recipient_id: UUID, idempotency_key: str):
    if sender_id == recipient_id:
        raise HandoffDenied("A person cannot hand off a Publish to themselves")
    with session_factory.begin() as session:
        existing = session.scalar(select(Handoff).where(Handoff.idempotency_key == idempotency_key))
        if existing is not None:
            if (existing.task_id, existing.publish_id, existing.sender_id, existing.recipient_id) == (task_id, publish_id, sender_id, recipient_id):
                return HandoffRef(existing.id, existing.state, existing.publish_id)
            raise PublishConflict("Handoff idempotency key is already used")
        publish = session.scalar(select(Publish).where(Publish.id == publish_id))
        if publish is None or publish.task_id != task_id or publish.state != "completed":
            raise PublishConflict("Handoff must reference a completed Publish for the same Task")
        handoff = Handoff(
            id=uuid4(),
            task_id=task_id,
            publish_id=publish_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            idempotency_key=idempotency_key,
            state="pending",
        )
        session.add(handoff)
        session.add(HandoffEvent(
            id=uuid4(),
            handoff_id=handoff.id,
            actor_id=sender_id,
            event_type="created",
            feedback=None,
            idempotency_key=f"{idempotency_key}:created",
        ))
        session.flush()
        return HandoffRef(handoff.id, handoff.state, handoff.publish_id)


def transition_handoff(session_factory, *, actor_id: UUID, handoff_id: UUID, target: str, feedback: str | None, idempotency_key: str):
    if target not in ("accepted", "needs_fix"):
        raise ValueError("Handoff target must be accepted or needs_fix")
    if target == "needs_fix" and not (feedback and feedback.strip()):
        raise ValueError("Needs Fix requires feedback")
    with session_factory.begin() as session:
        prior = session.scalar(select(HandoffEvent).where(HandoffEvent.idempotency_key == idempotency_key))
        if prior is not None:
            if prior.actor_id == actor_id and prior.event_type == target and prior.feedback == feedback:
                handoff = session.scalar(select(Handoff).where(Handoff.id == prior.handoff_id))
                return HandoffRef(handoff.id, handoff.state, handoff.publish_id)
            raise PublishConflict("Handoff idempotency key is already used")
        handoff = session.scalar(select(Handoff).where(Handoff.id == handoff_id).with_for_update())
        if handoff is None or handoff.recipient_id != actor_id:
            raise HandoffDenied("Only the designated recipient can respond")
        if handoff.state != "pending":
            raise PublishConflict("Handoff is already resolved")
        event = HandoffEvent(
            id=uuid4(),
            handoff_id=handoff.id,
            actor_id=actor_id,
            event_type=target,
            feedback=feedback,
            idempotency_key=idempotency_key,
        )
        session.add(event)
        handoff.state = target
        session.flush()
        return HandoffRef(handoff.id, handoff.state, handoff.publish_id)
