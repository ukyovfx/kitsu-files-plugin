#!/usr/bin/env python3
"""Build and verify a deterministic, review-only VFX Files handoff bundle.

All source bytes are read from a Git commit, never from the working tree. This
keeps untracked overlays, local databases, credentials, and generated test
outputs outside the bundle by construction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path


REPOSITORY = "ukyovfx/vfx-kitsu-files-plugin"
SUPPORT_FILES = (
    "environment/host-lock.yaml",
    "environment/patched-candidate-requirements.txt",
    "frontend/package.json",
    "frontend/package-lock.json",
    "docs/oss/WORKER-DISTRIBUTION.md",
    "docs/oss/THIRD_PARTY_NOTICES.draft.md",
    "docs/oss/distribution-sbom.cdx.json",
    "tools/build_offline_bundle.py",
)
SUPPORT_DIRS = ("deploy/uat/",)
MANIFEST_PATH = "META/bundle-manifest.json"
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def source_files(root: Path, commit: str) -> dict[str, bytes]:
    names = git(root, "ls-tree", "-r", "-z", "--name-only", commit, "--", "plugin/")
    paths = sorted(item.decode("utf-8") for item in names.split(b"\0") if item)
    if not paths or any(not path.startswith("plugin/") for path in paths):
        raise ValueError("Git source does not contain the expected plugin package")

    tracked = set(git(root, "ls-tree", "-r", "--name-only", commit).decode("utf-8").splitlines())
    for path in SUPPORT_FILES:
        if path not in tracked:
            raise ValueError(f"Required tracked distribution input is missing at {commit}: {path}")

    result = {path: git(root, "show", f"{commit}:{path}") for path in paths}
    result.update({path: git(root, "show", f"{commit}:{path}") for path in SUPPORT_FILES})
    result.update({
        path: git(root, "show", f"{commit}:{path}")
        for path in sorted(tracked)
        if any(path.startswith(directory) for directory in SUPPORT_DIRS)
    })
    return result


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def make_manifest(files: dict[str, bytes], commit: str) -> bytes:
    package = toml_value(files["plugin/manifest.toml"].decode("utf-8"), "version")
    host_lock = json.loads(files["environment/host-lock.yaml"])
    host_version = str(host_lock["python_version"])
    node_version = str(host_lock["node_version"])
    npm_version = str(host_lock["npm_version"])
    frontend = json.loads(files["frontend/package.json"])
    sbom = json.loads(files["docs/oss/distribution-sbom.cdx.json"])

    plugin_paths = {name for name in files if name.startswith("plugin/")}
    sbom_components = {
        component["name"]: component
        for component in sbom.get("components", [])
        if component.get("type") == "file" and component.get("name", "").startswith("plugin/")
    }
    sbom_paths = set(sbom_components)
    if plugin_paths != sbom_paths:
        missing = sorted(plugin_paths - sbom_paths)
        stale = sorted(sbom_paths - plugin_paths)
        raise ValueError(f"Distribution SBOM file inventory mismatch: missing={missing}, stale={stale}")
    for path in sorted(plugin_paths):
        sbom_hashes = sbom_components[path].get("hashes", [])
        recorded = next((item.get("content") for item in sbom_hashes if item.get("alg") == "SHA-256"), None)
        if recorded != sha256(files[path]):
            raise ValueError(f"Distribution SBOM SHA-256 does not match Git source: {path}")

    runtime_dependencies = frontend.get("dependencies", {})
    sbom_packages = {
        (component.get("name"), component.get("version"))
        for component in sbom.get("components", [])
        if component.get("type") == "library"
    }
    absent = [name for name, version in runtime_dependencies.items() if (name, version) not in sbom_packages]
    if absent:
        raise ValueError(f"Runtime dependency absent from distribution SBOM: {absent}")

    document = {
        "schema": "vfx-files-offline-bundle/v1",
        "distribution_status": "review-only; unsigned; not approved for public redistribution or deployment",
        "repository": REPOSITORY,
        "source_commit": commit,
        "plugin": {"id": "vfx_files", "version": package},
        "host": {
            "lock_file": "environment/host-lock.yaml",
            "lock_sha256": sha256(files["environment/host-lock.yaml"]),
            "python": host_version,
            "official_host_security_status": "FAIL; see pinned host audit",
            "patched_candidate": "unofficial isolated test candidate only",
        },
        "python_dependency_identity": {
            "plugin_dependencies": "provided by the host; no standalone plugin Python requirements are bundled",
            "candidate_override_lock": "environment/patched-candidate-requirements.txt",
            "candidate_override_lock_sha256": sha256(files["environment/patched-candidate-requirements.txt"]),
            "candidate_overrides_are_not_a_supported_host_lock": True,
        },
        "frontend_dependency_identity": {
            "package": frontend["name"],
            "version": frontend["version"],
            "node": node_version,
            "npm": npm_version,
            "package_json_sha256": sha256(files["frontend/package.json"]),
            "package_lock": "frontend/package-lock.json",
            "package_lock_sha256": sha256(files["frontend/package-lock.json"]),
            "runtime_dependencies": runtime_dependencies,
        },
        "uat_package": {
            "compose": "deploy/uat/compose.yaml",
            "isolated_network": "internal; no external route; UI bind limited to loopback",
            "data_services": ["pinned PostgreSQL", "pinned Redis", "persistent FakeStorage", "staging"],
            "api_worker_source": "one image built from this exact source commit",
            "resource_limits": "CPU/RAM/PIDs are configured per container; named-volume disk quota is not provided",
            "candidate_host": "unofficial patched candidate only; not Production-approved",
        },
        "sbom": "docs/oss/distribution-sbom.cdx.json",
        "third_party_notices": "docs/oss/THIRD_PARTY_NOTICES.draft.md (draft; rights-holder review required)",
        "files": [
            {"path": name, "size": len(payload), "sha256": sha256(payload)}
            for name, payload in sorted(files.items())
        ],
    }
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def toml_value(document: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}\s*=\s*\"([^\"]+)\"\s*$", document)
    if not match:
        raise ValueError(f"Could not read {key} from plugin manifest")
    return match.group(1)


def write_zip(path: Path, files: dict[str, bytes], manifest: bytes) -> None:
    entries = {**files, MANIFEST_PATH: manifest}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(entries.items()):
            info = zipfile.ZipInfo(f"vfx-files/{name}", date_time=FIXED_TIME)
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, payload)


def verify_zip(path: Path, files: dict[str, bytes], commit: str) -> str:
    with zipfile.ZipFile(path, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"Archive CRC check failed at {bad}")
        entries = {name: archive.read(name) for name in archive.namelist()}
    prefix = "vfx-files/"
    expected = {f"{prefix}{name}" for name in files} | {f"{prefix}{MANIFEST_PATH}"}
    if set(entries) != expected:
        raise ValueError("Archive entry set differs from the locked distribution inputs")
    manifest_bytes = entries[f"{prefix}{MANIFEST_PATH}"]
    manifest = json.loads(manifest_bytes)
    if manifest.get("source_commit") != commit:
        raise ValueError("Archive manifest has the wrong source commit")
    listed = {item["path"]: item for item in manifest.get("files", [])}
    if set(listed) != set(files):
        raise ValueError("Archive manifest file set differs from the locked distribution inputs")
    for name, source in files.items():
        archive_bytes = entries[f"{prefix}{name}"]
        if archive_bytes != source or sha256(archive_bytes) != listed[name]["sha256"]:
            raise ValueError(f"Archive readback mismatch for {name}")
    return sha256(manifest_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", default="HEAD", help="Git commit/ref whose tracked bytes are packaged")
    parser.add_argument("--output", required=True, type=Path, help="New output archive path; existing files are never overwritten")
    args = parser.parse_args()

    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode("utf-8").strip())
    commit = git(root, "rev-parse", f"{args.source_ref}^{{commit}}").decode("ascii").strip()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    files = source_files(root, commit)
    manifest = make_manifest(files, commit)
    try:
        write_zip(output, files, manifest)
        manifest_hash = verify_zip(output, files, commit)
    except Exception:
        output.unlink(missing_ok=True)
        raise

    print(json.dumps({
        "result": "PASS",
        "source_commit": commit,
        "archive": str(output),
        "archive_sha256": sha256(output.read_bytes()),
        "manifest_sha256": manifest_hash,
        "entries": len(files) + 1,
        "plugin_files": sum(path.startswith("plugin/") for path in files),
        "readback": "every archive payload byte-compared to git object and SHA-256 checked",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
