"""Build bounded metadata manifests for immutable render and data packages.

The helper consumes provider object IDs and checksums; it does not download or
copy the media through Kitsu/Zou. Frame numbering is supplied explicitly by the
ingest contract and is never used to infer package or version identity.
"""

from __future__ import annotations

import re
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
