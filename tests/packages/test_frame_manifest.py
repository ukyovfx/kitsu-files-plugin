import pytest

from plugin.packages.manifest import build_frame_manifest, canonical_package_manifest, evaluate_package_qc, verify_provider_readback


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


def test_canonical_package_manifest_records_sequence_metadata_and_stable_digest():
    import hashlib
    import json
    output_type = "f675f54e-1810-4b4a-985f-212396ca54b2"
    manifest, body, digest = canonical_package_manifest(
        output_type_id=output_type,
        package_type="final-exr",
        files=[entry(1002, "drive-2"), entry(1000, "drive-0")],
        frame_start=1000,
        frame_end=1002,
        color_space="ACEScg",
    )
    assert manifest["missing_frames"] == [1001]
    assert [item["frame_number"] for item in manifest["files"]] == [1002, 1000]
    assert [item["source_provider_object_id"] for item in manifest["files"]] == ["drive-2", "drive-0"]
    assert json.loads(body)["output_type_id"] == output_type
    assert digest == hashlib.sha256(body).hexdigest()
    assert len(body) < 4096


def test_canonical_package_manifest_rejects_duplicate_objects_and_untyped_proxy_flags():
    from copy import deepcopy
    output_type = "f675f54e-1810-4b4a-985f-212396ca54b2"
    original = entry(1, "drive-1")
    duplicate = deepcopy(original)
    with pytest.raises(ValueError, match="unique"):
        canonical_package_manifest(output_type_id=output_type, package_type="tracking", files=[original, duplicate])
    with pytest.raises(ValueError, match="boolean"):
        canonical_package_manifest(
            output_type_id=output_type,
            package_type="tracking",
            files=[{**original, "frame_number": None, "is_proxy": "false"}],
        )


def test_canonical_package_manifest_requires_complete_explicit_sequence_metadata():
    output_type = "f675f54e-1810-4b4a-985f-212396ca54b2"
    with pytest.raises(ValueError, match="frame range"):
        canonical_package_manifest(output_type_id=output_type, package_type="low-res", files=[entry(1, "drive-1")])
    with pytest.raises(ValueError, match="every file"):
        canonical_package_manifest(
            output_type_id=output_type,
            package_type="low-res",
            files=[entry(1, "drive-1"), {**entry(2, "drive-2"), "frame_number": None}],
            frame_start=1,
            frame_end=2,
        )


def test_package_qc_fails_for_gaps_and_detects_tampered_frame_summary():
    output_type = "f675f54e-1810-4b4a-985f-212396ca54b2"
    manifest, _body, _digest = canonical_package_manifest(
        output_type_id=output_type, package_type="final-exr",
        files=[entry(1000, "drive-0"), entry(1002, "drive-2")],
        frame_start=1000, frame_end=1002,
    )
    for item in manifest["files"]:
        item["provider_object_id"] = item["source_provider_object_id"]
    state, report = evaluate_package_qc(manifest)
    assert state == "failed"
    assert report["missing_frames"] == [1001]
    assert report["total_bytes"] == 24
    manifest["missing_frames"] = []
    with pytest.raises(ValueError, match="summary"):
        evaluate_package_qc(manifest)


def test_package_qc_rejects_duplicate_frame_numbers_even_if_ids_differ():
    output_type = "f675f54e-1810-4b4a-985f-212396ca54b2"
    with pytest.raises(ValueError, match="duplicate frame"):
        canonical_package_manifest(
            output_type_id=output_type, package_type="final-exr",
            files=[entry(1, "drive-1"), entry(1, "drive-2")],
            frame_start=1, frame_end=1,
        )


def test_drive_listing_may_reserve_package_without_sha_until_worker_readback():
    output_type = "f675f54e-1810-4b4a-985f-212396ca54b2"
    manifest, _body, _digest = canonical_package_manifest(
        output_type_id=output_type,
        package_type="camera-data",
        files=[{**entry(1, "drive-unknown-sha"), "frame_number": None, "sha256": None}],
    )
    assert manifest["files"][0]["sha256"] is None
    manifest["files"][0]["provider_object_id"] = "copied-drive-object"
    with pytest.raises(ValueError, match="checksum"):
        evaluate_package_qc(manifest)
