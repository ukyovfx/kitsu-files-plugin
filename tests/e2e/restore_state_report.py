"""Emit sanitized, hash-checked evidence for one synthetic restore test scope."""

import hashlib
import json
import os
from uuid import UUID

from sqlalchemy import select
from zou.app import create_app, db
from zou.app.models.output_file import OutputFile

from vfx_files.models import (
    ApprovalEvent,
    FolderBinding,
    Handoff,
    HandoffEvent,
    Job,
    LatestWorkingPointer,
    Publish,
    PublishPointer,
    SharedTemplateConfiguration,
    WorkingRevision,
    WorkingRevisionFile,
)
from vfx_files.storage.fake import PersistentFakeStorage


def main():
    production_id = UUID(os.environ["VFX_FILES_RESTORE_PRODUCTION_ID"])
    task_id = UUID(os.environ["VFX_FILES_RESTORE_TASK_ID"])
    fake_path = os.environ["VFX_FILES_FAKE_STORAGE_ROOT"]
    app = create_app()
    with app.app_context():
        storage = PersistentFakeStorage(
            fake_path,
            root_id=os.environ.get("VFX_FILES_FAKE_ROOT_ID", "synthetic-root-id"),
            drive_id=os.environ.get("VFX_FILES_FAKE_DRIVE_ID", "synthetic-drive-id"),
        )
        revisions = db.session.scalars(
            select(WorkingRevision).where(WorkingRevision.task_id == task_id).order_by(WorkingRevision.version)
        ).all()
        revision_ids = [row.id for row in revisions]
        working_files = db.session.scalars(
            select(WorkingRevisionFile).where(WorkingRevisionFile.revision_id.in_(revision_ids)).order_by(
                WorkingRevisionFile.revision_id, WorkingRevisionFile.ordinal
            )
        ).all() if revision_ids else []
        working_by_revision = {}
        for row in working_files:
            verified = bool(row.provider_object_id and storage.verify(
                row.provider_object_id, row.provider_size, row.provider_sha256
            ))
            working_by_revision.setdefault(str(row.revision_id), []).append({
                "id": str(row.id),
                "provider_object_id": row.provider_object_id,
                "size": row.provider_size,
                "sha256": row.provider_sha256,
                "verified": verified,
            })

        publishes = db.session.scalars(
            select(Publish).where(Publish.task_id == task_id).order_by(Publish.version)
        ).all()
        publish_ids = [row.id for row in publishes]
        approvals = db.session.scalars(
            select(ApprovalEvent).where(ApprovalEvent.publish_id.in_(publish_ids)).order_by(ApprovalEvent.created_at)
        ).all() if publish_ids else []
        handoffs = db.session.scalars(
            select(Handoff).where(Handoff.publish_id.in_(publish_ids)).order_by(Handoff.created_at)
        ).all() if publish_ids else []
        handoff_ids = [row.id for row in handoffs]
        handoff_events = db.session.scalars(
            select(HandoffEvent).where(HandoffEvent.handoff_id.in_(handoff_ids)).order_by(HandoffEvent.created_at)
        ).all() if handoff_ids else []
        pointers = db.session.scalars(select(PublishPointer).where(PublishPointer.task_id == task_id)).all()
        latest_working = db.session.get(LatestWorkingPointer, task_id)
        template = db.session.scalar(
            select(SharedTemplateConfiguration).where(SharedTemplateConfiguration.production_id == production_id)
        )
        bindings = db.session.scalars(
            select(FolderBinding).where(FolderBinding.canonical_scope_key.like(f"%{production_id}%"))
        ).all()
        jobs = [row for row in db.session.scalars(select(Job)).all()
                if str(row.payload.get("task_id", "")) == str(task_id)]

        publish_evidence = []
        for row in publishes:
            artifact_verified = bool(row.provider_artifact_id and storage.verify(
                row.provider_artifact_id, row.byte_count, row.sha256
            ))
            native_output_exists = bool(row.native_output_file_id and db.session.get(OutputFile, row.native_output_file_id))
            publish_evidence.append({
                "id": str(row.id),
                "version": row.version,
                "state": row.state,
                "qc_state": row.qc_state,
                "source_revision_file_id": str(row.source_revision_file_id) if row.source_revision_file_id else None,
                "provider_artifact_id": row.provider_artifact_id,
                "byte_count": row.byte_count,
                "sha256": row.sha256,
                "manifest_sha256": row.manifest_sha256,
                "manifest_bytes_sha256": hashlib.sha256(row.manifest_bytes or b"").hexdigest(),
                "native_output_file_id": str(row.native_output_file_id) if row.native_output_file_id else None,
                "native_output_exists": native_output_exists,
                "artifact_verified": artifact_verified,
            })

        evidence = {
            "production_id": str(production_id),
            "task_id": str(task_id),
            "template": {
                "id": str(template.id) if template else None,
                "version": template.version if template else None,
                "enabled": template.enabled if template else None,
                "tree_sha256": hashlib.sha256(json.dumps(template.template_tree, sort_keys=True).encode()).hexdigest() if template else None,
            },
            "folder_bindings": sorted(({
                "id": str(row.id),
                "scope_key": row.canonical_scope_key,
                "provider_folder_id": row.provider_folder_id,
                "state": row.state,
            } for row in bindings), key=lambda value: value["scope_key"]),
            "latest_working_revision_id": str(latest_working.revision_id) if latest_working else None,
            "working_revisions": [{
                "id": str(row.id),
                "version": row.version,
                "state": row.state,
                "manifest_sha256": row.manifest_sha256,
                "manifest_bytes_sha256": hashlib.sha256(row.manifest_bytes or b"").hexdigest(),
                "files": working_by_revision.get(str(row.id), []),
            } for row in revisions],
            "publishes": publish_evidence,
            "publish_pointers": sorted(({
                "output_family_id": str(row.output_family_id),
                "latest_published_id": str(row.latest_published_id) if row.latest_published_id else None,
                "latest_approved_id": str(row.latest_approved_id) if row.latest_approved_id else None,
            } for row in pointers), key=lambda value: value["output_family_id"]),
            "approvals": [{
                "id": str(row.id),
                "publish_id": str(row.publish_id),
                "actor_id": str(row.actor_id),
                "event_type": row.event_type,
                "manifest_sha256": row.manifest_sha256,
            } for row in approvals],
            "handoffs": [{
                "id": str(row.id),
                "publish_id": str(row.publish_id),
                "sender_id": str(row.sender_id),
                "recipient_id": str(row.recipient_id),
                "state": row.state,
                "events": [{
                    "event_type": event.event_type,
                    "actor_id": str(event.actor_id),
                    "feedback_sha256": hashlib.sha256((event.feedback or "").encode()).hexdigest(),
                } for event in handoff_events if event.handoff_id == row.id],
            } for row in handoffs],
            "jobs": sorted(({
                "id": str(row.id),
                "job_type": row.job_type,
                "state": row.state,
                "attempts": row.attempts,
                "lease_generation": row.lease_generation,
            } for row in jobs), key=lambda value: value["id"]),
            "storage_objects": sorted(({
                "object_id": object_id,
                "parent_id": item.parent_id,
                "size": item.size,
                "sha256": item.sha256,
                "mime_type": item.mime_type,
                "verified": bool(item.mime_type == "folder" or storage.verify(object_id, item.size, item.sha256)),
            } for object_id, item in storage.objects.items()), key=lambda value: value["object_id"]),
        }
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
