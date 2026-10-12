import hashlib
import hmac

import pytest

from plugin.integrations.v1 import (
    ContractValidationError,
    HierarchySnapshot,
    SnapshotConflict,
    canonical_json,
    signed_event_headers,
    validate_event_batch,
    validate_hierarchy,
)
from plugin.integrations.outbox import EventIdentityConflict, append_event_batch


def test_canonical_json_uses_rfc8785_key_order_and_number_rendering():
    assert canonical_json({"z": 1.0, "a": "é", "b": 1e-6, "c": 1e21}) == (
        b'{"a":"\xc3\xa9","b":0.000001,"c":1e+21,"z":1}'
    )


def test_snapshot_pages_are_bounded_idempotent_sorted_and_digest_checked():
    objects = [
        {"id": "10000000-0000-4000-8000-000000000001", "object_type": "production", "parent_id": None, "name": "Synthetic", "code": "SYN", "included_in_sync": True},
        {"id": "40000000-0000-4000-8000-000000000001", "object_type": "shot", "parent_id": "10000000-0000-4000-8000-000000000001", "name": "shot-01", "included_in_sync": True},
    ]
    digest = hashlib.sha256(canonical_json(objects)).hexdigest()
    page_digest = hashlib.sha256(canonical_json(objects)).hexdigest()
    snapshot = HierarchySnapshot(page_count=1, object_count=2, full_sha256=digest)
    first = snapshot.put_page(0, objects, page_digest)
    assert snapshot.put_page(0, objects, page_digest) == first
    assert snapshot.complete() == objects


def test_published_kitsusync_hierarchy_fixture_digest_is_compatible():
    objects = [
        {"object_type": "production", "id": "10000000-0000-4000-8000-000000000001", "parent_id": None, "name": "Synthetic Production", "code": "SYN", "included_in_sync": True},
        {"object_type": "sequence", "id": "30000000-0000-4000-8000-000000000001", "parent_id": "10000000-0000-4000-8000-000000000001", "name": "sc01", "code": "sc01", "included_in_sync": True},
        {"object_type": "shot", "id": "40000000-0000-4000-8000-000000000001", "parent_id": "30000000-0000-4000-8000-000000000001", "name": "sc01_sh005", "code": "sc01_sh005", "included_in_sync": True},
        {"object_type": "task", "id": "50000000-0000-4000-8000-000000000002", "parent_id": "40000000-0000-4000-8000-000000000001", "name": "Compositing", "task_type_id": "50000000-0000-4000-8000-000000000010", "included_in_sync": True},
        {"object_type": "task_type", "id": "50000000-0000-4000-8000-000000000010", "parent_id": None, "name": "Compositing", "included_in_sync": False},
    ]
    expected = "87f3531099ec79709c5d0bb1e6dc9e288495219f43b29260bb316df642af8e58"
    assert hashlib.sha256(canonical_json(objects)).hexdigest() == expected
    snapshot = HierarchySnapshot(page_count=1, object_count=5, full_sha256=expected)
    snapshot.put_page(0, objects, expected)
    assert len(snapshot.complete()) == 5


def test_snapshot_incomplete_or_mismatched_page_fails_closed():
    objects = [{"id": "10000000-0000-4000-8000-000000000001", "object_type": "production", "parent_id": None, "name": "Synthetic", "included_in_sync": True}]
    snapshot = HierarchySnapshot(page_count=1, object_count=1, full_sha256=hashlib.sha256(canonical_json(objects)).hexdigest())
    with pytest.raises(ContractValidationError, match="incomplete"):
        snapshot.complete()
    with pytest.raises(ContractValidationError, match="checksum"):
        snapshot.put_page(0, [], "0" * 64)


def test_snapshot_page_limit_is_enforced_before_hashing():
    snapshot = HierarchySnapshot(page_count=1, object_count=1, full_sha256="0" * 64)
    with pytest.raises(ContractValidationError, match="page size"):
        snapshot.put_page(0, [{}] * 501, "0" * 64)


