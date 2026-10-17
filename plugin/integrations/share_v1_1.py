"""Strict semantic checks for the frozen file_share.created 1.1 contract."""

from __future__ import annotations

from datetime import datetime
import re
from urllib.parse import urlsplit
from uuid import UUID


_EVENT_FIELDS = {
    "contract_version", "producer_id", "event_id", "event_type", "occurred_at",
    "production_id", "activation_epoch", "source", "recipient", "share",
}
_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._~:-]{16,128}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class ShareContractError(ValueError):
    pass


def _exact_object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ShareContractError("file_share.created object fields are invalid")


def _uuid(value):
    if not isinstance(value, str):
        raise ShareContractError("file_share.created UUID is invalid")
    try:
        UUID(value)
    except (TypeError, ValueError):
        raise ShareContractError("file_share.created UUID is invalid") from None


def _timestamp(value, *, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or len(value) > 40:
        raise ShareContractError("file_share.created timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ShareContractError("file_share.created timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise ShareContractError("file_share.created timestamp requires a timezone")


def _text(value, limit, *, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or len(value) > limit:
        raise ShareContractError("file_share.created text field is invalid")


def validate_file_share_event(event):
    """Validate one event, including URL/object parity and inventory semantics."""
    _exact_object(event, _EVENT_FIELDS)
    if event["contract_version"] != "1.1" or event["producer_id"] != "vfx-files-plugin" or event["event_type"] != "file_share.created":
        raise ShareContractError("file_share.created version or event identity is invalid")
    _uuid(event["event_id"])
    _uuid(event["production_id"])
    epoch = event["activation_epoch"]
    if isinstance(epoch, bool) or not isinstance(epoch, int) or not 1 <= epoch <= 2147483647:
        raise ShareContractError("file_share.created activation epoch is invalid")
    _timestamp(event["occurred_at"])

    source = event["source"]
    _exact_object(source, {"entity_type", "entity_id", "task_id", "task_type_id"}, {"entity_name", "task_type_name"})
    if source["entity_type"] not in {"shot", "asset"}:
        raise ShareContractError("file_share.created source entity type is invalid")
    for key in ("entity_id", "task_id", "task_type_id"):
        _uuid(source[key])
    _text(source.get("entity_name"), 200, nullable=True)
    _text(source.get("task_type_name"), 120, nullable=True)

    recipient = event["recipient"]
    _exact_object(recipient, {"task_id", "task_type_id"}, {"task_type_name", "department_id", "department_name"})
    _uuid(recipient["task_id"])
    _uuid(recipient["task_type_id"])
    _text(recipient.get("task_type_name"), 120, nullable=True)
    if recipient.get("department_id") is not None:
        _uuid(recipient["department_id"])
    _text(recipient.get("department_name"), 120, nullable=True)

    share = event["share"]
    _exact_object(share, {"id", "share_target_id", "revision", "shared_by_id", "shared_at", "idempotency_key", "object", "metadata", "inventory", "access"})
    for key in ("id", "share_target_id", "shared_by_id"):
        _uuid(share[key])
    if share["revision"] != 1 or isinstance(share["revision"], bool):
        raise ShareContractError("file_share.created revision is invalid")
    _timestamp(share["shared_at"])
    if not isinstance(share["idempotency_key"], str) or not _IDEMPOTENCY.fullmatch(share["idempotency_key"]):
        raise ShareContractError("file_share.created target idempotency key is invalid")

    obj = share["object"]
    _exact_object(obj, {"provider", "object_id", "object_type", "url"})
    if obj["provider"] != "google_drive" or obj["object_type"] not in {"file", "folder"}:
        raise ShareContractError("file_share.created Drive object is invalid")
    if not isinstance(obj["object_id"], str) or not _ID.fullmatch(obj["object_id"]):
        raise ShareContractError("file_share.created Drive object ID is invalid")
    expected_url = (
        f"https://drive.google.com/file/d/{obj['object_id']}/view" if obj["object_type"] == "file"
        else f"https://drive.google.com/drive/folders/{obj['object_id']}"
    )
    if obj["url"] != expected_url:
        raise ShareContractError("file_share.created URL does not match its Drive object")
    parsed_url = urlsplit(obj["url"])
    if parsed_url.scheme != "https" or parsed_url.netloc != "drive.google.com" or parsed_url.query or parsed_url.fragment or parsed_url.username or parsed_url.password:
        raise ShareContractError("file_share.created Drive URL is not canonical")

    metadata = share["metadata"]
    _exact_object(metadata, {"status", "observed_at", "name", "mime_type", "size_bytes", "modified_at", "sha256"})
    if metadata["status"] not in {"complete", "partial", "unavailable"}:
        raise ShareContractError("file_share.created metadata status is invalid")
    _timestamp(metadata["observed_at"])
    _text(metadata["name"], 255, nullable=True)
    _text(metadata["mime_type"], 127, nullable=True)
    size = metadata["size_bytes"]
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 9223372036854775807):
        raise ShareContractError("file_share.created metadata size is invalid")
    _timestamp(metadata["modified_at"], nullable=True)
    if metadata["sha256"] is not None and (not isinstance(metadata["sha256"], str) or not _SHA256.fullmatch(metadata["sha256"])):
        raise ShareContractError("file_share.created metadata hash is invalid")
    if metadata["status"] == "unavailable" and any(metadata[key] is not None for key in ("name", "mime_type", "size_bytes", "modified_at", "sha256")):
        raise ShareContractError("file_share.created unavailable metadata claims values")

    inventory = share["inventory"]
    _exact_object(inventory, {"status", "observed_at", "scope", "file_count", "observed_file_count_lower_bound", "frame_summary"})
    if inventory["status"] not in {"complete", "partial", "unavailable"} or inventory["scope"] not in {"object", "direct_children", "recursive"}:
        raise ShareContractError("file_share.created inventory status or scope is invalid")
    _timestamp(inventory["observed_at"])
    for key in ("file_count", "observed_file_count_lower_bound"):
        value = inventory[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000):
            raise ShareContractError("file_share.created inventory count is invalid")
    if inventory["status"] == "unavailable" and (inventory["file_count"] is not None or inventory["observed_file_count_lower_bound"] is not None):
        raise ShareContractError("file_share.created unavailable inventory claims counts")
    if inventory["status"] == "partial" and (inventory["file_count"] is not None or inventory["observed_file_count_lower_bound"] is None):
        raise ShareContractError("file_share.created partial inventory count is invalid")
    if inventory["status"] == "complete" and (inventory["file_count"] is None or inventory["observed_file_count_lower_bound"] is not None):
        raise ShareContractError("file_share.created complete inventory count is invalid")
    frame = inventory["frame_summary"]
    if frame is not None:
        _exact_object(frame, {"status", "frame_start", "frame_end", "missing_count", "duplicate_count", "missing_samples"})
        if frame["status"] not in {"complete", "partial", "unavailable"} or not isinstance(frame["missing_samples"], list) or len(frame["missing_samples"]) > 20:
            raise ShareContractError("file_share.created frame summary is invalid")
        for key in ("frame_start", "frame_end", "missing_count", "duplicate_count"):
            value = frame[key]
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not -1_000_000_000 <= value <= 1_000_000_000):
                raise ShareContractError("file_share.created frame value is invalid")
        if any(isinstance(value, bool) or not isinstance(value, int) or not -1_000_000_000 <= value <= 1_000_000_000 for value in frame["missing_samples"]):
            raise ShareContractError("file_share.created frame sample is invalid")
        if frame["status"] == "complete" and any(
            isinstance(frame[key], bool) or not isinstance(frame[key], int)
            for key in ("frame_start", "frame_end", "missing_count", "duplicate_count")
        ):
            raise ShareContractError("file_share.created complete frame summary is incomplete")
        if frame["status"] == "partial" and any(
            frame[key] is not None for key in ("missing_count", "duplicate_count")
        ):
            raise ShareContractError("file_share.created partial frame summary claims exact counts")
        if frame["status"] == "unavailable" and (
            any(frame[key] is not None for key in ("frame_start", "frame_end", "missing_count", "duplicate_count"))
            or frame["missing_samples"]
        ):
            raise ShareContractError("file_share.created unavailable frame summary claims measurements")

    access = share["access"]
    _exact_object(access, {"status", "access_verified_at", "check_subject_type", "check_subject_id"})
    if access["status"] not in {"verified_service_only", "denied", "unknown", "not_checked"}:
        raise ShareContractError("file_share.created access status is invalid")
    _timestamp(access["access_verified_at"], nullable=True)
    if access["check_subject_type"] not in {"plugin_service_identity", "none"}:
        raise ShareContractError("file_share.created access subject is invalid")
    _text(access["check_subject_id"], 200, nullable=True)
    if access["status"] == "verified_service_only" and (access["access_verified_at"] is None or access["check_subject_type"] != "plugin_service_identity"):
        raise ShareContractError("file_share.created verified access evidence is incomplete")
    return event


def validate_file_share_batch(batch):
    if not isinstance(batch, dict) or set(batch) != {"contract_version", "events"} or batch["contract_version"] != "1.1":
        raise ShareContractError("file_share.created batch version is invalid")
    events = batch["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= 100:
        raise ShareContractError("file_share.created batch size is invalid")
    event_ids = set()
    production_epochs = set()
    share_targets = {}
    share_objects = {}
    share_recipients = {}
    for event in events:
        validate_file_share_event(event)
        if event["event_id"] in event_ids:
            raise ShareContractError("file_share.created batch event IDs are duplicated")
        event_ids.add(event["event_id"])
        production_epochs.add((event["production_id"], event["activation_epoch"]))
        share = event["share"]
        identity = (share["object"]["object_id"], share["object"]["object_type"], share["object"]["url"])
        prior = share_objects.setdefault(share["id"], identity)
        if prior != identity:
            raise ShareContractError("file_share.created share operation contains conflicting Drive objects")
        target_ids = share_targets.setdefault(share["id"], set())
        if share["share_target_id"] in target_ids:
            raise ShareContractError("file_share.created share operation duplicates a target")
        target_ids.add(share["share_target_id"])
        recipients = share_recipients.setdefault(share["id"], set())
        recipient_task_id = event["recipient"]["task_id"]
        if recipient_task_id in recipients:
            raise ShareContractError("file_share.created share operation duplicates a recipient Task")
        recipients.add(recipient_task_id)
    if len(production_epochs) != 1:
        raise ShareContractError("file_share.created batch mixes Production activation epochs")
    return events
