"""Transactional persistence helpers for the KitsuSync hierarchy snapshot API.

HTTP authentication and route mounting are kept in the host adapter. These
functions assume their caller has already verified service identity, actor,
Production scope, and request idempotency.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from .v1 import ContractValidationError, HierarchySnapshot, SnapshotConflict


class StaleSnapshot(ContractValidationError):
    pass


def _get_models():
    from ..models import KitsuSyncDesiredConfiguration, KitsuSyncHierarchySnapshot, KitsuSyncHierarchySnapshotPage

    return KitsuSyncDesiredConfiguration, KitsuSyncHierarchySnapshot, KitsuSyncHierarchySnapshotPage


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
    Desired, Snapshot, _Page = _get_models()
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
    _Desired, Snapshot, Page = _get_models()
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
    Desired, Snapshot, Page = _get_models()
    snapshot = session.scalar(select(Snapshot).where(Snapshot.id == snapshot_id).with_for_update())
    if snapshot is None or snapshot.state != "uploading":
        raise ContractValidationError("Snapshot is not available for completion")
    if snapshot.expires_at <= datetime.now(timezone.utc):
        snapshot.state = "expired"
        session.flush()
        raise ContractValidationError("Snapshot has expired")
    config = session.scalar(select(Desired).where(Desired.production_id == snapshot.production_id).with_for_update())
    if config is None or config.config_revision != config_revision:
        snapshot.state = "rejected"
        session.flush()
        raise StaleSnapshot("Desired configuration changed while the snapshot was uploaded")
    if (config_revision, page_count, object_count, snapshot_sha256) != (
        snapshot.config_revision, snapshot.page_count, snapshot.object_count, snapshot.hierarchy_sha256
    ):
        raise SnapshotConflict("Completion request does not match the reserved snapshot")
    pages = list(session.scalars(select(Page).where(Page.snapshot_id == snapshot_id).order_by(Page.page_index)))
    verifier = HierarchySnapshot(page_count=page_count, object_count=object_count, full_sha256=snapshot_sha256)
    for page in pages:
        verifier.put_page(page.page_index, page.objects, page.page_sha256)
    verifier.complete()
    snapshot.state = "complete"
    session.flush()
    return snapshot
