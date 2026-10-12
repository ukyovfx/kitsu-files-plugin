import pytest

from plugin.packages.manifest import build_frame_manifest, verify_provider_readback


def entry(frame, object_id):
    return {"frame_number": frame, "provider_object_id": object_id, "file_name": f"plate.{frame:04d}.exr", "byte_count": 12, "sha256": "a" * 64, "file_format": "exr"}


def test_sequence_manifest_is_one_version_with_explicit_range_and_missing_frames():
    manifest = build_frame_manifest([entry(1002, "drive-b"), entry(1000, "drive-a")], frame_start=1000, frame_end=1002, color_space="ACEScg")
    assert manifest["frame_count"] == 2
    assert manifest["missing_frames"] == [1001]
    assert [row["provider_object_id"] for row in manifest["files"]] == ["drive-a", "drive-b"]
    assert manifest["files"][0]["color_space"] == "ACEScg"


@pytest.mark.parametrize("entries", [
    [entry(1, "same"), entry(1, "other")],
    [entry(1, "same"), entry(2, "same")],
    [{**entry(1, "same"), "sha256": "unknown"}],
])
def test_sequence_manifest_rejects_ambiguous_or_unverified_files(entries):
    with pytest.raises(ValueError):
        build_frame_manifest(entries, frame_start=1, frame_end=2)


def test_sequence_readback_stays_on_provider_and_fails_on_checksum_mismatch():
    class Provider:
        def __init__(self, result): self.result = result
        def verify(self, object_id, size, digest): return self.result
    manifest = build_frame_manifest([entry(1, "drive-a")], frame_start=1, frame_end=1)
    assert verify_provider_readback(Provider(True), manifest)
    assert not verify_provider_readback(Provider(False), manifest)
