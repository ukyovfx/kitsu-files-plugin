import pytest

from plugin.integrations.kitsusync_contract import (
    AuthorizedProductionRoot,
    ContractConflict,
    FakeConnectionProvider,
    FakeEventOutbox,
)


def test_fake_connection_provider_returns_root_by_stable_production_id_only():
    production = "production-uuid-1"
    expected = AuthorizedProductionRoot(production, "google_drive", "drive-root-1", "shared-drive-1")
    provider = FakeConnectionProvider({production: expected})
    assert provider.authorized_root(production) == expected
    with pytest.raises(PermissionError):
        provider.authorized_root("other-production-uuid")


def test_fake_event_outbox_is_idempotent_and_rejects_key_reuse_with_new_content():
    outbox = FakeEventOutbox()
    first = outbox.append(
        idempotency_key="handoff:uuid-1:accepted",
        event_type="handoff.accepted",
        payload={"production_id": "production-uuid-1", "handoff_id": "uuid-1"},
    )
    replay = outbox.append(
        idempotency_key="handoff:uuid-1:accepted",
        event_type="handoff.accepted",
        payload={"handoff_id": "uuid-1", "production_id": "production-uuid-1"},
    )
    assert replay == first
    assert len(outbox.events) == 1
    with pytest.raises(ContractConflict):
        outbox.append(
            idempotency_key="handoff:uuid-1:accepted",
            event_type="handoff.accepted",
            payload={"production_id": "another-production", "handoff_id": "uuid-1"},
        )


def test_fake_event_outbox_rejects_non_serializable_payload():
    with pytest.raises(ValueError, match="JSON serializable"):
        FakeEventOutbox().append(idempotency_key="publish:1", event_type="publish.completed", payload={"bad": object()})