def test_snapshot_duplicate_page_with_different_content_is_security_conflict():
    one = [{"id": "10000000-0000-4000-8000-000000000001", "object_type": "production", "parent_id": None, "name": "One", "included_in_sync": True}]
    two = [{"id": "10000000-0000-4000-8000-000000000002", "object_type": "production", "parent_id": None, "name": "Two", "included_in_sync": True}]
    digest = hashlib.sha256(canonical_json(one)).hexdigest()
    snapshot = HierarchySnapshot(page_count=1, object_count=1, full_sha256=digest)
    snapshot.put_page(0, one, digest)
    with pytest.raises(SnapshotConflict):
        snapshot.put_page(0, two, hashlib.sha256(canonical_json(two)).hexdigest())


def test_hierarchy_rejects_duplicate_unsorted_and_missing_parents():
    obj = {"id": "40000000-0000-4000-8000-000000000001", "object_type": "shot", "parent_id": None, "name": "shot", "included_in_sync": True}
    with pytest.raises(ContractValidationError, match="duplicate"):
        validate_hierarchy([obj, obj])
    with pytest.raises(ContractValidationError, match="outside"):
        validate_hierarchy([{**obj, "parent_id": "10000000-0000-4000-8000-000000000001"}])


def test_hierarchy_optional_updated_at_and_required_parent_field():
    production = {"id": "10000000-0000-4000-8000-000000000001", "object_type": "production", "parent_id": None, "name": "Synthetic", "included_in_sync": True, "updated_at": "2026-10-12T12:00:00Z"}
    assert validate_hierarchy([production]) == [production]
    with pytest.raises(ContractValidationError, match="required"):
        validate_hierarchy([{key: value for key, value in production.items() if key != "parent_id"}])


def test_hierarchy_parent_cycles_are_rejected():
    a = {"id": "10000000-0000-4000-8000-000000000001", "object_type": "production", "parent_id": "40000000-0000-4000-8000-000000000001", "name": "A", "included_in_sync": True}
    b = {"id": "40000000-0000-4000-8000-000000000001", "object_type": "shot", "parent_id": "10000000-0000-4000-8000-000000000001", "name": "B", "included_in_sync": True}
    with pytest.raises(ContractValidationError, match="cycle"):
        validate_hierarchy([a, b])


def test_signed_event_hmac_matches_contract_exact_input():
    secret = b"s" * 32
    body = b'{"contract_version":"1.0","events":[]}'
    headers = signed_event_headers(key_id="key-a", secret=secret, timestamp="2026-10-12T12:00:00Z", nonce="n" * 16, raw_body=body, idempotency_key="batch-key-0000001")
    digest = hashlib.sha256(body).hexdigest()
    expected = hmac.new(secret, f"2026-10-12T12:00:00Z\n{'n' * 16}\n{digest}\nbatch-key-0000001".encode(), hashlib.sha256).hexdigest()
    assert headers["X-VFX-Content-SHA256"] == digest
    assert headers["X-VFX-Signature"] == f"sha256={expected}"


def test_event_batch_is_versioned_bounded_and_unique():
    event = {
        "contract_version": "1.0", "producer_id": "vfx-files-plugin",
        "event_id": "60000000-0000-4000-8000-000000000001",
        "event_type": "folder.provisioning.completed", "occurred_at": "2026-10-12T12:00:00Z",
        "production_id": "10000000-0000-4000-8000-000000000001", "activation_epoch": 1,
        "folder": {
            "object_type": "shot", "object_id": "40000000-0000-4000-8000-000000000001",
            "drive_file_id": "fake-folder-1", "binding_state": "ready",
        },
    }
    batch = {"contract_version": "1.0", "events": [event]}
    assert validate_event_batch(batch) == [event]
    with pytest.raises(ContractValidationError, match="unique"):
        validate_event_batch({"contract_version": "1.0", "events": [event, event]})
    with pytest.raises(ContractValidationError, match="contract version"):
        validate_event_batch({"contract_version": "2.0", "events": [event]})
    with pytest.raises(ContractValidationError, match="published schema"):
        validate_event_batch({"contract_version": "1.0", "events": [{**event, "share_url": "https://example.invalid"}]})


