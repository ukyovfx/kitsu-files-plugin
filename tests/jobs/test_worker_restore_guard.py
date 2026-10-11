import json

import pytest

from plugin.worker_runtime import assert_restore_validation_complete


def test_worker_restore_guard_requires_a_matching_verified_manifest(tmp_path, monkeypatch):
    manifest_sha256 = "a" * 64
    marker_path = tmp_path / "restore-verified.json"
    monkeypatch.setenv("VFX_FILES_RESTORE_GUARD_PATH", str(marker_path))
    monkeypatch.setenv("VFX_FILES_RESTORE_EXPECTED_MANIFEST_SHA256", manifest_sha256)

    with pytest.raises(RuntimeError, match="integrity verification is required"):
        assert_restore_validation_complete()

    marker_path.write_text(json.dumps({"integrity_verified": True, "manifest_sha256": "b" * 64}))
    with pytest.raises(RuntimeError, match="integrity verification is required"):
        assert_restore_validation_complete()

    marker_path.write_text(json.dumps({"integrity_verified": True, "manifest_sha256": manifest_sha256}))
    assert_restore_validation_complete()


def test_worker_restore_guard_rejects_partial_configuration(monkeypatch):
    monkeypatch.setenv("VFX_FILES_RESTORE_EXPECTED_MANIFEST_SHA256", "a" * 64)
    monkeypatch.delenv("VFX_FILES_RESTORE_GUARD_PATH", raising=False)

    with pytest.raises(RuntimeError, match="integrity verification is required"):
        assert_restore_validation_complete()
