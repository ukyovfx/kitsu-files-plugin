"""Persist bounded, root-scoped Drive metadata reconciliation evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from .base import PermissionFailure, StorageAdapter, StorageObject


MAX_EXPECTED_OBJECTS = 500
MAX_PARENT_LISTINGS = 200


def enqueue_drive_reconciliation(
    session,
    *,
    reconciliation_id: UUID,
    production_id: UUID,
    storage_connection_id: UUID,
    allowed_root_id: str,
    actor_id: UUID,
    expected_objects: list[dict],
):
    """Persist a manager-requested reconciliation with stable retry identity."""
    from ..models import Job, StorageConnection

    if len(expected_objects) > MAX_EXPECTED_OBJECTS:
        raise ValueError("Drive reconciliation object limit exceeded")
    connection = session.get(StorageConnection, storage_connection_id)
    if connection is None or not connection.enabled or connection.production_id != production_id or connection.allowed_root_id != allowed_root_id:
        raise PermissionFailure("Drive reconciliation scope is no longer authorized")
    payload = {
        "job_type": "reconcile_drive_objects",
        "reconciliation_id": str(reconciliation_id),
        "production_id": str(production_id),
        "storage_connection_id": str(storage_connection_id),
        "allowed_root_id": allowed_root_id,
        "actor_id": str(actor_id),
        "expected_objects": expected_objects,
    }
    key = f"drive-reconcile:{reconciliation_id}"
    existing = session.scalar(select(Job).where(Job.idempotency_key == key).with_for_update())
    if existing is not None:
        if existing.job_type != "reconcile_drive_objects" or existing.payload != payload:
            raise ValueError("Drive reconciliation idempotency key conflicts with another request")
        return existing
    row = Job(job_type="reconcile_drive_objects", idempotency_key=key, payload=payload)
    session.add(row)
    session.flush()
    return row


def metadata_fingerprint(item: StorageObject) -> str:
    """Fingerprint provider metadata only; never claim this is a content hash."""
    payload = {
        "object_id": item.object_id,
        "parent_id": item.parent_id,
        "size": item.size,
        "modified_time": item.modified_time,
        "md5_checksum": item.md5_checksum,
        "mime_type": item.mime_type,
        "trashed": item.trashed,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_observations(
    session,
    *,
    reconciliation_id: UUID,
    production_id: UUID,
    storage_connection_id: UUID,
    allowed_root_id: str,
    expected: list[dict],
    observed: dict[str, StorageObject],
    listing_complete: bool,
    inaccessible: bool = False,
    error_code: str | None = None,
):
    """Append one idempotent observation per expected Drive object.

    A missing state is recorded only after a complete scoped listing. A failed
    or incomplete listing becomes inaccessible/stale and cannot prove absence.
    """
    from ..models import DriveReconciliationObservation, StorageConnection

    if len(expected) > MAX_EXPECTED_OBJECTS:
        raise ValueError("Drive reconciliation object limit exceeded")
    connection = session.get(StorageConnection, storage_connection_id)
    if (
        connection is None or not connection.enabled
        or connection.production_id != production_id
        or connection.allowed_root_id != allowed_root_id
    ):
        raise PermissionFailure("Drive reconciliation scope is no longer authorized")
    unique = {}
    for item in expected:
        if not isinstance(item, dict) or set(item) - {"object_id", "metadata_fingerprint", "parent_id"}:
            raise ValueError("Drive reconciliation expectation is invalid")
        object_id = item.get("object_id")
        if not isinstance(object_id, str) or not object_id or len(object_id) > 255 or object_id in unique:
            raise ValueError("Drive reconciliation object identity is invalid")
        fingerprint = item.get("metadata_fingerprint")
        if fingerprint is not None and (not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
            raise ValueError("Drive reconciliation metadata fingerprint is invalid")
        parent_id = item.get("parent_id")
        if parent_id is not None and (not isinstance(parent_id, str) or not parent_id or len(parent_id) > 255):
            raise ValueError("Drive reconciliation parent is invalid")
        unique[object_id] = item
    existing = {
        row.provider_object_id: row
        for row in session.scalars(select(DriveReconciliationObservation).where(
            DriveReconciliationObservation.reconciliation_id == reconciliation_id
        ))
    }
    rows = []
    now = datetime.now(timezone.utc)
    for object_id, expectation in unique.items():
        item = observed.get(object_id)
        if inaccessible:
            state = "inaccessible"
        elif not listing_complete:
            state = "stale"
        elif item is None or item.trashed:
            state = "missing"
        elif expectation.get("metadata_fingerprint") and expectation["metadata_fingerprint"] != metadata_fingerprint(item):
            state = "modified"
        else:
            state = "present"
        values = dict(
            reconciliation_id=reconciliation_id,
            production_id=production_id,
            storage_connection_id=storage_connection_id,
            allowed_root_id=allowed_root_id,
            provider_object_id=object_id,
            state=state,
            metadata_fingerprint=metadata_fingerprint(item) if item else None,
            provider_modified_at=item.modified_time if item else None,
            byte_count=item.size if item else None,
            error_code=error_code if state in ("inaccessible", "stale") else None,
            observed_at=now,
        )
        row = existing.get(object_id)
        if row is None:
            row = DriveReconciliationObservation(**values)
            session.add(row)
        else:
            # Idempotent replay may only repeat identical evidence.
            comparable = {key: getattr(row, key) for key in values if key != "observed_at"}
            replay_values = {key: value for key, value in values.items() if key != "observed_at"}
            if comparable != replay_values:
                raise ValueError("Reconciliation ID was reused with different evidence")
        rows.append(row)
    session.flush()
    return rows


def make_drive_reconciliation_handler(sessions, adapter_factory):
    """Create a durable Worker handler for bounded provider metadata checks."""
    from ..jobs.queue import JobResult
    from ..models import StorageConnection
    from sqlalchemy import select
    from .base import PermissionFailure

    def handler(job):
        payload = job.payload
        try:
            production_id = UUID(str(payload["production_id"]))
            connection_id = UUID(str(payload["storage_connection_id"]))
            reconciliation_id = UUID(str(payload["reconciliation_id"]))
            root_id = payload["allowed_root_id"]
            expected = payload["expected_objects"]
            if not isinstance(expected, list) or len(expected) > MAX_EXPECTED_OBJECTS:
                return JobResult("failed", error_code="invalid_reconciliation_payload")
        except (KeyError, ValueError, TypeError):
            return JobResult("failed", error_code="invalid_reconciliation_payload")
        with sessions() as session:
            connection = session.scalar(select(StorageConnection).where(
                StorageConnection.id == connection_id,
                StorageConnection.production_id == production_id,
                StorageConnection.enabled.is_(True),
                StorageConnection.allowed_root_id == root_id,
            ))
            if connection is None:
                return JobResult("blocked_authorization", error_code="storage_scope_changed")
            adapter = adapter_factory(connection)
            try:
                capabilities = adapter.capabilities(root_id)
                if not capabilities.root_visible or capabilities.root_id != root_id:
                    raise PermissionFailure("root is not visible")
                parents = sorted({item.get("parent_id") or root_id for item in expected})
                if len(parents) > MAX_PARENT_LISTINGS:
                    return JobResult("failed", error_code="reconciliation_parent_limit")
                observed = {}
                complete = True
                for parent_id in parents:
                    adapter.assert_parent_within_root(parent_id)
                    token = None
                    pages = 0
                    while True:
                        listing = adapter.list_children(parent_id, token)
                        observed.update((item.object_id, item) for item in listing.items)
                        pages += 1
                        if pages > 1000 or not listing.complete:
                            complete = False
                            break
                        if listing.next_page_token is None:
                            break
                        if listing.next_page_token == token:
                            complete = False
                            break
                        token = listing.next_page_token
                    if not complete:
                        break
            except PermissionFailure:
                with session.begin_nested():
                    record_observations(
                        session,
                        reconciliation_id=reconciliation_id,
                        production_id=production_id,
                        storage_connection_id=connection_id,
                        allowed_root_id=root_id,
                        expected=expected,
                        observed={},
                        listing_complete=False,
                        inaccessible=True,
                        error_code="provider_permission_denied",
                    )
                session.commit()
                return JobResult("completed")
            except Exception:
                # Provider timeouts and uncertain responses are retried by the
                # queue; they are never misreported as confirmed missing data.
                return JobResult("retry", error_code="provider_reconciliation_unavailable")
            if not complete:
                with session.begin_nested():
                    record_observations(
                        session,
                        reconciliation_id=reconciliation_id,
                        production_id=production_id,
                        storage_connection_id=connection_id,
                        allowed_root_id=root_id,
                        expected=expected,
                        observed=observed,
                        listing_complete=False,
                        error_code="incomplete_provider_listing",
                    )
                session.commit()
                return JobResult("reconcile_required", error_code="provider_listing_incomplete")
            with session.begin_nested():
                record_observations(
                    session,
                    reconciliation_id=reconciliation_id,
                    production_id=production_id,
                    storage_connection_id=connection_id,
                    allowed_root_id=root_id,
                    expected=expected,
                    observed=observed,
                    listing_complete=True,
                )
            session.commit()
            return JobResult("completed")

    return handler
