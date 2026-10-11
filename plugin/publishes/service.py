"""Publish numbering and one-shot native OutputFile permits."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from ..models import (
    OutputFamilyMapping,
    Publish,
    PublishCheckpoint,
    PublishPointer,
    ReconciliationFinding,
    NativeCreateEvent,
)
from .checkpoints import PublishConflict, PublishRef


@dataclass(frozen=True)
class NativeCreatePermit:
    publish_id: UUID
    task_id: UUID
    output_family_id: UUID
    version: int
    source_working_file_id: UUID | None
    actor_id: UUID
    family_mapping: dict


@dataclass(frozen=True)
class ReconciliationProof:
    original_worker_terminated: bool
    authoritative_native_absence: bool
    evidence_reference: str


def reserve_publish(session_factory, *, task_id: UUID, output_family_id: UUID, source_working_file_id: UUID | None, actor_id: UUID, idempotency_key: str | None = None) -> PublishRef:
    from zou.app.models.task import Task
    from zou.app.models.working_file import WorkingFile

    with session_factory.begin() as session:
        if idempotency_key:
            existing = session.scalar(select(Publish).where(Publish.idempotency_key == idempotency_key))
            if existing is not None:
                if (existing.task_id, existing.output_family_id, existing.source_working_file_id, existing.created_by) == (task_id, output_family_id, source_working_file_id, actor_id):
                    return PublishRef(existing.id, existing.version, existing.state, existing.qc_state, existing.native_output_file_id, existing.native_revision)
                raise PublishConflict("Publish idempotency key is already used")
        task = session.scalar(select(Task).where(Task.id == task_id))
        family = session.scalar(select(OutputFamilyMapping).where(OutputFamilyMapping.id == output_family_id, OutputFamilyMapping.enabled.is_(True)))
        if task is None or family is None or task.project_id != family.production_id:
            raise PublishConflict("Task and output family are outside the resolved Production")
        if source_working_file_id is not None:
            working = session.scalar(select(WorkingFile).where(WorkingFile.id == source_working_file_id, WorkingFile.task_id == task_id))
            if working is None:
                raise PublishConflict("Source WorkingFile does not belong to this Task")

        key = {"task_id": task_id, "output_family_id": output_family_id}
        session.execute(insert(PublishPointer).values(**key).on_conflict_do_nothing())
        session.scalar(
            select(PublishPointer)
            .where(PublishPointer.task_id == task_id, PublishPointer.output_family_id == output_family_id)
            .with_for_update()
        )
        latest_version = session.scalar(
            select(func.max(Publish.version)).where(Publish.task_id == task_id, Publish.output_family_id == output_family_id)
        ) or 0
        publish = Publish(
            id=uuid4(),
            task_id=task_id,
            output_family_id=output_family_id,
            version=latest_version + 1,
            idempotency_key=idempotency_key,
            source_working_file_id=source_working_file_id,
            created_by=actor_id,
            state="reserved",
            native_create_permit_used=False,
            native_retry_count=0,
        )
        session.add(publish)
        session.flush()
        return PublishRef(publish.id, publish.version, publish.state, publish.qc_state, publish.native_output_file_id, publish.native_revision)


def consume_native_create_attempt(session_factory, *, publish_id: UUID, worker_id: UUID, generation: int) -> NativeCreatePermit:
    """Commit the one-shot permit before invoking Zou; there is no automatic retry."""
    from ..models import Job

    with session_factory.begin() as session:
        publish = session.scalar(select(Publish).where(Publish.id == publish_id).with_for_update())
        if publish is None or publish.state != "manifest_verified" or publish.qc_state != "passed":
            raise PublishConflict("Publish is not eligible for native OutputFile creation")
        if publish.native_create_permit_used:
            raise PublishConflict("Native OutputFile create permit was already consumed")
        job = session.scalar(
            select(Job).where(
                Job.state == "leased",
                Job.lease_owner == worker_id,
                Job.lease_generation == generation,
                Job.lease_expires_at > func.now(),
                Job.payload["publish_id"].astext == str(publish_id),
            ).with_for_update()
        )
        if job is None:
            raise PublishConflict("Worker lease is stale")
        family = session.scalar(select(OutputFamilyMapping).where(OutputFamilyMapping.id == publish.output_family_id))
        if family is None or not family.enabled:
            raise PublishConflict("Native output family mapping is disabled")
        publish.native_create_permit_used = True
        session.flush()
        mapping = {
            "output_type_id": str(family.output_type_id),
            "task_type_id": str(family.task_type_id),
            "name": family.name,
            "representation": family.representation,
            "extension": family.extension,
            "file_status_id": str(family.file_status_id),
        }
        return NativeCreatePermit(
            publish_id=publish.id,
            task_id=publish.task_id,
            output_family_id=publish.output_family_id,
            version=publish.version,
            source_working_file_id=publish.source_working_file_id,
            actor_id=publish.created_by,
            family_mapping=mapping,
        )


def mark_native_create_ambiguous(session_factory, *, publish_id: UUID, evidence_reference: str):
    with session_factory.begin() as session:
        publish = session.scalar(select(Publish).where(Publish.id == publish_id).with_for_update())
        if publish is None or not publish.native_create_permit_used or publish.native_output_file_id is not None:
            raise PublishConflict("Publish cannot enter native-create ambiguity")
        publish.state = "native_create_ambiguous"
        session.add(NativeCreateEvent(
            publish_id=publish_id,
            event_type="ambiguous",
            evidence={"evidence_reference": evidence_reference},
        ))
        session.add(ReconciliationFinding(
            object_type="native_output_create",
            kitsu_object_id=publish_id,
            state="reconcile_required",
            evidence={"publish_id": str(publish_id), "evidence_reference": evidence_reference},
        ))


def authorize_native_create_retry(session_factory, *, actor_role: str, actor_id: UUID, publish_id: UUID, proof: ReconciliationProof):
    if actor_role not in ("admin", "manager"):
        raise PublishConflict("Only a Production manager may authorize native-create reconciliation")
    if not (proof.original_worker_terminated and proof.authoritative_native_absence and proof.evidence_reference):
        raise PublishConflict("Manual retry requires terminated worker and authoritative OutputFile absence")
    with session_factory.begin() as session:
        publish = session.scalar(select(Publish).where(Publish.id == publish_id).with_for_update())
        if publish is None or publish.state != "native_create_ambiguous" or publish.native_output_file_id is not None:
            raise PublishConflict("Publish is not in an ambiguous native-create state")
        if publish.native_retry_count >= 3:
            raise PublishConflict("Native OutputFile retry limit reached")
        publish.native_retry_count += 1
        publish.native_create_permit_used = False
        publish.state = "manifest_verified"
        session.add(ReconciliationFinding(
            object_type=f"native_retry_authorized_{publish.native_retry_count}",
            kitsu_object_id=publish_id,
            state="authorized",
            evidence={
                "actor_id": str(actor_id),
                "publish_id": str(publish_id),
                "original_worker_terminated": proof.original_worker_terminated,
                "authoritative_native_absence": proof.authoritative_native_absence,
                "evidence_reference": proof.evidence_reference,
            },
        ))
        session.add(NativeCreateEvent(
            publish_id=publish_id,
            event_type="manual_retry_authorized",
            actor_id=actor_id,
            evidence={
                "retry_number": publish.native_retry_count,
                "original_worker_terminated": proof.original_worker_terminated,
                "authoritative_native_absence": proof.authoritative_native_absence,
                "evidence_reference": proof.evidence_reference,
            },
        ))
