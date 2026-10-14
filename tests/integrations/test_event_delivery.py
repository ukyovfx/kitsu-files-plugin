import json
import hashlib
import hmac
from uuid import uuid4

from plugin.integrations.event_delivery import EventDeliveryConfig, dispatch_one
from plugin.integrations.v1 import validate_event_batch


def _event(event_id=None, epoch=4):
    return {
        "contract_version": "1.0",
        "producer_id": "vfx-files-plugin",
        "event_id": str(event_id or uuid4()),
        "event_type": "folder.provisioning.completed",
        "occurred_at": "2026-10-13T00:00:00Z",
        "production_id": str(uuid4()),
        "activation_epoch": epoch,
        "folder": {
            "object_type": "task",
            "object_id": str(uuid4()),
            "drive_file_id": "fake-folder-01",
            "binding_state": "ready",
        },
    }


class FakeOutbox:
    def __init__(self, payload, *, current=True):
        self.row = type("Row", (), {
            "event_id": payload["event_id"], "payload": payload,
            "production_id": payload["production_id"], "attempts": 1,
            "lease_generation": 1, "state": "leased",
        })()
        self.current = current
        self.finalized = []

    def claim_next(self, worker_id):
        row, self.row = self.row, None
        return row

    def is_current_activation(self, row):
        return self.current

    def finish(self, row, worker_id, state, *, response_code, retry_after_seconds=None):
        self.finalized.append((state, response_code, retry_after_seconds))
        return True


class FakeReceiver:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, body, headers):
        self.calls.append((url, body, dict(headers)))
        return self.response


