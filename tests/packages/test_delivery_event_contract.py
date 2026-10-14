"""Contract tests for Output Package recipient delivery events."""

from types import SimpleNamespace
from uuid import UUID

from plugin.integrations.file_delivery_events import build_file_delivery_event
from plugin.integrations.v1 import validate_event_batch


def test_delivery_event_uses_source_task_id_and_exact_v1_recipient_shape():
    production_id = UUID("10000000-0000-4000-8000-000000000001")
    source_task_id = UUID("50000000-0000-4000-8000-000000000002")
    source_task_type_id = UUID("50000000-0000-4000-8000-000000000010")
    recipient_task_id = UUID("50000000-0000-4000-8000-000000000020")
    recipient_task_type_id = UUID("50000000-0000-4000-8000-000000000021")
    delivery = SimpleNamespace(
        id=UUID("a0000000-0000-4000-8000-000000000001"),
        production_id=production_id,
        recipient_task_id=recipient_task_id,
        recipient_task_type_id=recipient_task_type_id,
        delivery_revision=3,
        state="revised",
        feedback="Internal notes must not leak to the notification payload",
        updated_at=None,
    )
    package = SimpleNamespace(
        id=UUID("90000000-0000-4000-8000-000000000001"),
        version=3,
        package_type="final-exr",
        manifest_sha256="a" * 64,
        manifest_bytes=b"{}",
        created_by=UUID("80000000-0000-4000-8000-000000000001"),
    )
    source_task = SimpleNamespace(id=source_task_id, task_type_id=source_task_type_id)
    source_task_type = SimpleNamespace(name="Compositing")
    recipient_task_type = SimpleNamespace(name="FX")
    entity = SimpleNamespace(id=UUID("40000000-0000-4000-8000-000000000001"), name="SH010", code="SH010")
    sender = SimpleNamespace(
        id=package.created_by, first_name="Synthetic", last_name="Artist",
    )
    files = [SimpleNamespace(
        provider_object_id="fake-drive-file-01", file_format="exr",
        byte_count=16, sha256="b" * 64,
    )]

    event = build_file_delivery_event(
        delivery=delivery,
        package=package,
        source_task=source_task,
        source_task_type=source_task_type,
        recipient_task_type=recipient_task_type,
        entity=entity,
        entity_type_name="Shot",
        sender=sender,
        production_name="Synthetic Production",
        files=files,
        event_type="file_delivery.revised",
        activation_epoch=7,
    )

    validate_event_batch({"contract_version": "1.0", "events": [event]})
    assert event["source"] == {
        "id": str(source_task_id),
        "task_type_id": str(source_task_type_id),
        "task_type_name": "Compositing",
    }
    assert event["source"]["id"] != event["source"]["task_type_id"]
    assert event["delivery"] == {
        "id": str(delivery.id),
        "revision": 3,
        "status": "revised",
        "recipient_task_id": str(recipient_task_id),
        "recipient_task_type_id": str(recipient_task_type_id),
        "recipient_task_type_name": "FX",
    }
    assert event["output_package"]["objects"] == [{
        "drive_file_id": "fake-drive-file-01",
        "classification": "exr",
        "size_bytes": 16,
        "sha256": "b" * 64,
    }]
    assert "Internal notes" not in str(event)


def test_delivery_event_rejects_unrepresentable_entity_type():
    values = {
        "delivery": SimpleNamespace(
            id=UUID("a0000000-0000-4000-8000-000000000001"),
            production_id=UUID("10000000-0000-4000-8000-000000000001"),
            recipient_task_id=UUID("50000000-0000-4000-8000-000000000020"),
            recipient_task_type_id=UUID("50000000-0000-4000-8000-000000000021"),
            delivery_revision=1, state="available", feedback=None, updated_at=None,
        ),
        "package": SimpleNamespace(
            id=UUID("90000000-0000-4000-8000-000000000001"), version=1,
            package_type="render", manifest_sha256="a" * 64,
            manifest_bytes=b"{}", created_by=UUID("80000000-0000-4000-8000-000000000001"),
        ),
        "source_task": SimpleNamespace(
            id=UUID("50000000-0000-4000-8000-000000000002"),
            task_type_id=UUID("50000000-0000-4000-8000-000000000010"),
        ),
        "source_task_type": SimpleNamespace(name="Compositing"),
        "recipient_task_type": SimpleNamespace(name="FX"),
        "entity": SimpleNamespace(
            id=UUID("40000000-0000-4000-8000-000000000001"), name="SH010", code=None,
        ),
        "entity_type_name": "Unknown Entity",
        "sender": SimpleNamespace(
            id=UUID("80000000-0000-4000-8000-000000000001"), first_name="A", last_name="B",
        ),
        "production_name": "Synthetic Production",
        "files": [],
        "event_type": "file_delivery.created",
        "activation_epoch": 1,
    }
    try:
        build_file_delivery_event(**values)
    except ValueError as error:
        assert str(error) == "Kitsu entity type is not representable by the v1 file-event contract"
    else:
        raise AssertionError("Unknown Kitsu entity type must fail closed")


def test_delivery_event_rejects_manifest_over_v1_object_limit_without_truncating():
    row = SimpleNamespace(
        provider_object_id="fake-drive-file-01", file_format="exr",
        byte_count=16, sha256="b" * 64,
    )
    values = {
        "delivery": SimpleNamespace(
            id=UUID("a0000000-0000-4000-8000-000000000001"),
            production_id=UUID("10000000-0000-4000-8000-000000000001"),
            recipient_task_id=UUID("50000000-0000-4000-8000-000000000020"),
            recipient_task_type_id=UUID("50000000-0000-4000-8000-000000000021"),
            delivery_revision=1, state="available", feedback=None, updated_at=None,
        ),
        "package": SimpleNamespace(
            id=UUID("90000000-0000-4000-8000-000000000001"), version=1,
            package_type="render", manifest_sha256="a" * 64,
        ),
        "source_task": SimpleNamespace(
            id=UUID("50000000-0000-4000-8000-000000000002"),
            task_type_id=UUID("50000000-0000-4000-8000-000000000010"),
        ),
        "source_task_type": SimpleNamespace(name="Compositing"),
        "recipient_task_type": SimpleNamespace(name="FX"),
        "entity": SimpleNamespace(
            id=UUID("40000000-0000-4000-8000-000000000001"), name="SH010", code="SH010",
        ),
        "entity_type_name": "Shot",
        "sender": SimpleNamespace(
            id=UUID("80000000-0000-4000-8000-000000000001"), first_name="A", last_name="B",
        ),
        "production_name": "Synthetic Production",
        "files": [row] * 101,
        "event_type": "file_delivery.created",
        "activation_epoch": 1,
    }
    try:
        build_file_delivery_event(**values)
    except ValueError as error:
        assert str(error) == "The v1 file-event object limit does not allow truncating this delivery manifest"
    else:
        raise AssertionError("Oversized delivery manifests must not be silently truncated")
