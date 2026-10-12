"""Build bounded metadata manifests for immutable render and data packages.

The helper consumes provider object IDs and checksums; it does not download or
copy the media through Kitsu/Zou. Frame numbering is supplied explicitly by the
ingest contract and is never used to infer package or version identity.
"""

from __future__ import annotations

import re
import json
import hashlib
from typing import Iterable


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def build_frame_manifest(entries: Iterable[dict], *, frame_start: int, frame_end: int, color_space: str | None = None) -> dict:
    if (
        isinstance(frame_start, bool) or not isinstance(frame_start, int)
        or isinstance(frame_end, bool) or not isinstance(frame_end, int)
        or frame_end < frame_start
    ):
        raise ValueError("Frame range must be an ordered integer range")
    files = []
    seen_frames = set()
    seen_objects = set()
    for entry in entries:
        frame = entry.get("frame_number")
        size = entry.get("byte_count")
        object_id = entry.get("provider_object_id")
        sha256 = entry.get("sha256")
        name = entry.get("file_name")
        if isinstance(frame, bool) or not isinstance(frame, int) or not frame_start <= frame <= frame_end:
            raise ValueError("Every sequence file must have an explicit frame number in the declared range")
        if frame in seen_frames:
            raise ValueError("Sequence contains duplicate frame numbers")
        if not isinstance(object_id, str) or not object_id or object_id in seen_objects:
            raise ValueError("Sequence provider object IDs must be unique and non-empty")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Sequence file names must be non-empty")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("Sequence file byte counts must be non-negative integers")
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise ValueError("Every sequence object requires a SHA-256 checksum")
        seen_frames.add(frame)
        seen_objects.add(object_id)
        files.append({
            "frame_number": frame,
            "file_name": name,
            "provider_object_id": object_id,
            "byte_count": size,
            "sha256": sha256.lower(),
            "file_format": entry.get("file_format"),
            "color_space": entry.get("color_space") or color_space,
        })
    files.sort(key=lambda item: item["frame_number"])
    missing = sorted(set(range(frame_start, frame_end + 1)) - seen_frames)
    return {
        "frame_start": frame_start,
        "frame_end": frame_end,
        "frame_count": len(files),
        "missing_frames": missing,
        "files": files,
    }


def verify_provider_readback(adapter, manifest: dict) -> bool:
    """Verify each existing provider object without routing bytes via Zou."""
    for item in manifest["files"]:
        if not adapter.verify(item["provider_object_id"], item["byte_count"], item["sha256"]):
            return False
    return True


