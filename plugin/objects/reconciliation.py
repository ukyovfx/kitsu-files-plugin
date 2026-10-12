from uuid import uuid4

from sqlalchemy import select

from ..models import ReconciliationFinding


def mark_orphaned(session_factory, *, kitsu_object_id, object_type: str, exists_by_id):
    """Record an orphan only after exact-ID lookup; never relink by display name."""
    if exists_by_id(kitsu_object_id):
        return None
    with session_factory.begin() as session:
        finding = session.scalar(
            select(ReconciliationFinding).where(
                ReconciliationFinding.object_type == object_type,
                ReconciliationFinding.kitsu_object_id == kitsu_object_id,
            ).with_for_update()
        )
        if finding is not None:
            return finding.id
        finding = ReconciliationFinding(
            id=uuid4(),
            object_type=object_type,
            kitsu_object_id=kitsu_object_id,
            state="orphaned",
            evidence={"identity_source": "stable_kitsu_uuid", "name_relink_attempted": False},
        )
        session.add(finding)
        session.flush()
        return finding.id