class SyntheticKitsuSyncReceiver:
    """Contract-level receiver with a producer-bound synthetic HMAC key."""

    def __init__(self, *, key_id, secret):
        self.key_id = key_id
        self.secret = secret
        self.seen = {}
        self.calls = []

    def post(self, url, body, headers):
        self.calls.append((url, body, dict(headers)))
        if headers.get("X-VFX-Key-Id") != self.key_id:
            return 401, {"code": "unknown_producer_key"}
        digest = hashlib.sha256(body).hexdigest()
        if headers.get("X-VFX-Content-SHA256") != digest:
            return 401, {"code": "body_digest_mismatch"}
        signing_input = "\n".join((
            headers["X-VFX-Timestamp"], headers["X-VFX-Nonce"], digest,
            headers["Idempotency-Key"],
        )).encode("utf-8")
        expected = "sha256=" + hmac.new(self.secret, signing_input, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(headers.get("X-VFX-Signature", ""), expected):
            return 401, {"code": "signature_mismatch"}
        batch = json.loads(body)
        events = validate_event_batch(batch)
        if len(events) != 1 or headers["Idempotency-Key"] != events[0]["event_id"]:
            return 400, {"code": "idempotency_mismatch"}
        event = events[0]
        outcome = "duplicate" if event["event_id"] in self.seen else "accepted"
        self.seen[event["event_id"]] = digest
        return 202, {
            "contract_version": "1.0",
            "results": [{"event_id": event["event_id"], "outcome": outcome, "code": None}],
        }


def _config():
    return EventDeliveryConfig(
        endpoint="https://kitsusync.example.invalid/api/integrations/vfx-files/v1/events",
        key_id="vfx-files-prod-key-01", secret=b"synthetic-event-key-" * 3,
    )


def test_signed_delivery_uses_producer_key_and_contract_batch_and_confirms_event_result():
    payload = _event()
    outbox = FakeOutbox(payload)
    config = _config()
    receiver = SyntheticKitsuSyncReceiver(key_id=config.key_id, secret=config.secret)

    assert dispatch_one(outbox, receiver, config, worker_id=uuid4()) == "delivered"

    url, body, headers = receiver.calls[0]
    assert url == config.endpoint
    assert json.loads(body) == {"contract_version": "1.0", "events": [payload]}
    assert headers["X-VFX-Key-Id"] == "vfx-files-prod-key-01"
    assert headers["Idempotency-Key"] == payload["event_id"]
    assert headers["X-VFX-Content-SHA256"]
    assert headers["X-VFX-Signature"].startswith("sha256=")
    signed = "\n".join((
        headers["X-VFX-Timestamp"], headers["X-VFX-Nonce"],
        hashlib.sha256(body).hexdigest(), headers["Idempotency-Key"],
    )).encode("utf-8")
    expected_signature = "sha256=" + hmac.new(config.secret, signed, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(headers["X-VFX-Signature"], expected_signature)
    assert outbox.finalized == [("delivered", "202", None)]

    replay = FakeOutbox(payload)
    assert dispatch_one(replay, receiver, config, worker_id=uuid4()) == "delivered"
    assert replay.finalized == [("delivered", "202", None)]
    assert len(receiver.seen) == 1
    assert json.loads(receiver.calls[1][1])["events"][0]["event_id"] == payload["event_id"]


def test_matching_duplicate_is_success_but_receiver_quarantine_is_terminal():
    payload = _event()
    duplicate = FakeOutbox(payload)
    duplicate_receiver = FakeReceiver((202, {
        "contract_version": "1.0",
        "results": [{"event_id": payload["event_id"], "outcome": "duplicate", "code": None}],
    }))
    assert dispatch_one(duplicate, duplicate_receiver, _config(), worker_id=uuid4()) == "delivered"

    quarantined = FakeOutbox(payload)
    quarantine_receiver = FakeReceiver((202, {
        "contract_version": "1.0",
        "results": [{"event_id": payload["event_id"], "outcome": "quarantined", "code": "invalid_event"}],
    }))
    assert dispatch_one(quarantined, quarantine_receiver, _config(), worker_id=uuid4()) == "quarantined"
    assert quarantined.finalized == [("quarantined", "receiver_quarantined", None)]


def test_transient_failure_retries_with_bounded_backoff_and_auth_failure_quarantines():
    payload = _event()
    outbox = FakeOutbox(payload)
    receiver = FakeReceiver((503, {"contract_version": "1.0", "code": "provider_unavailable"}))
    assert dispatch_one(outbox, receiver, _config(), worker_id=uuid4()) == "retry"
    assert outbox.finalized[0][0] == "retry"
    assert 1 <= outbox.finalized[0][2] <= 3600

    rejected = FakeOutbox(payload)
    auth_receiver = FakeReceiver((401, {"contract_version": "1.0", "code": "unauthorized_service"}))
    assert dispatch_one(rejected, auth_receiver, _config(), worker_id=uuid4()) == "quarantined"
    assert rejected.finalized == [("quarantined", "http_401", None)]


def test_stale_activation_is_quarantined_without_network_io():
    outbox = FakeOutbox(_event(), current=False)
    receiver = FakeReceiver((202, {}))
    assert dispatch_one(outbox, receiver, _config(), worker_id=uuid4()) == "quarantined"
    assert receiver.calls == []
    assert outbox.finalized == [("quarantined", "activation_fence_changed", None)]


def test_202_without_exact_event_result_is_retried_not_acknowledged():
    payload = _event()
    outbox = FakeOutbox(payload)
    receiver = FakeReceiver((202, {"contract_version": "1.0", "results": []}))
    assert dispatch_one(outbox, receiver, _config(), worker_id=uuid4()) == "retry"
    assert outbox.finalized[0][0] == "retry"


def test_max_attempts_quarantines_and_stale_lease_cannot_be_acked():
    payload = _event()
    exhausted = FakeOutbox(payload)
    exhausted.row.attempts = 8
    receiver = FakeReceiver((503, {}))
    assert dispatch_one(exhausted, receiver, _config(), worker_id=uuid4()) == "quarantined"
    assert exhausted.finalized == [("quarantined", "http_503", None)]

    stale = FakeOutbox(payload)
    stale.finish = lambda *_args, **_kwargs: False
    accepted = FakeReceiver((202, {
        "contract_version": "1.0",
        "results": [{"event_id": payload["event_id"], "outcome": "accepted", "code": None}],
    }))
    assert dispatch_one(stale, accepted, _config(), worker_id=uuid4()) == "lease_lost"


def test_event_key_file_must_be_private_and_endpoint_must_match_contract():
    from plugin.integrations.event_delivery import load_event_delivery_config

    assert load_event_delivery_config({}) is None
    try:
        EventDeliveryConfig("http://127.0.0.1/api/integrations/vfx-files/v1/events", "test-key", b"x" * 32)
        raise AssertionError("HTTP event endpoints must be rejected")
    except ValueError:
        pass

    import pytest

    with pytest.raises(RuntimeError, match="protected"):
        load_event_delivery_config({
            "VFX_FILES_KITSUSYNC_EVENT_ENDPOINT": _config().endpoint,
            "VFX_FILES_KITSUSYNC_EVENT_KEY_ID": "synthetic-key-01",
            "VFX_FILES_KITSUSYNC_EVENT_HMAC_KEY_FILE": "missing-key-file",
        })


def test_event_key_file_reads_only_private_regular_file(tmp_path):
    import os
    import pytest
    from plugin.integrations.event_delivery import load_event_delivery_config

    if os.name != "posix":
        pytest.skip("This test verifies POSIX mode bits; Windows ACL validation is not covered here")

    key_file = tmp_path / "event.key"
    key_file.write_bytes(b"synthetic-test-key-material-00000000000000000000")
    key_file.chmod(0o600)
    env = {
        "VFX_FILES_KITSUSYNC_EVENT_ENDPOINT": _config().endpoint,
        "VFX_FILES_KITSUSYNC_EVENT_KEY_ID": "synthetic-key-01",
        "VFX_FILES_KITSUSYNC_EVENT_HMAC_KEY_FILE": str(key_file),
    }
    loaded = load_event_delivery_config(env)
    assert loaded is not None
    assert len(loaded.secret) >= 32

    key_file.chmod(0o644)
    with pytest.raises(RuntimeError, match="protected"):
        load_event_delivery_config(env)
