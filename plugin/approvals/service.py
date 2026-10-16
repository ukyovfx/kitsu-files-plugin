from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import aliased

from ..models import ApprovalEvent, Publish, PublishPointer, PinnedDependency
from ..publishes.checkpoints import PublishConflict


class ApprovalDenied(Exception):
    pass


def approve_publish(session_factory, *, actor_id: UUID, actor_role: str, publish_id: UUID, policy_id: str, policy_sha256: str, idempotency_key: str):
    if actor_role not in ("admin", "manager", "supervisor"):
        raise ApprovalDenied("Approval role is not authorized")
    _require_sha256(policy_sha256)
    with session_factory.begin() as session:
        prior = session.scalar(select(ApprovalEvent).where(ApprovalEvent.idempotency_key == idempotency_key))
        if prior is not None:
            if prior.publish_id == publish_id and prior.actor_id == actor_id and prior.event_type == "approved":
                return prior.id
            raise PublishConflict("Approval idempotency key is already used")
        publish = session.scalar(select(Publish).where(Publish.id == publish_id).with_for_update())
        if publish is None or publish.state != "completed" or publish.qc_state != "passed" or not publish.manifest_sha256:
            raise PublishConflict("Only a completed, QC-passed Publish with a manifest can be approved")
        if publish.created_by == actor_id:
            raise ApprovalDenied("Publishers cannot approve their own Publish")
        event = ApprovalEvent(
            id=uuid4(),
            publish_id=publish.id,
            actor_id=actor_id,
            event_type="approved",
            policy_id=policy_id,
            policy_sha256=policy_sha256,
            manifest_sha256=publish.manifest_sha256,
            idempotency_key=idempotency_key,
        )
        session.add(event)
        session.flush()
        _refresh_latest_approved(session, publish.task_id, publish.output_family_id)
        from ..packages.publication import refresh_package_publication_pointers
        refresh_package_publication_pointers(session, publish.task_id)
        return event.id


def revoke_approval(session_factory, *, actor_id: UUID, actor_role: str, approval_id: UUID, reason: str, idempotency_key: str):
    if actor_role not in ("admin", "manager", "supervisor"):
        raise ApprovalDenied("Approval role is not authorized")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError("A bounded revocation reason is required")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise ValueError("A bounded revocation idempotency key is required")
    reason = reason.strip()
    with session_factory.begin() as session:
        prior = session.scalar(select(ApprovalEvent).where(ApprovalEvent.idempotency_key == idempotency_key))
        if prior is not None:
            if (
                prior.reverses_event_id == approval_id and prior.actor_id == actor_id
                and prior.event_type == "revoked" and prior.reason == reason
            ):
                return prior.id
            raise PublishConflict("Revocation idempotency key is already used")
        # Lock the approval before checking for a prior revocation. Concurrent
        # requests with different keys then serialize on this immutable source
        # event and cannot append two revocations.
        original = session.scalar(select(ApprovalEvent).where(
            ApprovalEvent.id == approval_id, ApprovalEvent.event_type == "approved",
        ).with_for_update())
        if original is None:
            raise PublishConflict("Approval event does not exist")
        # The same idempotency key may have committed while this transaction
        # waited for the source approval lock.
        prior = session.scalar(select(ApprovalEvent).where(ApprovalEvent.idempotency_key == idempotency_key))
        if prior is not None:
            if (
                prior.reverses_event_id == approval_id and prior.actor_id == actor_id
                and prior.event_type == "revoked" and prior.reason == reason
            ):
                return prior.id
            raise PublishConflict("Revocation idempotency key is already used")
        revoked = session.scalar(select(ApprovalEvent).where(ApprovalEvent.reverses_event_id == approval_id))
        if revoked is not None:
            raise PublishConflict("Approval was already revoked")
        publish = session.scalar(select(Publish).where(Publish.id == original.publish_id).with_for_update())
        if publish is None:
            raise PublishConflict("Approved Publish does not exist")
        event = ApprovalEvent(
            id=uuid4(),
            publish_id=publish.id,
            actor_id=actor_id,
            event_type="revoked",
            reverses_event_id=original.id,
            policy_id=original.policy_id,
            policy_sha256=original.policy_sha256,
            manifest_sha256=original.manifest_sha256,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        session.add(event)
        session.flush()
        _refresh_latest_approved(session, publish.task_id, publish.output_family_id)
        from ..packages.publication import refresh_package_publication_pointers
        refresh_package_publication_pointers(session, publish.task_id)
        return event.id


def pin_dependency(session_factory, *, task_id: UUID, publish_id: UUID, manifest_sha256: str, actor_id: UUID):
    _require_sha256(manifest_sha256)
    with session_factory.begin() as session:
        publish = session.scalar(select(Publish).where(Publish.id == publish_id))
        if publish is None or publish.manifest_sha256 != manifest_sha256:
            raise PublishConflict("Pinned dependency must bind the exact manifest hash")
        pin = PinnedDependency(id=uuid4(), task_id=task_id, publish_id=publish_id, manifest_sha256=manifest_sha256, created_by=actor_id)
        session.add(pin)
        session.flush()
        return pin.id


def _refresh_latest_approved(session, task_id, output_family_id):
    pointer = session.scalar(
        select(PublishPointer)
        .where(PublishPointer.task_id == task_id, PublishPointer.output_family_id == output_family_id)
        .with_for_update()
    )
    if pointer is None:
        pointer = PublishPointer(task_id=task_id, output_family_id=output_family_id)
        session.add(pointer)
        session.flush()
    approval = aliased(ApprovalEvent)
    revoke = aliased(ApprovalEvent)
    eligible = session.scalar(
        select(Publish.id)
        .join(approval, approval.publish_id == Publish.id)
        .outerjoin(revoke, revoke.reverses_event_id == approval.id)
        .where(
            Publish.task_id == task_id,
            Publish.output_family_id == output_family_id,
            Publish.state == "completed",
            Publish.qc_state == "passed",
            Publish.manifest_sha256 == approval.manifest_sha256,
            approval.event_type == "approved",
            revoke.id.is_(None),
        )
        .order_by(Publish.version.desc(), approval.created_at.desc())
        .limit(1)
    )
    pointer.latest_approved_id = eligible


def _require_sha256(value):
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("SHA-256 must be 64 lowercase hexadecimal characters")
