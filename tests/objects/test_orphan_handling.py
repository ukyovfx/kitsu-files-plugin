import pytest

pytest.importorskip("zou")
pytest.importorskip("sqlalchemy")

from sqlalchemy.orm import sessionmaker
from uuid import uuid4

from plugin.models import ReconciliationFinding
from plugin.objects.reconciliation import mark_orphaned


def test_missing_kitsu_entity_is_orphaned_without_relink(zou_db_factory):
    object_id = uuid4()
    factory = zou_db_factory
    calls = []
    finding_id = mark_orphaned(factory, kitsu_object_id=object_id, object_type="task", exists_by_id=lambda value: calls.append(value) or False)
    with factory() as session:
        finding = session.get(ReconciliationFinding, finding_id)
        assert finding.kitsu_object_id == object_id
        assert finding.state == "orphaned"
        assert finding.evidence["name_relink_attempted"] is False
        assert calls == [object_id]
    with factory.begin() as session:
        session.execute(ReconciliationFinding.__table__.delete().where(ReconciliationFinding.id == finding_id))
