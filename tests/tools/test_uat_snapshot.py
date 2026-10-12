import hashlib
import json

import pytest

from deploy.uat.tools import snapshot
from deploy.uat.tools.snapshot import verify_snapshot


def make_snapshot(root, *, project="vfx-files-uat-source", source="a" * 40):
    (root / "database.dump").write_bytes(b"synthetic database")
    (root / "storage.tar").write_bytes(b"synthetic fake-storage")
    manifest = {
        "schema": "vfx-files-uat-snapshot/v1",
        "unit_project": project,
        "source_head": source,
        "contents": {
            name: {
                "bytes": (root / name).stat().st_size,
                "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
            }
            for name in ("database.dump", "storage.tar")
        },
    }
    payload = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    (root / "snapshot.json").write_bytes(payload)
    (root / "snapshot.sha256").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  snapshot.json\n", encoding="ascii"
    )


def test_snapshot_accepts_only_verified_payload_and_separate_target(tmp_path):
    make_snapshot(tmp_path)

    manifest, digest = verify_snapshot(tmp_path, "vfx-files-uat-restore", "a" * 12)

    assert manifest["unit_project"] == "vfx-files-uat-source"
    assert len(digest) == 64


@pytest.mark.parametrize("target,source_short", [
    ("vfx-files-uat-source", "a" * 12),
    ("vfx-files-uat-restore", "b" * 12),
])
def test_snapshot_refuses_same_project_or_different_source(tmp_path, target, source_short):
    make_snapshot(tmp_path)

    with pytest.raises(RuntimeError):
        verify_snapshot(tmp_path, target, source_short)


def test_snapshot_detects_payload_tampering(tmp_path):
    make_snapshot(tmp_path)
    (tmp_path / "storage.tar").write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="payload checksum mismatch"):
        verify_snapshot(tmp_path, "vfx-files-uat-restore", "a" * 12)


def test_snapshot_rejects_unexpected_payload_name(tmp_path):
    make_snapshot(tmp_path)
    manifest = json.loads((tmp_path / "snapshot.json").read_text())
    manifest["contents"]["../../credentials.txt"] = {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}
    payload = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    (tmp_path / "snapshot.json").write_bytes(payload)
    (tmp_path / "snapshot.sha256").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  snapshot.json\n", encoding="ascii"
    )

    with pytest.raises(RuntimeError, match="unsupported structure"):
        verify_snapshot(tmp_path, "vfx-files-uat-restore", "a" * 12)


def test_restore_waits_until_database_service_reports_healthy(monkeypatch, tmp_path):
    states = iter(("running|starting", "running|healthy"))
    monkeypatch.setattr(snapshot, "run_compose", lambda *args, **kwargs: b"synthetic-container\n")
    monkeypatch.setattr(snapshot.subprocess, "check_output", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(snapshot.time, "sleep", lambda _: None)

    snapshot.wait_service_healthy(tmp_path, tmp_path / "uat.env", "vfx-files-uat-restore", tmp_path / "compose.yaml", "postgres")

    assert list(states) == []
