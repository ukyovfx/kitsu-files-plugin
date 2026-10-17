from copy import deepcopy
from uuid import uuid4

import pytest

from plugin.integrations.share_v1_1 import ShareContractError, validate_file_share_batch
from plugin.shares.service import validate_share_request


def _event(*, recipient_id=None, target_id=None, event_id=None, object_id="drive_file_01"):
    recipient_id = recipient_id or uuid4()
    target_id = target_id or uuid4()
    event_id = event_id or uuid4()
    return {
        "contract_version": "1.1", "producer_id": "vfx-files-plugin",
        "event_id": str(event_id), "event_type": "file_share.created",
        "occurred_at": "2026-10-17T01:02:03Z", "production_id": str(uuid4()),
        "activation_epoch": 7,
        "source": {"entity_type": "shot", "entity_id": str(uuid4()), "entity_name": "sh010", "task_id": str(uuid4()), "task_type_id": str(uuid4()), "task_type_name": "Compositing"},
        "recipient": {"task_id": str(recipient_id), "task_type_id": str(uuid4()), "task_type_name": "Lighting", "department_id": None, "department_name": None},
        "share": {
            "id": "00000000-0000-4000-8000-000000000001", "share_target_id": str(target_id),
            "revision": 1, "shared_by_id": str(uuid4()), "shared_at": "2026-10-17T01:02:03Z",
            "idempotency_key": f"share-target-{target_id}-create-v1",
            "object": {"provider": "google_drive", "object_id": object_id, "object_type": "file", "url": f"https://drive.google.com/file/d/{object_id}/view"},
            "metadata": {"status": "complete", "observed_at": "2026-10-17T01:02:03Z", "name": "plate.exr", "mime_type": "image/x-exr", "size_bytes": 42, "modified_at": None, "sha256": None},
            "inventory": {"status": "unavailable", "observed_at": "2026-10-17T01:02:03Z", "scope": "object", "file_count": None, "observed_file_count_lower_bound": None, "frame_summary": None},
            "access": {"status": "verified_service_only", "access_verified_at": "2026-10-17T01:02:03Z", "check_subject_type": "plugin_service_identity", "check_subject_id": "connection-1"},
        },
    }


def test_valid_single_and_multi_recipient_batches_keep_shared_object_and_distinct_targets():
    first = _event()
    second = deepcopy(first)
    second["event_id"] = str(uuid4())
    second["recipient"]["task_id"] = str(uuid4())
    second["share"]["share_target_id"] = str(uuid4())
    second["share"]["idempotency_key"] = f"share-target-{uuid4()}-create-v1"

    assert validate_file_share_batch({"contract_version": "1.1", "events": [first]}) == [first]
    assert validate_file_share_batch({"contract_version": "1.1", "events": [first, second]}) == [first, second]


@pytest.mark.parametrize("mutate", [
    lambda event: event["share"]["object"].update(url="https://drive.google.com/file/d/other/view"),
    lambda event: event["share"]["object"].update(resource_key="secret-key"),
    lambda event: event["share"]["inventory"].update({"frame_summary": {"status": "complete", "frame_start": 1, "frame_end": 9, "missing_count": None, "duplicate_count": 0, "missing_samples": []}}),
    lambda event: event["share"]["inventory"].update({"frame_summary": {"status": "unavailable", "frame_start": None, "frame_end": None, "missing_count": None, "duplicate_count": None, "missing_samples": [1]}}),
])
def test_invalid_contract_semantics_fail_closed(mutate):
    event = _event()
    mutate(event)
    with pytest.raises(ShareContractError):
        validate_file_share_batch({"contract_version": "1.1", "events": [event]})


def test_same_share_cannot_duplicate_recipient_or_target():
    first = _event()
    duplicate_recipient = deepcopy(first)
    duplicate_recipient["event_id"] = str(uuid4())
    duplicate_recipient["share"]["share_target_id"] = str(uuid4())
    duplicate_recipient["share"]["idempotency_key"] = f"share-target-{uuid4()}-create-v1"
    with pytest.raises(ShareContractError):
        validate_file_share_batch({"contract_version": "1.1", "events": [first, duplicate_recipient]})


def test_share_request_requires_opaque_drive_id_unique_task_targets_and_key():
    recipient = uuid4()
    assert validate_share_request("drive_file_01", [str(recipient)], "share-request:12345678") == [recipient]
    for object_id, recipients, key in (
        ("https://drive.google.com/file/d/a/view", [str(recipient)], "share-request:12345678"),
        ("drive_file_01", [str(recipient), str(recipient)], "share-request:12345678"),
        ("drive_file_01", [str(recipient)], "short"),
    ):
        with pytest.raises(ValueError):
            validate_share_request(object_id, recipients, key)
