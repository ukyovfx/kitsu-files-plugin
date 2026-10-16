"""Transactional Output Package delivery and recipient-response state."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select

from ..models import OutputPackage, OutputPackageDelivery, OutputPackageDeliveryEvent


class PackageDeliveryConflict(Exception):
    pass


class PackageDeliveryDenied(Exception):
    pass


@dataclass(frozen=True)
class PackageDeliveryResult:
    id: UUID
    package_id: UUID
    production_id: UUID
    recipient_task_id: UUID
    recipient_task_type_id: UUID
    revision: int
    state: str
    replayed: bool = False


def create_package_delivery(
    session_factory, *, package_id: UUID, recipient_task_id: UUID,
    recipient_task_type_id: UUID, sender_id: UUID, idempotency_key: str,
) -> PackageDeliveryResult:
    if not idempotency_key or len(idempotency_key) > 255:
        raise ValueError("A bounded delivery idempotency key is required")
    with session_factory.begin() as session:
        replay = _replay_create_request(
            session, idempotency_key=idempotency_key, package_id=package_id,
            recipient_task_id=recipient_task_id,
            recipient_task_type_id=recipient_task_type_id, sender_id=sender_id,
        )
        if replay is not None:
            return replay

        from zou.app.models.project import ProjectTaskTypeLink
        from zou.app.models.task import Task
        from zou.app.models.task_type import TaskType

        package = session.scalar(select(OutputPackage).where(
            OutputPackage.id == package_id,
        ).with_for_update())
        if package is None or package.state != "completed" or package.qc_state != "passed":
            raise PackageDeliveryConflict("Only a completed QC-passed Output Package can be delivered")
        # Locking the actual receiving Task serializes first delivery creation
        # even when no prior delivery stream row exists yet.
        recipient = session.scalar(select(Task).where(
            Task.id == recipient_task_id,
        ).with_for_update())
        # Another request using this key may have committed while we waited on
        # the package/recipient lock. Re-read after serialization so the loser
        # returns the committed result instead of a spurious stream conflict.
        replay = _replay_create_request(
            session, idempotency_key=idempotency_key, package_id=package_id,
            recipient_task_id=recipient_task_id,
            recipient_task_type_id=recipient_task_type_id, sender_id=sender_id,
        )
        if replay is not None:
            return replay
        source = session.get(Task, package.task_id)
        recipient_type = session.get(TaskType, recipient.task_type_id) if recipient is not None else None
        if (
            source is None or recipient is None or recipient_type is None
            or source.id == recipient.id
            or source.project_id != package.production_id
            or recipient.project_id != package.production_id
            or source.entity_id != package.entity_id
            or recipient.entity_id != package.entity_id
            or recipient.task_type_id != recipient_task_type_id
            or session.query(ProjectTaskTypeLink.id).filter_by(
                project_id=recipient.project_id,
                task_type_id=recipient.task_type_id,
            ).one_or_none() is None
        ):
            raise PackageDeliveryDenied("Recipient Task must be a different Task Type on the same Production entity")

        stream = session.scalar(select(OutputPackageDelivery).where(
            OutputPackageDelivery.production_id == package.production_id,
            OutputPackageDelivery.recipient_task_id == recipient.id,
        ).order_by(OutputPackageDelivery.delivery_revision.desc()).with_for_update().limit(1))
        if stream is None:
            delivery = OutputPackageDelivery(
                id=uuid4(), package_id=package.id, production_id=package.production_id,
                recipient_task_id=recipient.id, recipient_task_type_id=recipient.task_type_id,
                delivery_revision=1, state="available", idempotency_key=idempotency_key,
                created_by=sender_id,
            )
            event_type = "file_delivery.created"
            history_type = "delivered"
            session.add(delivery)
        else:
            if stream.state not in {"needs_fix", "accepted"}:
                raise PackageDeliveryConflict("Recipient already has an unresolved delivery")
            previous_package = session.get(OutputPackage, stream.package_id)
            if (
                previous_package is None or previous_package.id == package.id
                or previous_package.entity_id != package.entity_id
            ):
                raise PackageDeliveryConflict("A revised delivery requires a new Output Package version for the same entity")
            if package.version <= previous_package.version:
                raise PackageDeliveryConflict("A revised delivery must use a newer Output Package version")
            stream.package_id = package.id
            stream.delivery_revision += 1
            stream.state = "revised"
            stream.feedback = None
            stream.idempotency_key = idempotency_key
            stream.created_by = sender_id
            event_type = "file_delivery.revised"
            history_type = "revised"
            delivery = stream

        session.add(OutputPackageDeliveryEvent(
            delivery_id=delivery.id, actor_id=sender_id, event_type=history_type,
            feedback=None, idempotency_key=idempotency_key,
            request_operation="delivery_create", request_package_id=package.id,
            request_recipient_task_id=recipient.id,
            request_recipient_task_type_id=recipient.task_type_id,
            request_revision=delivery.delivery_revision,
        ))
        session.flush()
        from ..integrations.event_producers import append_file_delivery_event
        append_file_delivery_event(session, delivery, event_type=event_type)
        return _result(delivery)


def respond_to_package_delivery(
    session_factory, *, delivery_id: UUID, actor_id: UUID, target: str,
    feedback: str | None, idempotency_key: str,
) -> PackageDeliveryResult:
    if target not in {"accepted", "needs_fix"}:
        raise ValueError("Delivery response must be Accepted or Needs Fix")
    if not idempotency_key or len(idempotency_key) > 255:
        raise ValueError("A bounded delivery idempotency key is required")
    if feedback is not None and not isinstance(feedback, str):
        raise ValueError("Delivery feedback must be text")
    if target == "needs_fix" and (not isinstance(feedback, str) or not feedback.strip()):
        raise ValueError("Needs Fix requires feedback")
    if feedback is not None and (not isinstance(feedback, str) or len(feedback) > 2000):
        raise ValueError("Delivery feedback is too long")

    with session_factory.begin() as session:
        prior = session.scalar(select(OutputPackageDeliveryEvent).where(
            OutputPackageDeliveryEvent.idempotency_key == idempotency_key,
        ))
        delivery = session.scalar(select(OutputPackageDelivery).where(
            OutputPackageDelivery.id == delivery_id,
        ).with_for_update())
        if delivery is None:
            raise PackageDeliveryDenied("Delivery is unavailable")
        if prior is not None:
            if (
                prior.delivery_id != delivery.id or prior.actor_id != actor_id
                or prior.event_type != target
                or prior.feedback != (feedback.strip() if target == "needs_fix" and feedback else None)
            ):
                raise PackageDeliveryConflict("Delivery idempotency key is already used")
            return _result(delivery, replayed=True)
        if delivery.state not in {"available", "delivered", "in_review", "revised"}:
            raise PackageDeliveryConflict("Delivery is already resolved; create a newer package delivery to revise it")

        delivery.state = target
        delivery.feedback = feedback.strip() if target == "needs_fix" else None
        session.add(OutputPackageDeliveryEvent(
            delivery_id=delivery.id, actor_id=actor_id, event_type=target,
            feedback=delivery.feedback, idempotency_key=idempotency_key,
        ))
        session.flush()
        from ..integrations.event_producers import append_file_delivery_event
        append_file_delivery_event(
            session, delivery,
            event_type="file_delivery.accepted" if target == "accepted" else "file_delivery.needs_fix",
        )
        return _result(delivery)


def _result(delivery, *, replayed=False):
    return PackageDeliveryResult(
        id=delivery.id,
        package_id=delivery.package_id,
        production_id=delivery.production_id,
        recipient_task_id=delivery.recipient_task_id,
        recipient_task_type_id=delivery.recipient_task_type_id,
        revision=delivery.delivery_revision,
        state=delivery.state,
        replayed=replayed,
    )


def _replay_create_request(
    session, *, idempotency_key, package_id, recipient_task_id,
    recipient_task_type_id, sender_id,
):
    prior_event = session.scalar(select(OutputPackageDeliveryEvent).where(
        OutputPackageDeliveryEvent.idempotency_key == idempotency_key,
    ))
    if prior_event is None:
        return None
    delivery = session.get(OutputPackageDelivery, prior_event.delivery_id)
    # Legacy rows have no request identity. Never infer it from the mutable
    # delivery stream, which may now point at a later package/revision.
    if (
        delivery is None or prior_event.actor_id != sender_id
        or prior_event.event_type not in {"delivered", "revised"}
        or prior_event.request_operation != "delivery_create"
        or prior_event.request_package_id is None
        or prior_event.request_recipient_task_id is None
        or prior_event.request_recipient_task_type_id is None
        or prior_event.request_revision is None
        or prior_event.request_package_id != package_id
        or prior_event.request_recipient_task_id != recipient_task_id
        or prior_event.request_recipient_task_type_id != recipient_task_type_id
    ):
        raise PackageDeliveryConflict("Delivery idempotency key conflicts with the requested delivery")
    return _result(delivery, replayed=True)
