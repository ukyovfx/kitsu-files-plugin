import hashlib
import subprocess
import zipfile
from pathlib import Path

import pytest

from tools.build_zou_plugin_package import build, committed_plugin_files, exact_commit


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_package_uses_exact_commit_root_layout_and_readback(tmp_path):
    root = Path(__file__).resolve().parents[2]
    commit = _git(root, "rev-parse", "HEAD")
    output, sidecar, manifest = build(root, commit, tmp_path / "vfx_files.zip")

    assert manifest["source_commit"] == commit
    assert manifest["plugin"]["id"] == "vfx_files"
    assert manifest["migrations_in_order"] == [f"vxf_000{i}" for i in range(1, 7)]
    assert "manifest.toml" in zipfile.ZipFile(output).namelist()
    assert not any(name.startswith("plugin/") for name in zipfile.ZipFile(output).namelist())
    assert sidecar.exists()
    assert hashlib.sha256(output.read_bytes()).hexdigest()

    second, second_sidecar, _ = build(root, commit, tmp_path / "vfx_files-copy.zip")
    assert second.read_bytes() == output.read_bytes()
    assert second_sidecar.read_bytes() == sidecar.read_bytes()
    with pytest.raises(FileExistsError):
        build(root, commit, output)


def test_package_ignores_untracked_overlay_and_dirty_worktree(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "plugin").mkdir()
    (root / "plugin" / "manifest.toml").write_text('id = "vfx_files"\nversion = "1.0.0"\n')
    (root / "plugin" / "__init__.py").write_text("routes = []\n")
    (root / "plugin" / "migrations" / "versions").mkdir(parents=True)
    (root / "plugin" / "migrations" / "versions" / "one.py").write_text("revision = 'vxf_0001'\ndown_revision = None\n")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", "plugin")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    commit = _git(root, "rev-parse", "HEAD")
    (root / "plugin" / "untracked.secret").write_text("must not ship")
    (root / "plugin" / "manifest.toml").write_text('id = "wrong"\nversion = "9.9.9"\n')

    files = committed_plugin_files(root, commit)

    assert "untracked.secret" not in files
    assert files["manifest.toml"].startswith(b'id = "vfx_files"')


def test_source_requires_an_exact_full_commit_sha(tmp_path):
    root = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="exact 40-character"):
        exact_commit(root, "HEAD")
