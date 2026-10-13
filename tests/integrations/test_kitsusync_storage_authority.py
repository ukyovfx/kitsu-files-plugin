from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("zou")


def _rows(*, desired=True, connection=True, selected=True, root="root-01", owner="owner-01"):
    production_id, connection_id = uuid4(), uuid4()
    desired_row = SimpleNamespace(
        desired_config={
            "connection_id": str(connection_id if selected else uuid4()),
            "drive": {
                "account_mode": "my_drive", "owner_subject": owner,
                "root_id": root, "root_state": "verified",
            },
        },
    ) if desired else None
    connection_row = SimpleNamespace(
        id=connection_id, production_id=production_id, enabled=True,
        provider="google_drive", allowed_root_id="root-01",
        account_mode="my_drive", owner_subject="owner-01", drive_id=None,
    ) if connection else None

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            return desired_row if model.__name__ == "KitsuSyncDesiredConfiguration" else connection_row

    return production_id, connection_row, Session()


def test_plugin_storage_resolution_requires_kitsusync_selected_verified_root():
    # Resolve the pinned Zou model registry before compiling its ORM select.
    from zou.app.models.asset_instance import AssetInstance  # noqa: F401
    from zou.app.models.person import Person  # noqa: F401
    from plugin.integrations.storage_authority import selected_storage_connection

    production_id, connection, session = _rows()
    assert selected_storage_connection(session, production_id) is connection

    production_id, _, session = _rows(desired=False)
    assert selected_storage_connection(session, production_id) is None

    production_id, _, session = _rows(selected=False)
    assert selected_storage_connection(session, production_id) is None

    production_id, _, session = _rows(root="different-root")
    assert selected_storage_connection(session, production_id) is None

    production_id, _, session = _rows(owner="different-owner")
    assert selected_storage_connection(session, production_id) is None

    production_id, _, session = _rows(connection=False)
    assert selected_storage_connection(session, production_id) is None
