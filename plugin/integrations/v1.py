"""Pure protocol primitives shared by the coordinated KitsuSync v1 contract.

Management API authentication and Zou route registration live in their own
adapter. Event HMAC remains a separate trust direction from service JWT auth.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence
from uuid import UUID

MAX_PAGE_OBJECTS = 500
MAX_SNAPSHOT_OBJECTS = 500_000
MAX_EVENT_BATCH = 100
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HIERARCHY_TYPES = {"production", "episode", "sequence", "shot", "asset", "task", "task_type"}
_HIERARCHY_FIELDS = {"object_type", "id", "parent_id", "name", "code", "task_type_id", "updated_at", "included_in_sync"}
_EVENT_FIELDS = {
    "contract_version", "producer_id", "event_id", "event_type", "occurred_at", "production_id",
    "activation_epoch", "production_name", "episode_id", "episode_name", "entity", "source",
    "output_package", "delivery", "sender", "folder", "sender_person_id",
    "config_revision", "previous_activation_epoch", "new_activation_epoch",
}
_FILE_EVENT_TYPES = {
    "file_delivery.created", "file_delivery.ready", "file_delivery.accepted",
    "file_delivery.needs_fix", "file_delivery.revised",
}
_PACKAGE_EVENT_TYPES = {"output_package.created", "output_package.ready"}


def _is_contract_int(value: Any, *, minimum: int = 1) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _require_object(value: Any, *, allowed: set[str], required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= set(value) or set(value) - allowed:
        raise ContractValidationError(f"{label} does not match the published schema")
    return value


def _uuid_string(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ContractValidationError(f"{label} must be a UUID")
    try:
        UUID(value)
    except ValueError:
        raise ContractValidationError(f"{label} must be a UUID") from None


def _validate_output_package(value: Any) -> None:
    package = _require_object(value, allowed={"id", "version", "classification", "manifest_sha256", "manifest_ref", "frame_range", "objects"}, required={"id", "version", "classification", "manifest_sha256", "objects"}, label="Output Package event")
    _uuid_string(package["id"], "Output Package ID")
    if not isinstance(package["version"], int) or isinstance(package["version"], bool) or package["version"] < 1:
        raise ContractValidationError("Output Package version is invalid")
    if not isinstance(package["classification"], str) or len(package["classification"]) > 100:
        raise ContractValidationError("Output Package classification is invalid")
    if not isinstance(package["manifest_sha256"], str) or not _SHA256.fullmatch(package["manifest_sha256"]):
        raise ContractValidationError("Output Package manifest checksum is invalid")
    if package.get("manifest_ref") is not None and (not isinstance(package["manifest_ref"], str) or len(package["manifest_ref"]) > 200 or not package["manifest_ref"].startswith("manifest:")):
        raise ContractValidationError("Output Package manifest reference is invalid")
    if package.get("manifest_ref") is not None and not re.fullmatch(r"manifest:[0-9a-fA-F-]{36}", package["manifest_ref"]):
        raise ContractValidationError("Output Package manifest reference is invalid")
    objects = package["objects"]
    if not isinstance(objects, list) or len(objects) > 100:
        raise ContractValidationError("Output Package event object list is invalid")
    for item in objects:
        file = _require_object(item, allowed={"drive_file_id", "classification", "size_bytes", "sha256"}, required={"drive_file_id", "classification", "size_bytes", "sha256"}, label="Drive object")
        if not isinstance(file["drive_file_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", file["drive_file_id"]):
            raise ContractValidationError("Drive object identity is invalid")
        if not isinstance(file["size_bytes"], int) or isinstance(file["size_bytes"], bool) or file["size_bytes"] < 0:
            raise ContractValidationError("Drive object size is invalid")
        if not isinstance(file["sha256"], str) or not _SHA256.fullmatch(file["sha256"]):
            raise ContractValidationError("Drive object checksum is invalid")
        if not isinstance(file["classification"], str) or len(file["classification"]) > 100:
            raise ContractValidationError("Drive object classification is invalid")


def _validate_event_shape(event: dict[str, Any]) -> None:
    _require_object(event, allowed=_EVENT_FIELDS, required={"contract_version", "producer_id", "event_id", "event_type", "occurred_at", "production_id", "activation_epoch"}, label="Event")
    if event["contract_version"] != "1.0" or event["producer_id"] != "vfx-files-plugin":
        raise ContractValidationError("Event producer or contract version is invalid")
    if not isinstance(event["event_type"], str):
        raise ContractValidationError("Event type is invalid")
    if event["event_type"] in _FILE_EVENT_TYPES:
        required = {"entity", "source", "output_package", "delivery", "sender"}
        event_specific_fields = {"entity", "source", "output_package", "delivery", "sender"}
    elif event["event_type"] == "folder.provisioning.completed":
        required = {"folder"}
        event_specific_fields = {"folder"}
    elif event["event_type"] in _PACKAGE_EVENT_TYPES:
        required = {"source", "output_package"}
        event_specific_fields = {"source", "output_package", "sender_person_id"}
    elif event["event_type"] in {
        "storage_authority.deactivation_requested", "storage_authority.activation_requested",
    }:
        required = {"config_revision", "previous_activation_epoch", "new_activation_epoch"}
        event_specific_fields = required
    else:
        raise ContractValidationError("Event type is not supported by the published v1 contract")
    common_fields = {"contract_version", "producer_id", "event_id", "event_type", "occurred_at", "production_id", "activation_epoch", "production_name", "episode_id", "episode_name"}
    if set(event) - common_fields - event_specific_fields:
        raise ContractValidationError("Event contains fields not allowed for its event type")
    if not required <= set(event):
        raise ContractValidationError("Event omits fields required by its published event type")
    if not isinstance(event["activation_epoch"], int) or isinstance(event["activation_epoch"], bool) or event["activation_epoch"] < 1:
        raise ContractValidationError("Event activation epoch is invalid")
    if event["event_type"] in {
        "storage_authority.deactivation_requested", "storage_authority.activation_requested",
    }:
        previous = event["previous_activation_epoch"]
        following = event["new_activation_epoch"]
        if (
            not _is_contract_int(event["config_revision"])
            or
            not _is_contract_int(previous) or not _is_contract_int(following)
            or following != previous + 1
            or event["event_type"] == "storage_authority.deactivation_requested" and event["activation_epoch"] != previous
            or event["event_type"] == "storage_authority.activation_requested" and event["activation_epoch"] != following
        ):
            raise ContractValidationError("Deactivation barrier epochs are invalid")
    for field in ("production_name", "episode_name"):
        if field in event and event[field] is not None and (not isinstance(event[field], str) or len(event[field]) > 240):
            raise ContractValidationError("Event display name is invalid")
    if "episode_id" in event and event["episode_id"] is not None:
        _uuid_string(event["episode_id"], "Episode ID")
    if "entity" in event:
        entity = _require_object(event["entity"], allowed={"type", "id", "name", "code"}, required={"type", "id", "name"}, label="Entity")
        if entity["type"] not in {"sequence", "shot", "asset"}:
            raise ContractValidationError("Entity type is invalid")
        _uuid_string(entity["id"], "Entity ID")
        if not isinstance(entity["name"], str) or len(entity["name"]) > 240:
            raise ContractValidationError("Entity name is invalid")
        if entity.get("code") is not None and (not isinstance(entity["code"], str) or len(entity["code"]) > 120):
            raise ContractValidationError("Entity code is invalid")
    task = event.get("source")
    if task is not None:
        if event["event_type"] in _FILE_EVENT_TYPES:
            task = _require_object(task, allowed={"id", "task_type_id", "task_type_name"}, required={"id", "task_type_id", "task_type_name"}, label="Source Task")
            _uuid_string(task["task_type_id"], "Task Type ID")
        else:
            task = _require_object(task, allowed={"task_id", "task_type_id", "task_type_name"}, required={"task_id", "task_type_id"}, label="Source Task")
            _uuid_string(task["task_id"], "Task ID")
        _uuid_string(task["task_type_id"], "Task Type ID")
        _uuid_string(task["id"] if "id" in task else task["task_type_id"], "Task identity")
        if "task_type_name" in task and (not isinstance(task["task_type_name"], str) or len(task["task_type_name"]) > 160):
            raise ContractValidationError("Task Type display name is invalid")
    if "sender" in event:
        sender = _require_object(event["sender"], allowed={"id", "display_name"}, required={"id", "display_name"}, label="Sender")
        _uuid_string(sender["id"], "Sender ID")
        if not isinstance(sender["display_name"], str) or len(sender["display_name"]) > 160:
            raise ContractValidationError("Sender display name is invalid")
    if "sender_person_id" in event and event["sender_person_id"] is not None:
        _uuid_string(event["sender_person_id"], "Sender ID")
    if "output_package" in event:
        _validate_output_package(event["output_package"])
        package = event["output_package"]
        frame = package.get("frame_range")
        if frame is not None:
            frame = _require_object(frame, allowed={"start", "end", "step", "rate_num", "rate_den"}, required={"start", "end", "step", "rate_num", "rate_den"}, label="Frame Range")
            if any(not isinstance(frame[field], int) or isinstance(frame[field], bool) for field in frame):
                raise ContractValidationError("Frame Range values must be integers")
            if frame["step"] < 1 or frame["rate_num"] < 1 or frame["rate_den"] < 1:
                raise ContractValidationError("Frame Range rates and step must be positive")
    if "delivery" in event:
        delivery = _require_object(event["delivery"], allowed={"id", "revision", "status", "recipient_task_id", "recipient_task_type_id", "recipient_task_type_name", "feedback_code"}, required={"id", "revision", "status", "recipient_task_id", "recipient_task_type_id", "recipient_task_type_name"}, label="Delivery")
        for field in ("id", "recipient_task_id", "recipient_task_type_id"):
            _uuid_string(delivery[field], field)
        if delivery["status"] not in {"created", "ready", "accepted", "needs_fix", "revised"}:
            raise ContractValidationError("Delivery status is invalid")
        if not isinstance(delivery["revision"], int) or isinstance(delivery["revision"], bool) or delivery["revision"] < 1:
            raise ContractValidationError("Delivery revision is invalid")
        if not isinstance(delivery["recipient_task_type_name"], str) or len(delivery["recipient_task_type_name"]) > 160:
            raise ContractValidationError("Recipient Task Type name is invalid")
        if delivery.get("feedback_code") is not None and (not isinstance(delivery["feedback_code"], str) or len(delivery["feedback_code"]) > 80):
            raise ContractValidationError("Delivery feedback code is invalid")
    if "folder" in event:
        folder = _require_object(event["folder"], allowed={"object_type", "object_id", "drive_file_id", "binding_state"}, required={"object_type", "object_id", "drive_file_id", "binding_state"}, label="Folder")
        if folder["object_type"] not in {"production", "episode", "sequence", "shot", "asset", "task"} or folder["binding_state"] != "ready":
            raise ContractValidationError("Folder event is invalid")
        _uuid_string(folder["object_id"], "Folder object ID")
        if not isinstance(folder["drive_file_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", folder["drive_file_id"]):
            raise ContractValidationError("Folder Drive ID is invalid")


class ContractValidationError(ValueError):
    """A request does not satisfy the published v1 contract constraints."""


class SnapshotConflict(ContractValidationError):
    """A repeated page or snapshot identity contains different bytes."""


def _number(value: int | float) -> str:
    if isinstance(value, bool):
        raise ContractValidationError("Boolean is not a JSON number here")
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise ContractValidationError("Integer exceeds the interoperable JSON number range")
        return str(value)
    if not math.isfinite(value):
        raise ContractValidationError("Non-finite numbers are not valid in canonical JSON")
    if value == 0:
        return "0"
    absolute = abs(value)
    encoded = repr(value).lower()
    if 1e-6 <= absolute < 1e21:
        fixed = format(Decimal(encoded), "f")
        if "." in fixed:
            fixed = fixed.rstrip("0").rstrip(".")
        return fixed
    if "e" not in encoded:
        encoded = format(value, ".15e")
    mantissa, exponent = encoded.split("e", 1)
    mantissa = mantissa.rstrip("0").rstrip(".")
    exp = int(exponent)
    return f"{mantissa}e{'+' if exp >= 0 else ''}{exp}"


def canonical_json(value: Any) -> bytes:
    """Serialize JSON using RFC 8785 ordering and ECMAScript number forms."""
    def encode(item: Any) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, (int, float)):
            return _number(item)
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if isinstance(item, list):
            return "[" + ",".join(encode(v) for v in item) + "]"
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            keys = sorted(item, key=lambda key: key.encode("utf-16-be", errors="surrogatepass"))
            return "{" + ",".join(encode(key) + ":" + encode(item[key]) for key in keys) + "}"
        raise ContractValidationError("Only JSON-compatible values are accepted")

    try:
        return encode(value).encode("utf-8", errors="strict")
    except UnicodeError:
        raise ContractValidationError("Invalid Unicode in canonical JSON") from None


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def hierarchy_sort_key(obj: Mapping[str, Any]) -> tuple[str, str]:
    object_type, object_id = obj.get("object_type"), obj.get("id")
    if object_type not in _HIERARCHY_TYPES or not isinstance(object_id, str):
        raise ContractValidationError("Hierarchy object type or ID is invalid")
    try:
        UUID(object_id)
    except ValueError:
        raise ContractValidationError("Hierarchy object ID must be a UUID") from None
    return object_type, object_id


def validate_hierarchy(objects: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(objects, (list, tuple)) or not 1 <= len(objects) <= MAX_SNAPSHOT_OBJECTS:
        raise ContractValidationError("Hierarchy snapshot exceeds the object limit")
    normalized = [dict(obj) for obj in objects]
    keys = [hierarchy_sort_key(obj) for obj in normalized]
    if len(set(keys)) != len(keys):
        raise ContractValidationError("Hierarchy snapshot contains duplicate object identities")
    if keys != sorted(keys):
        raise ContractValidationError("Hierarchy snapshot is not sorted by object_type and id")
    identities = {obj["id"] for obj in normalized}
    for obj in normalized:
        if not {"object_type", "id", "parent_id", "name", "included_in_sync"} <= set(obj):
            raise ContractValidationError("Hierarchy object omits required published fields")
        if set(obj) - _HIERARCHY_FIELDS:
            raise ContractValidationError("Hierarchy object contains fields outside the published contract")
        if not isinstance(obj.get("name"), str) or not obj["name"] or len(obj["name"]) > 240:
            raise ContractValidationError("Hierarchy object name is invalid")
        if obj.get("code") is not None and (not isinstance(obj["code"], str) or len(obj["code"]) > 240):
            raise ContractValidationError("Hierarchy object code is invalid")
        if obj.get("task_type_id") is not None:
            try:
                UUID(obj["task_type_id"])
            except (ValueError, TypeError):
                raise ContractValidationError("Hierarchy Task Type ID must be a UUID") from None
        if obj.get("updated_at") is not None:
            if not isinstance(obj["updated_at"], str):
                raise ContractValidationError("Hierarchy updated_at is invalid")
            try:
                timestamp = obj["updated_at"].replace("Z", "+00:00")
                if not datetime.fromisoformat(timestamp).tzinfo:
                    raise ValueError
            except ValueError:
                raise ContractValidationError("Hierarchy updated_at must be RFC 3339") from None
        parent_id = obj.get("parent_id")
        if parent_id is not None and parent_id not in identities:
            raise ContractValidationError("Hierarchy object refers to a parent outside the snapshot")
        if parent_id is not None:
            try:
                UUID(parent_id)
            except (ValueError, TypeError):
                raise ContractValidationError("Hierarchy parent ID must be a UUID") from None
        if not isinstance(obj.get("included_in_sync"), bool):
            raise ContractValidationError("Hierarchy objects require included_in_sync")
    parent_by_id = {obj["id"]: obj.get("parent_id") for obj in normalized}
    for object_id in parent_by_id:
        visited = set()
        cursor = object_id
        while cursor is not None:
            if cursor in visited:
                raise ContractValidationError("Hierarchy snapshot contains a parent cycle")
            visited.add(cursor)
            cursor = parent_by_id[cursor]
    return normalized


def validate_snapshot_start(payload: Any) -> dict[str, Any]:
    """Validate the published SyncSnapshotStart request without extensions."""
    required = {"contract_version", "snapshot_id", "config_revision", "source_etag", "page_count", "object_count", "snapshot_sha256"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ContractValidationError("Snapshot start does not match the published schema")
    if payload["contract_version"] != "1.0":
        raise ContractValidationError("Unsupported snapshot contract version")
    try:
        snapshot_id = UUID(payload["snapshot_id"])
    except (ValueError, TypeError, AttributeError):
        raise ContractValidationError("Snapshot ID must be a UUID") from None
    if str(snapshot_id) != payload["snapshot_id"]:
        raise ContractValidationError("Snapshot ID must be canonical")
    if not _is_contract_int(payload["config_revision"]) or not 1 <= payload["config_revision"] <= 2_147_483_647:
        raise ContractValidationError("Snapshot config revision is invalid")
    if not isinstance(payload["source_etag"], str) or not 1 <= len(payload["source_etag"]) <= 200:
        raise ContractValidationError("Snapshot source ETag is invalid")
    if not _is_contract_int(payload["page_count"]) or payload["page_count"] > math.ceil(MAX_SNAPSHOT_OBJECTS / MAX_PAGE_OBJECTS):
        raise ContractValidationError("Snapshot page count is outside the published bounds")
    if not _is_contract_int(payload["object_count"]) or payload["object_count"] > MAX_SNAPSHOT_OBJECTS:
        raise ContractValidationError("Snapshot object count is outside the published bounds")
    if payload["page_count"] > payload["object_count"]:
        raise ContractValidationError("Snapshot cannot declare more non-empty pages than objects")
    if not isinstance(payload["snapshot_sha256"], str) or not _SHA256.fullmatch(payload["snapshot_sha256"]):
        raise ContractValidationError("Snapshot digest is invalid")
    return payload


def validate_snapshot_page_request(payload: Any, *, path_page_index: int) -> dict[str, Any]:
    """Validate one exact contract page envelope and its canonical checksum."""
    required = {"contract_version", "page_index", "page_sha256", "objects"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ContractValidationError("Snapshot page does not match the published schema")
    if payload["contract_version"] != "1.0":
        raise ContractValidationError("Unsupported snapshot contract version")
    if not _is_contract_int(payload["page_index"], minimum=0) or payload["page_index"] != path_page_index:
        raise ContractValidationError("Snapshot page index does not match the route")
    if not isinstance(payload["objects"], list) or not 1 <= len(payload["objects"]) <= MAX_PAGE_OBJECTS:
        raise ContractValidationError("Snapshot page size is outside the published bounds")
    if not isinstance(payload["page_sha256"], str) or not _SHA256.fullmatch(payload["page_sha256"]):
        raise ContractValidationError("Snapshot page digest is invalid")
    # Validate object shape and byte size before hashing or persistence.
    if len(canonical_json(payload["objects"])) > 2 * 1024 * 1024:
        raise ContractValidationError("Snapshot page exceeds the byte limit")
    return payload


def validate_snapshot_complete_request(payload: Any) -> dict[str, Any]:
    required = {"contract_version", "page_count", "object_count", "snapshot_sha256"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ContractValidationError("Snapshot completion does not match the published schema")
    if payload["contract_version"] != "1.0":
        raise ContractValidationError("Unsupported snapshot contract version")
    if not _is_contract_int(payload["page_count"]) or payload["page_count"] > math.ceil(MAX_SNAPSHOT_OBJECTS / MAX_PAGE_OBJECTS):
        raise ContractValidationError("Snapshot page count is outside the published bounds")
    if not _is_contract_int(payload["object_count"]) or payload["object_count"] > MAX_SNAPSHOT_OBJECTS:
        raise ContractValidationError("Snapshot object count is outside the published bounds")
    if payload["page_count"] > payload["object_count"]:
        raise ContractValidationError("Snapshot cannot declare more non-empty pages than objects")
    if not isinstance(payload["snapshot_sha256"], str) or not _SHA256.fullmatch(payload["snapshot_sha256"]):
        raise ContractValidationError("Snapshot digest is invalid")
    return payload


def validate_snapshot_production(objects: Sequence[Mapping[str, Any]], production_id: str) -> None:
    """Ensure the immutable snapshot is rooted in its reserved Production UUID."""
    production_objects = [item for item in objects if item.get("object_type") == "production"]
    if len(production_objects) != 1 or production_objects[0].get("id") != production_id:
        raise ContractValidationError("Hierarchy snapshot Production does not match its reserved Production")


@dataclass(frozen=True)
class SnapshotPage:
    page_index: int
    objects: tuple[dict[str, Any], ...]
    sha256: str


class HierarchySnapshot:
    """In-memory page verifier; callers persist accepted pages transactionally."""

    def __init__(self, *, page_count: int, object_count: int, full_sha256: str):
        if isinstance(page_count, bool) or not isinstance(page_count, int) or not 1 <= page_count <= math.ceil(MAX_SNAPSHOT_OBJECTS / MAX_PAGE_OBJECTS):
            raise ContractValidationError("Invalid hierarchy page count")
        if isinstance(object_count, bool) or not isinstance(object_count, int) or not 1 <= object_count <= MAX_SNAPSHOT_OBJECTS:
            raise ContractValidationError("Invalid hierarchy object count")
        if not isinstance(full_sha256, str) or not _SHA256.fullmatch(full_sha256):
            raise ContractValidationError("Invalid hierarchy snapshot bounds or digest")
        self.page_count = page_count
        self.object_count = object_count
        self.full_sha256 = full_sha256
        self._pages: dict[int, SnapshotPage] = {}

    def put_page(self, page_index: int, objects: Sequence[Mapping[str, Any]], page_sha256: str) -> SnapshotPage:
        if isinstance(page_index, bool) or not isinstance(page_index, int) or not 0 <= page_index < self.page_count:
            raise ContractValidationError("Page index is out of range")
        if not isinstance(objects, (list, tuple)) or len(objects) > MAX_PAGE_OBJECTS:
            raise ContractValidationError("Hierarchy page exceeds the page size limit")
        normalized = tuple(dict(obj) for obj in objects)
        actual = sha256_hex(canonical_json(list(normalized)))
        if not _SHA256.fullmatch(page_sha256) or not hmac.compare_digest(actual, page_sha256):
            raise ContractValidationError("Hierarchy page checksum mismatch")
        page = SnapshotPage(page_index, normalized, actual)
        previous = self._pages.get(page_index)
        if previous is not None and previous != page:
            raise SnapshotConflict("Page index was already accepted with different content")
        if previous is None:
            self._pages[page_index] = page
        return page

    def complete(self) -> list[dict[str, Any]]:
        if len(self._pages) != self.page_count:
            raise ContractValidationError("Hierarchy snapshot is incomplete")
        objects = [obj for index in range(self.page_count) for obj in self._pages[index].objects]
        if len(objects) != self.object_count:
            raise ContractValidationError("Hierarchy object count does not match the declared count")
        validated = validate_hierarchy(objects)
        if not hmac.compare_digest(sha256_hex(canonical_json(validated)), self.full_sha256):
            raise ContractValidationError("Hierarchy snapshot digest mismatch")
        return validated


def signed_event_headers(*, key_id: str, secret: bytes, timestamp: str, nonce: str, raw_body: bytes, idempotency_key: str) -> dict[str, str]:
    """Build the contract HMAC over exact bytes; does not send the request."""
    if not isinstance(key_id, str) or not 1 <= len(key_id) <= 80 or not re.fullmatch(r"[A-Za-z0-9._:-]+", key_id):
        raise ContractValidationError("Event signing inputs are invalid")
    if not isinstance(nonce, str) or not 16 <= len(nonce) <= 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", nonce):
        raise ContractValidationError("Event nonce is outside the published bounds")
    if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", idempotency_key):
        raise ContractValidationError("Event idempotency key is outside the published bounds")
    if not isinstance(timestamp, str) or "\n" in timestamp or not isinstance(raw_body, bytes):
        raise ContractValidationError("Event signing inputs are invalid")
    try:
        parsed_timestamp = timestamp.replace("Z", "+00:00")
        if not datetime.fromisoformat(parsed_timestamp).tzinfo:
            raise ValueError
    except ValueError:
        raise ContractValidationError("Event timestamp must be RFC 3339") from None
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ContractValidationError("Event signing key must contain at least 256 bits")
    body_sha = sha256_hex(raw_body)
    signing_input = f"{timestamp}\n{nonce}\n{body_sha}\n{idempotency_key}".encode("utf-8")
    signature = hmac.new(secret, signing_input, hashlib.sha256).hexdigest()
    return {
        "X-VFX-Key-Id": key_id,
        "X-VFX-Timestamp": timestamp,
        "X-VFX-Nonce": nonce,
        "X-VFX-Content-SHA256": body_sha,
        "X-VFX-Signature": f"sha256={signature}",
        "Idempotency-Key": idempotency_key,
    }


def validate_event_batch(batch: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(batch, Mapping) or batch.get("contract_version") != "1.0":
        raise ContractValidationError("Unsupported KitsuSync contract version")
    events = batch.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENT_BATCH:
        raise ContractValidationError("Event batch must contain between 1 and 100 events")
    seen: set[str] = set()
    result = []
    for event in events:
        if not isinstance(event, dict):
            raise ContractValidationError("Event must be an object")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise ContractValidationError("Event IDs must be non-empty and unique within the batch")
        try:
            _uuid_string(event_id, "Event ID")
            _uuid_string(event.get("production_id"), "Production ID")
        except ContractValidationError:
            raise ContractValidationError("Event identity is invalid") from None
        if not isinstance(event.get("occurred_at"), str):
            raise ContractValidationError("Event timestamp is invalid")
        if event.get("contract_version") != "1.0" or event.get("producer_id") != "vfx-files-plugin":
            raise ContractValidationError("Event does not match the published event envelope")
        try:
            UUID(event_id)
            UUID(event["production_id"])
            datetime_value = event["occurred_at"].replace("Z", "+00:00")
            if not datetime.fromisoformat(datetime_value).tzinfo:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ContractValidationError("Event identity or timestamp is invalid") from None
        _validate_event_shape(event)
        seen.add(event_id)
        result.append(event)
    return result
