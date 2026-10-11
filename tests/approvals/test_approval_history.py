from uuid import uuid4

import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from plugin.approvals.service import ApprovalDenied, approve_publish, pin_dependency, revoke_approval
from plugin.models import ApprovalEvent, Publish, PublishPointer, PinnedDependency


@pytest.fixture
def approval_environment(zou_db_factory):
    factory = zou_db_factory
    task_id, family_id = uuid4(), uuid4()
    publish_rows = []
    for version in (1, 2):
        row = Publish(
            id=uuid4(), task_id=task_id, output_family_id=family_id, version=version,
            created_by=uuid4(), state="completed", qc_state="passed", manifest_sha256=(f"{version:064x}"),
        )
        publish_rows.append(row)
    with factory.begin() as session:
        session.add_all(publish_rows)
        session.add(PublishPointer(task_id=task_id, output_family_id=family_id))
    yield factory, publish_rows
    # Audit rows are immutable; the disposable integration DB retains fixtures.


def test_approval_binds_exact_publish_and_manifest_hash(approval_environment):
    factory, publishes = approval_environment
    row = publishes[0]
    reviewer = uuid4()
    approval_id = approve_publish(factory, actor_id=reviewer, actor_role="supervisor", publish_id=row.id, policy_id="review-v1", policy_sha256="a" * 64, idempotency_key=f"approve:{row.id}")
    with factory() as session:
        event = session.get(ApprovalEvent, approval_id)
        assert event.publish_id == row.id
        assert event.manifest_sha256 == row.manifest_sha256


def test_approval_and_revocation_are_append_only_and_latest_falls_back(approval_environment):
    factory, publishes = approval_environment
    first, latest = publishes
    reviewer = uuid4()
    old_approval = approve_publish(factory, actor_id=reviewer, actor_role="manager", publish_id=first.id, policy_id="review-v1", policy_sha256="a" * 64, idempotency_key=f"approve:{first.id}")
    latest_approval = approve_publish(factory, actor_id=reviewer, actor_role="manager", publish_id=latest.id, policy_id="review-v1", policy_sha256="a" * 64, idempotency_key=f"approve:{latest.id}")
    revoke_approval(factory, actor_id=uuid4(), actor_role="supervisor", approval_id=latest_approval, reason="QC evidence withdrawn", idempotency_key=f"revoke:{latest.id}")

    with factory() as session:
        pointer = session.get(PublishPointer, (first.task_id, first.output_family_id))
        events = session.scalars(select(ApprovalEvent).where(ApprovalEvent.publish_id == latest.id)).all()
        assert pointer.latest_approved_id == first.id
        assert len(events) == 2
        assert events[0].event_type == "approved"
        assert events[1].event_type == "revoked"
        with pytest.raises(DBAPIError):
            with factory.begin() as tx:
                tx.execute(ApprovalEvent.__table__.delete().where(ApprovalEvent.id == old_approval))


def test_self_approval_denied(approval_environment):
    factory, publishes = approval_environment
    row = publishes[0]
    with pytest.raises(ApprovalDenied):
        approve_publish(factory, actor_id=row.created_by, actor_role="manager", publish_id=row.id, policy_id="review-v1", policy_sha256="a" * 64, idempotency_key="self-approval")


def test_pinned_dependency_never_follows_latest(approval_environment):
    factory, publishes = approval_environment
    first, latest = publishes
    pin_id = pin_dependency(factory, task_id=uuid4(), publish_id=first.id, manifest_sha256=first.manifest_sha256, actor_id=uuid4())
    with factory() as session:
        pin = session.get(PinnedDependency, pin_id)
        assert pin.publish_id == first.id
        assert pin.publish_id != latest.id