def test_event_outbox_is_idempotent_and_rejects_event_identity_reuse():
    class Repository:
        def __init__(self):
            self.rows = {}

        def get(self, event_id):
            return self.rows.get(event_id)

        def insert(self, *, production_id, event_id, event_type, payload, payload_sha256):
            row = type("OutboxRow", (), {})()
            row.production_id = production_id
            row.event_id = event_id
            row.event_type = event_type
            row.payload = payload
            row.payload_sha256 = payload_sha256
            self.rows[event_id] = row
            return row

    event = {
        "contract_version": "1.0", "producer_id": "vfx-files-plugin",
        "event_id": "60000000-0000-4000-8000-000000000001",
        "event_type": "folder.provisioning.completed", "occurred_at": "2026-10-12T12:00:00Z",
        "production_id": "10000000-0000-4000-8000-000000000001", "activation_epoch": 1,
        "folder": {"object_type": "shot", "object_id": "40000000-0000-4000-8000-000000000001", "drive_file_id": "fake-folder-1", "binding_state": "ready"},
    }
    repository = Repository()
    batch = {"contract_version": "1.0", "events": [event]}
    accepted = append_event_batch(repository, batch)
    duplicate = append_event_batch(repository, batch)
    assert accepted[0].outcome == "accepted"
    assert duplicate[0].outcome == "duplicate"
    assert duplicate[0].record is accepted[0].record
    changed = {**event, "folder": {**event["folder"], "drive_file_id": "fake-folder-2"}}
    with pytest.raises(EventIdentityConflict):
        append_event_batch(repository, {"contract_version": "1.0", "events": [changed]})


def test_contract_accepts_two_independent_delivery_receipts_for_one_package():
    package = {
        "id": "90000000-0000-4000-8000-000000000001", "version": 3,
        "classification": "Low-Res EXR Sequence", "manifest_sha256": "a" * 64,
        "manifest_ref": "manifest:90000000-0000-4000-8000-000000000001",
        "frame_range": {"start": 1001, "end": 1100, "step": 1, "rate_num": 24, "rate_den": 1},
        "objects": [{"drive_file_id": "fake-file-01", "classification": "frame_sequence", "size_bytes": 123456, "sha256": "b" * 64}],
    }
    def delivery(event_id, target_task_id, target_type_id, status="created"):
        return {
            "contract_version": "1.0", "producer_id": "vfx-files-plugin", "event_id": event_id,
            "event_type": "file_delivery.created", "occurred_at": "2026-10-12T12:00:00Z",
            "production_id": "10000000-0000-4000-8000-000000000001", "activation_epoch": 1,
            "entity": {"type": "shot", "id": "40000000-0000-4000-8000-000000000001", "name": "sc01_sh005"},
            "source": {"id": "50000000-0000-4000-8000-000000000002", "task_type_id": "50000000-0000-4000-8000-000000000010", "task_type_name": "Compositing"},
            "sender": {"id": "80000000-0000-4000-8000-000000000001", "display_name": "Synthetic Artist"},
            "output_package": package,
            "delivery": {"id": "a0000000-0000-4000-8000-000000000001" if target_task_id.endswith("020") else "a0000000-0000-4000-8000-000000000002", "revision": 1, "status": status, "recipient_task_id": target_task_id, "recipient_task_type_id": target_type_id, "recipient_task_type_name": "FX" if target_task_id.endswith("020") else "Layout"},
        }
    events = [
        delivery("60000000-0000-4000-8000-000000000001", "50000000-0000-4000-8000-000000000020", "50000000-0000-4000-8000-000000000021"),
        delivery("60000000-0000-4000-8000-000000000002", "50000000-0000-4000-8000-000000000030", "50000000-0000-4000-8000-000000000031"),
    ]
    assert validate_event_batch({"contract_version": "1.0", "events": events}) == events
    assert events[0]["output_package"] == events[1]["output_package"]
    assert events[0]["output_package"]["objects"] == events[1]["output_package"]["objects"]
    assert events[0]["delivery"]["id"] != events[1]["delivery"]["id"]
    assert events[0]["delivery"]["recipient_task_id"] != events[1]["delivery"]["recipient_task_id"]
