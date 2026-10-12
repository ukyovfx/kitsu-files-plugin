#!/usr/bin/env python3
"""Build a deterministic Zou-installable plugin ZIP from one exact Git commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath


PLUGIN_ID = "vfx_files"
TARGET_HOST = {"kitsu": "1.0.70", "zou": "1.0.94"}
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
MIGRATION_RE = re.compile(r"(?m)^revision\s*=\s*['\"]([^'\"]+)['\"]\s*$")
DOWN_RE = re.compile(r"(?m)^down_revision\s*=\s*['\"]([^'\"]+)['\"]\s*$")
NULL_DOWN_RE = re.compile(r"(?m)^down_revision\s*=\s*None\s*$")


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exact_commit(root: Path, ref: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("source must be an exact 40-character commit SHA")
    commit = git(root, "rev-parse", f"{ref}^{{commit}}").decode("ascii").strip()
    if commit != ref:
        raise ValueError("source SHA did not resolve to itself")
    return commit


def committed_plugin_files(root: Path, commit: str) -> dict[str, bytes]:
    records = git(root, "ls-tree", "-r", "-z", commit, "--", "plugin/").split(b"\0")
    result: dict[str, bytes] = {}
    for record in records:
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, _object_id = metadata.decode("ascii").split(" ", 2)
        path = raw_path.decode("utf-8")
        if mode != "100644" or object_type != "blob":
            raise ValueError(f"plugin source contains a non-regular tracked object: {path}")
        relative = path.removeprefix("plugin/")
        pure = PurePosixPath(relative)
        if not relative or pure.is_absolute() or ".." in pure.parts or "\\" in relative:
            raise ValueError(f"unsafe plugin archive path: {path}")
        result[relative] = git(root, "show", f"{commit}:{path}")
    if "manifest.toml" not in result or "__init__.py" not in result:
        raise ValueError("Git commit does not contain a complete Zou plugin package")
    return dict(sorted(result.items()))


def migration_order(files: dict[str, bytes]) -> list[str]:
    migrations = {
        path.rsplit("/", 1)[-1]: data.decode("utf-8")
        for path, data in files.items()
        if path.startswith("migrations/versions/") and path.endswith(".py")
    }
    graph: dict[str, str | None] = {}
    for filename, source in migrations.items():
        revision = MIGRATION_RE.search(source)
        if not revision:
            raise ValueError(f"migration has no literal revision: {filename}")
        down = DOWN_RE.search(source)
        graph[revision.group(1)] = down.group(1) if down else (None if NULL_DOWN_RE.search(source) else "<dynamic>")
    if not graph:
        raise ValueError("plugin migration scripts are missing")
    ordered: list[str] = []
    pending = dict(graph)
    while pending:
        ready = sorted(revision for revision, parent in pending.items() if parent is None or parent in ordered)
        if not ready:
            raise ValueError("plugin migration graph is branched, cyclic, or unresolved")
        ordered.extend(ready)
        for revision in ready:
            pending.pop(revision)
    if len(ordered) != len(graph):
        raise ValueError("not every plugin migration is in the linear chain")
    return ordered


def manifest_text(files: dict[str, bytes], key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}\s*=\s*\"([^\"]+)\"\s*$", files["manifest.toml"].decode("utf-8"))
    if not match:
        raise ValueError(f"plugin manifest is missing {key}")
    return match.group(1)


def package_manifest(files: dict[str, bytes], commit: str, archive_sha256: str) -> bytes:
    plugin_id = manifest_text(files, "id")
    version = manifest_text(files, "version")
    if plugin_id != PLUGIN_ID:
        raise ValueError(f"unexpected plugin id: {plugin_id}")
    routes = files.get("__init__.py", b"")
    if b"routes" not in routes:
        raise ValueError("plugin route registration is missing")
    doc = {
        "schema": "vfx-files-zou-plugin-package/v1",
        "repository": "ukyovfx/vfx-kitsu-files-plugin",
        "source_commit": commit,
        "plugin": {"id": plugin_id, "version": version},
        "target_compatibility": TARGET_HOST,
        "distribution_status": "unsigned; local verification only; no server installation or deployment approval",
        "archive_layout": "ZIP root is the plugin root expected by zou install-plugin --path <zip>",
        "archive_sha256": archive_sha256,
        "plugin_file_count": len(files),
        "files": [{"path": name, "size": len(data), "sha256": sha256(data)} for name, data in files.items()],
        "migrations_in_order": migration_order(files),
        "worker": {
            "entrypoint": "python -m vfx_files.worker_runtime",
            "same_source_tree_as_api_plugin": True,
            "installation_path_example": "/opt/zou/plugins/vfx_files",
            "python_dependencies": "provided by the host Zou environment; package does not vendor or install dependencies",
        },
        "host_security": "official locked Kitsu/Zou dependency vulnerability gate FAIL; not cleared by this package",
    }
    return (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_archive(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)


def verify_archive(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"ZIP CRC verification failed: {bad}")
        if set(archive.namelist()) != set(files):
            raise ValueError("ZIP contains unexpected paths or is missing plugin files")
        for name, expected in files.items():
            actual = archive.read(name)
            if actual != expected or sha256(actual) != sha256(expected):
                raise ValueError(f"ZIP readback differs from Git source: {name}")


def build(root: Path, commit: str, output: Path) -> tuple[Path, Path, dict]:
    commit = exact_commit(root, commit)
    files = committed_plugin_files(root, commit)
    output = output.expanduser().resolve()
    sidecar = output.with_suffix(output.suffix + ".manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    created_output = False
    created_sidecar = False
    try:
        with output.open("xb"):
            created_output = True
        with sidecar.open("xb"):
            created_sidecar = True
        write_archive(output, files)
        verify_archive(output, files)
        manifest = package_manifest(files, commit, sha256(output.read_bytes()))
        sidecar.write_bytes(manifest)
    except Exception:
        if created_output:
            output.unlink(missing_ok=True)
        if created_sidecar:
            sidecar.unlink(missing_ok=True)
        raise
    return output, sidecar, json.loads(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True, help="exact 40-character Git commit SHA")
    parser.add_argument("--output", required=True, type=Path, help="new plugin ZIP path")
    args = parser.parse_args()
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    archive, sidecar, manifest = build(root, args.source_commit, args.output)
    print(json.dumps({
        "result": "PASS",
        "source_commit": manifest["source_commit"],
        "plugin": manifest["plugin"],
        "archive": str(archive),
        "archive_sha256": sha256(archive.read_bytes()),
        "manifest": str(sidecar),
        "manifest_sha256": sha256(sidecar.read_bytes()),
        "plugin_files": manifest["plugin_file_count"],
        "readback": "ZIP entries compared byte-for-byte with exact tracked Git commit",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
