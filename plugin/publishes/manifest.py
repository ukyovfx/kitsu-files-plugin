import hashlib
import json


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Stable UTF-8 JSON bytes used for signatures, pins and review evidence."""
    return json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def manifest_sha256(manifest_bytes: bytes) -> str:
    return hashlib.sha256(manifest_bytes).hexdigest()


def build_manifest(*, publish_id, task_id, output_family_id, version, artifact_id, byte_count, artifact_sha256, source_working_file_id=None):
    if byte_count < 0 or len(artifact_sha256) != 64:
        raise ValueError("Publish artifact evidence is incomplete")
    return {
        "schema": "vfx-files-manifest/1",
        "publish_id": str(publish_id),
        "task_id": str(task_id),
        "output_family_id": str(output_family_id),
        "publish_version": int(version),
        "source_working_file_id": str(source_working_file_id) if source_working_file_id else None,
        "artifact": {
            "provider_object_id": str(artifact_id),
            "byte_count": int(byte_count),
            "sha256": artifact_sha256,
            "immutability_policy": "application-managed; provider WORM not asserted",
        },
    }
