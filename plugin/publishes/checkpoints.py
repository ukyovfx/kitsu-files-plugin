"""Conditional durable publish state transitions and monotone pointers."""

import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from ..models import Publish, PublishCheckpoint, PublishPointer


CHECKPOINTS = (
    "reserved",
    "source_ready",
    "artifact_verified",
    "qc_recorded",
    "manifest_verified",
    "native_linked",
    "completed",
)


class PublishConflict(Exception):
    pass


@dataclass(frozen=True)
class PublishRef:
    id: object
    version: int
    state: str
    qc_state: str | None
    native_output_file_id: object | None
    native_revision: int | None


def _ref(publish):
    return PublishRef(publish.id, publish.version, publish.state, publish.qc_state, publish.native_output_file_id, publish.native_revision)


def evidence_bytes(evidence):
    return json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def advance_checkpoint(session_factory, publish_id, expected, next_state, evidence):
    if expected not in CHECKPOINTS or next_state not in CHECKPOINTS:
        raise PublishConflict("Unknown publish checkpoint")
    if CHECKPOINTS.index(next_state) != CHECKPOINTS.index(expected) + 1:
        raise PublishConflict("Publish checkpoints must advance exactly one stage")
    evidence_bytes_value = evidence_bytes(evidence)
    evidence_hash = hashlib.sha256(evidence_bytes_value).hexdigest()
    with session_factory.begin() as session:
        publish = session.scalar(select(Publish).where(Publish.id == publish_id).with_for_update())
        if publish is None:
            raise PublishConflict("Publish does not exist")
        if publish.state == next_state:
            existing = session.scalar(select(PublishCheckpoint).where(PublishCheckpoint.publish_id == publish_id, PublishCheckpoint.checkpoint == next_state))
            if existing is not None and existing.evidence_sha256 == evidence_hash:
                return _ref(publish)
            raise PublishConflict("Repeated checkpoint has different evidence")
        if publish.state != expected:
            raise PublishConflict("Publish state changed before checkpoint")
        if next_state in ("manifest_verified", "native_linked", "completed") and publish.qc_state != "passed":
            raise PublishConflict("Failed QC cannot advance toward completion")
        _validate_checkpoint(next_state, evidence)
        session.add(PublishCheckpoint(
            id=uuid4(),
            publish_id=publish_id,
            checkpoint=next_state,
            evidence=evidence,
            evidence_sha256=evidence_hash,
        ))
        publish.state = next_state
        if next_state == "artifact_verified":
            publish.provider_artifact_id = evidence["provider_object_id"]
            publish.byte_count = evidence["byte_count"]
            publish.sha256 = evidence["sha256"]
        elif next_state == "qc_recorded":
            publish.qc_state = "passed" if evidence["passed"] else "failed"
        elif next_state == "manifest_verified":
            publish.manifest_sha256 = evidence["manifest_sha256"]
            publish.manifest_bytes = bytes.fromhex(evidence["manifest_bytes_hex"])
        elif next_state == "native_linked":
            publish.native_output_file_id = evidence["native_output_file_id"]
            publish.native_revision = evidence["native_revision"]
        session.flush()
        return _ref(publish)


def _validate_checkpoint(state, evidence):
    if state == "source_ready":
        staged_source = evidence.get("staging_key") and evidence.get("staging_readback_verified")
        working_source = evidence.get("source_revision_file_id") and evidence.get("provider_readback_verified")
        if not ((staged_source or working_source) and evidence.get("sha256") and evidence.get("byte_count") is not None):
            raise PublishConflict("Source checkpoint needs durable staged or verified Working File evidence")
    if state == "artifact_verified" and not (evidence.get("provider_object_id") and evidence.get("readback_verified") and evidence.get("sha256") and evidence.get("byte_count") is not None):
        raise PublishConflict("Artifact checkpoint needs provider readback evidence")
    if state == "qc_recorded" and not (evidence.get("policy_id") and evidence.get("policy_sha256") and isinstance(evidence.get("passed"), bool)):
        raise PublishConflict("QC checkpoint must record policy and outcome")
    if state == "manifest_verified":
        manifest = bytes.fromhex(evidence.get("manifest_bytes_hex", ""))
        if hashlib.sha256(manifest).hexdigest() != evidence.get("manifest_sha256"):
            raise PublishConflict("Manifest hash does not match exact bytes")
    if state == "native_linked" and not (evidence.get("native_output_file_id") and evidence.get("native_revision", 0) > 0):
        raise PublishConflict("Native link checkpoint needs OutputFile ID and native revision")
    if state == "completed" and evidence.get("eligible") is not True:
        raise PublishConflict("Only an eligible Publish can complete")


def advance_latest_published(session_factory, publish_id):
    with session_factory.begin() as session:
        candidate = session.scalar(select(Publish).where(Publish.id == publish_id).with_for_update())
        if candidate is None or candidate.state != "completed" or candidate.qc_state != "passed":
            raise PublishConflict("Latest Published requires a completed Publish with passing QC")
        key = {"task_id": candidate.task_id, "output_family_id": candidate.output_family_id}
        session.execute(insert(PublishPointer).values(**key).on_conflict_do_nothing())
        pointer = session.scalar(
            select(PublishPointer)
            .where(PublishPointer.task_id == candidate.task_id, PublishPointer.output_family_id == candidate.output_family_id)
            .with_for_update()
        )
        current = session.scalar(select(Publish).where(Publish.id == pointer.latest_published_id)) if pointer.latest_published_id else None
        if current is None or candidate.version > current.version:
            pointer.latest_published_id = candidate.id