def canonical_package_manifest(*, output_type_id: str, package_type: str, files: Iterable[dict], frame_start: int | None = None, frame_end: int | None = None, color_space: str | None = None) -> tuple[dict, bytes, str]:
    """Validate and canonically encode an immutable package snapshot.

    Provider IDs and expected checksums are explicit inputs. Names never
    determine package or version identity. The caller must resolve provider
    metadata and root ancestry before reserving this manifest.
    """
    if not isinstance(output_type_id, str) or not _is_uuid(output_type_id):
        raise ValueError("A stable Kitsu Output Type UUID is required")
    if not isinstance(package_type, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", package_type):
        raise ValueError("Package type must be a bounded identifier")
    entries = list(files)
    if not entries or len(entries) > 10000:
        raise ValueError("A package must contain between 1 and 10000 files")
    seen_objects = set()
    normalized = []
    for ordinal, entry in enumerate(entries):
        object_id = entry.get("source_provider_object_id") or entry.get("provider_object_id")
        destination_object_id = entry.get("destination_provider_object_id")
        name = entry.get("file_name")
        size = entry.get("byte_count")
        sha256 = entry.get("sha256")
        if not isinstance(object_id, str) or not object_id or len(object_id) > 255 or object_id in seen_objects:
            raise ValueError("Package provider object IDs must be unique and non-empty")
        if not isinstance(name, str) or not name.strip() or len(name) > 1024:
            raise ValueError("Package file names must be non-empty and bounded")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("Package byte counts must be non-negative integers")
        if sha256 is not None and (not isinstance(sha256, str) or not _SHA256.fullmatch(sha256)):
            raise ValueError("Package SHA-256 checksums must be valid hexadecimal values")
        frame = entry.get("frame_number")
        if frame is not None and (isinstance(frame, bool) or not isinstance(frame, int)):
            raise ValueError("Frame numbers must be integers")
        seen_objects.add(object_id)
        is_proxy = entry.get("is_proxy", False)
        if not isinstance(is_proxy, bool):
            raise ValueError("is_proxy must be a boolean")
        normalized.append({
            "ordinal": ordinal,
            "source_provider_object_id": object_id,
            "provider_object_id": destination_object_id,
            "file_name": name,
            "byte_count": size,
            "sha256": sha256.lower() if sha256 else None,
            "frame_number": frame,
            "file_format": _bounded_optional(entry.get("file_format"), 32, "file_format"),
            "color_space": _bounded_optional(entry.get("color_space") or color_space, 128, "color_space"),
            "is_proxy": is_proxy,
        })
    frame_values = [item["frame_number"] for item in normalized if item["frame_number"] is not None]
    if frame_values and len(frame_values) != len(normalized):
        raise ValueError("A sequence package must assign an explicit frame number to every file")
    if frame_values:
        if frame_start is None or frame_end is None:
            raise ValueError("Sequence packages require an explicit frame range")
        if (isinstance(frame_start, bool) or not isinstance(frame_start, int)
                or isinstance(frame_end, bool) or not isinstance(frame_end, int)
                or frame_end < frame_start or frame_end - frame_start > 10000):
            raise ValueError("Declared frame range is too large")
        frames = [item["frame_number"] for item in normalized]
        if any(not frame_start <= frame <= frame_end for frame in frames):
            raise ValueError("Sequence frame number falls outside the declared range")
        if len(set(frames)) != len(frames):
            raise ValueError("Sequence contains duplicate frame numbers")
        missing_frames = sorted(set(range(frame_start, frame_end + 1)) - set(frames))
    else:
        if frame_start is not None or frame_end is not None:
            raise ValueError("Frame range is only valid for a sequence package")
        missing_frames = []
    manifest = {
        "schema": "vfx-output-package/v1",
        "output_type_id": output_type_id,
        "package_type": package_type,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "missing_frames": missing_frames,
        "files": normalized,
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return manifest, encoded, hashlib.sha256(encoded).hexdigest()


def evaluate_package_qc(manifest: dict) -> tuple[str, dict]:
    """Run deterministic metadata QC after all provider objects read back."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list) or not manifest["files"]:
        raise ValueError("Output Package manifest must contain files")
    files = manifest["files"]
    ids = [item.get("provider_object_id") for item in files]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Output Package manifest has duplicate or empty provider IDs")
    for item in files:
        if (isinstance(item.get("byte_count"), bool) or not isinstance(item.get("byte_count"), int)
                or item["byte_count"] < 0 or not isinstance(item.get("sha256"), str)
                or not _SHA256.fullmatch(item["sha256"])):
            raise ValueError("Output Package manifest contains invalid size or checksum metadata")
    start, end = manifest.get("frame_start"), manifest.get("frame_end")
    if start is None and end is None:
        if any(item.get("frame_number") is not None for item in files) or manifest.get("missing_frames") != []:
            raise ValueError("Non-sequence Output Package contains frame metadata")
        missing = []
    else:
        if (isinstance(start, bool) or not isinstance(start, int) or isinstance(end, bool)
                or not isinstance(end, int) or end < start or end - start > 10000):
            raise ValueError("Output Package frame range is invalid or exceeds the bound")
        frames = [item.get("frame_number") for item in files]
        if any(isinstance(value, bool) or not isinstance(value, int) or not start <= value <= end for value in frames):
            raise ValueError("Output Package frame number is absent or outside its declared range")
        if len(set(frames)) != len(frames):
            raise ValueError("Output Package contains duplicate frame numbers")
        missing = sorted(set(range(start, end + 1)) - set(frames))
        if manifest.get("missing_frames") != missing:
            raise ValueError("Output Package missing-frame summary does not match file entries")
    total_bytes = sum(item["byte_count"] for item in files)
    return ("failed" if missing else "passed", {
        "policy": "vfx-output-package-integrity-v1",
        "provider_readback": "passed",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "missing_frames": missing,
        "sequence_qc": "failed" if missing else ("passed" if start is not None else "not_applicable"),
    })


def _is_uuid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", value))


def _bounded_optional(value, maximum: int, field: str):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{field} must be a bounded string")
    return value
