"""Transactional persistence helpers for the KitsuSync hierarchy snapshot API.

HTTP authentication and route mounting are kept in the host adapter. These
functions assume their caller has already verified service identity, actor,
Production scope, and request idempotency.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, text

from .v1 import (
    ContractValidationError,
    HierarchySnapshot,
    SnapshotConflict,
    canonical_json,
    validate_snapshot_production,
)

SNAPSHOT_UPLOAD_TTL = timedelta(minutes=15)


class StaleSnapshot(ContractValidationError):
    pass


def _get_models():
    from ..models import (
        KitsuSyncDesiredConfiguration,
        KitsuSyncHierarchySnapshot,
        KitsuSyncHierarchySnapshotPage,
        KitsuSyncManagementIdempotency,
    )

    return KitsuSyncDesiredConfiguration, KitsuSyncHierarchySnapshot, KitsuSyncHierarchySnapshotPage, KitsuSyncManagementIdempotency


def snapshot_payload(session, snapshot) -> dict:
    """Render only the published immutable snapshot response fields."""
    from sqlalchemy import func
    _Desired, _Snapshot, Page, _Idempotency = _get_models()
    received_pages = session.scalar(
        select(func.count()).select_from(Page).where(Page.snapshot_id == snapshot.id)
    ) or 0
    return {
        "contract_version": "1.0",
        "id": str(snapshot.id),
        "production_id": str(snapshot.production_id),
        "config_revision": snapshot.config_revision,
        "state": snapshot.state,
        "page_count": snapshot.page_count,
        "received_pages": received_pages,
        "object_count": snapshot.object_count,
        "expires_at": snapshot.expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def run_snapshot_idempotent(session, *, production_id: UUID, actor_id: UUID, idempotency_key: str,
                            operation: str, request_body: dict, apply):
    """Serialize by Production/key and persist successful response with the mutation."""
    if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128:
        raise ContractValidationError("Idempotency-Key is outside the published bounds")
    _Desired, _Snapshot, _Page, Idempotency = _get_models()
    request_sha256 = hashlib.sha256(canonical_json({"operation": operation, "body": request_body})).hexdigest()
    with session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"), {
            "lock_key": f"vfx-files-snapshot:{production_id}:{idempotency_key}"
        })
        prior = session.scalar(select(Idempotency).where(
            Idempotency.production_id == production_id,
            Idempotency.idempotency_key == idempotency_key,
        ).with_for_update())
        if prior is not None:
            if prior.actor_id != actor_id or prior.operation != operation or prior.request_sha256 != request_sha256:
                raise SnapshotConflict("Idempotency key was already used for a different snapshot request")
            return dict(prior.response_body), prior.response_status, prior.response_etag
        body, status, etag = apply()
        session.add(Idempotency(
            production_id=production_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            operation=operation,
            request_sha256=request_sha256,
            response_status=status,
            response_etag=etag,
            response_body=body,
        ))
        session.flush()
        return body, status, etag


def start_snapshot(
    session,
    *,
    production_id: UUID,
    snapshot_id: UUID,
    config_revision: int,
    source_etag: str,
    page_count: int,
    object_count: int,
    snapshot_sha256: str,
    expires_at: datetime,
):
    """Reserve an upload only against current desired configuration."""
    Desired, Snapshot, _Page, _Idempotency = _get_models()
    config = session.scalar(select(Desired).where(Desired.production_id == production_id).with_for_update())
    if config is None or config.config_revision != config_revision:
        raise StaleSnapshot("Snapshot configuration revision is stale")
    if not isinstance(source_etag, str) or not 1 <= len(source_etag) <= 200:
        raise ContractValidationError("Snapshot source ETag is invalid")
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
        raise ContractValidationError("Snapshot expiry must be a future UTC timestamp")
    existing = session.get(Snapshot, snapshot_id)
    if existing is not None:
        expected = (production_id, config_revision, source_etag, page_count, object_count, snapshot_sha256)
        actual = (existing.production_id, existing.config_revision, existing.source_etag, existing.page_count, existing.object_count, existing.hierarchy_sha256)
        if expected != actual:
            raise SnapshotConflict("Snapshot ID is already bound to different content")
        return existing
    same_content = session.scalar(select(Snapshot).where(
        Snapshot.production_id == production_id,
        Snapshot.config_revision == config_revision,
        Snapshot.hierarchy_sha256 == snapshot_sha256,
    ).with_for_update())
    if same_content is not None:
        raise SnapshotConflict("Snapshot content is already bound to another snapshot ID")
    row = Snapshot(
        id=snapshot_id,
        production_id=production_id,
        config_revision=config_revision,
        source_etag=source_etag,
        page_count=page_count,
        object_count=object_count,
        hierarchy_sha256=snapshot_sha256,
        state="uploading",
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return row


def put_snapshot_page(session, *, snapshot_id: UUID, page_index: int, objects, page_sha256: str):
    _Desired, Snapshot, Page, _Idempotency = _get_models()
    snapshot = session.scalar(select(Snapshot).where(Snapshot.id == snapshot_id).with_for_update())
    if snapshot is None or snapshot.state != "uploading":
        raise ContractValidationError("Snapshot is not accepting pages")
    if snapshot.expires_at <= datetime.now(timezone.utc):
        snapshot.state = "expired"
        session.flush()
        raise ContractValidationError("Snapshot has expired")
    if len(objects) < 1 or len(objects) > 500:
        raise ContractValidationError("Hierarchy page size is outside the published bounds")
    current = session.get(Page, (snapshot_id, page_index))
    verifier = HierarchySnapshot(
        page_count=snapshot.page_count,
        object_count=snapshot.object_count,
        full_sha256=snapshot.hierarchy_sha256,
    )
    # Existing page records are replayed first so duplicate bytes remain safe.
    for page in session.scalars(select(Page).where(Page.snapshot_id == snapshot_id).order_by(Page.page_index)):
        verifier.put_page(page.page_index, page.objects, page.page_sha256)
    verified = verifier.put_page(page_index, objects, page_sha256)
    if current is not None:
        return current
    row = Page(snapshot_id=snapshot_id, page_index=page_index, page_sha256=verified.sha256, objects=list(verified.objects))
    session.add(row)
    session.flush()
    return row


def complete_snapshot(
    session,
    *,
    snapshot_id: UUID,
    config_revision: int,
    page_count: int,
    object_count: int,
    snapshot_sha256: str,
):
    Desired, Snapshot, Page, _Idempotency = _get_models()
    snapshot = session.scalar(select(Snapshot).where(Snapshot.id == snapshot_id).with_for_update())
    if snapshot is None or snapshot.state not in {"uploading", "complete"}:
        raise ContractValidationError("Snapshot is not available for completion")
    if snapshot.state == "uploading" and snapshot.expires_at <= datetime.now(timezone.utc):
        snapshot.state = "expired"
        session.flush()
        raise ContractValidationError("Snapshot has expired")
    if (config_revision, page_count, object_count, snapshot_sha256) != (
        snapshot.config_revision, snapshot.page_count, snapshot.object_count, snapshot.hierarchy_sha256
    ):
        raise SnapshotConflict("Completion request does not match the reserved snapshot")
    pages = list(session.scalars(select(Page).where(Page.snapshot_id == snapshot_id).order_by(Page.page_index)))
    verifier = HierarchySnapshot(page_count=page_count, object_count=object_count, full_sha256=snapshot_sha256)
    for page in pages:
        verifier.put_page(page.page_index, page.objects, page.page_sha256)
    verified_objects = verifier.complete()
    validate_snapshot_production(verified_objects, str(snapshot.production_id))
    if snapshot.state == "uploading":
        config = session.scalar(select(Desired).where(Desired.production_id == snapshot.production_id).with_for_update())
        if config is None or config.config_revision != config_revision:
            snapshot.state = "rejected"
            session.flush()
            raise StaleSnapshot("Desired configuration changed while the snapshot was uploaded")
        snapshot.state = "complete"
        session.flush()
    return snapshot
