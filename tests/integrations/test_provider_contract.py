import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugin.integrations.providers import (
    ProviderContractError,
    connection_payload,
    root_verification_payload,
    templates_payload,
)
from plugin.storage.base import PermissionFailure
from plugin.storage.fake import FakeStorage


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "kitsusync_v1"


def test_templates_match_the_published_contract_fixture():
    expected = json.loads((FIXTURE_DIR / "templates_shot.json").read_text(encoding="utf-8"))
    assert templates_payload("shot") == expected


def test_template_scope_is_validated_before_serialization():
    with pytest.raises(ProviderContractError) as error:
        templates_payload("department")
    assert error.value.code == "invalid_event"
    assert error.value.status == 400


def test_connection_response_never_contains_credential_references():
    connection = type("Connection", (), {
        "id": uuid4(),
        "account_mode": "my_drive",
        "owner_subject": "verified-oidc-subject",
        "drive_id": None,
        "enabled": True,
        "credential_ref": "must-never-be-returned",
    })()
    payload = connection_payload(connection)
    assert payload["state"] == "connected"
    assert "credential_ref" not in payload
    assert "owner_subject" not in payload
    assert "must-never-be-returned" not in json.dumps(payload)


def test_my_drive_root_verification_requires_verified_owner_subject():
    with pytest.raises(ProviderContractError) as error:
        root_verification_payload(
            connection_id=uuid4(), account_mode="my_drive", root_id="root",
            can_add_children=True, owner_subject=None,
        )
    assert error.value.code == "root_unverified"


def test_shared_drive_root_verification_uses_verified_drive_id_and_null_owner():
    connection_id = uuid4()
    payload = root_verification_payload(
        connection_id=connection_id, account_mode="shared_drive", root_id="folder-1",
        drive_id="drive-1", can_add_children=False, owner_subject=None,
    )
    assert payload == {
        "contract_version": "1.0", "connection_id": str(connection_id),
        "account_mode": "shared_drive", "root_id": "folder-1", "drive_id": "drive-1",
        "state": "verified", "capabilities": {"can_read": True, "can_add_children": False},
        "owner_subject": None,
    }


def test_fake_storage_root_listing_and_verification_stay_inside_synthetic_boundary():
    adapter = FakeStorage(root_id="synthetic-root-id", drive_id="synthetic-drive-id")
    listing = adapter.list_root_candidates()
    assert [item.object_id for item in listing.items] == ["synthetic-root-id"]
    assert adapter.verify_root_candidate("synthetic-root-id").root_visible is True
    with pytest.raises(PermissionFailure):
        adapter.verify_root_candidate("foreign-production-root")
