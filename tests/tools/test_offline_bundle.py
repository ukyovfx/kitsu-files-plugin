import json
import subprocess
from pathlib import Path

import pytest

from tools.build_offline_bundle import make_manifest


@pytest.fixture
def committed_distribution_inputs():
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    names = subprocess.check_output(
        ["git", "-C", str(root), "ls-tree", "-r", "-z", "--name-only", commit, "--", "plugin/"],
    ).split(b"\0")
    paths = sorted(name.decode("utf-8") for name in names if name)
    tracked = subprocess.check_output(
        ["git", "-C", str(root), "ls-tree", "-r", "--name-only", commit], text=True
    ).splitlines()
    required = [*paths, *(
        "environment/host-lock.yaml",
        "environment/patched-candidate-requirements.txt",
        "frontend/package.json",
        "frontend/package-lock.json",
        "docs/oss/WORKER-DISTRIBUTION.md",
        "docs/oss/THIRD_PARTY_NOTICES.draft.md",
        "docs/oss/distribution-sbom.cdx.json",
        "tools/build_offline_bundle.py",
        *subprocess.check_output(
            ["git", "-C", str(root), "ls-tree", "-r", "--name-only", commit, "--", "deploy/uat/"], text=True
        ).splitlines(),
    )]
    missing = set(required) - set(tracked)
    if missing:
        pytest.skip(f"distribution bundle inputs are not committed yet: {sorted(missing)}")
    files = {
        path: subprocess.check_output(["git", "-C", str(root), "show", f"{commit}:{path}"])
        for path in required
    }
    return files, commit


def test_manifest_reads_json_host_lock_and_keeps_python_frontend_pins(committed_distribution_inputs):
    files, commit = committed_distribution_inputs

    manifest = json.loads(make_manifest(files, commit))

    assert manifest["host"]["python"] == "3.10.12"
    assert manifest["frontend_dependency_identity"]["node"] == "v24.14.0"
    assert manifest["frontend_dependency_identity"]["npm"] == "11.11.0"
    assert manifest["frontend_dependency_identity"]["runtime_dependencies"] == {
        "@noble/hashes": "1.8.0",
        "vue": "3.5.43",
    }
