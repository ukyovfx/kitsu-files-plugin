#!/usr/bin/env python3
"""Consistent snapshot/restore for one disposable UAT Compose unit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path


def run_compose(root: Path, env_file: Path, project: str, compose: Path, *args: str, data: bytes | None = None) -> bytes:
    command = [
        "docker", "compose", "--project-name", project, "--env-file", str(env_file),
        "--file", str(compose), *args,
    ]
    return subprocess.run(command, cwd=root, check=True, input=data, stdout=subprocess.PIPE).stdout


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def image_id(project: str, env_file: Path, compose: Path, root: Path) -> str:
    output = run_compose(root, env_file, project, compose, "images", "-q", "api").decode().splitlines()
    if len(set(output)) != 1 or not output:
        raise RuntimeError("Could not identify one API/Worker image for this UAT snapshot")
    return output[0]


def wait_service_healthy(root: Path, env_file: Path, project: str, compose: Path, service: str, timeout_seconds: int = 180) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ids = run_compose(root, env_file, project, compose, "ps", "--quiet", service).decode().splitlines()
        if len(ids) == 1:
            status = subprocess.check_output(
                ["docker", "inspect", "--format", "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", ids[0]],
                text=True,
            ).strip()
            if status == "running|healthy":
                return
            if status.startswith("exited|") or status.startswith("dead|"):
                raise RuntimeError(f"UAT service stopped before readiness: {service}")
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for isolated UAT service health: {service}")


def verify_snapshot(source: Path, target_project: str, source_short: str) -> tuple[dict, str]:
    manifest_path = source / "snapshot.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("Snapshot manifest must be a regular file")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    expected = hashlib.sha256(manifest_bytes).hexdigest()
    checksum = source / "snapshot.sha256"
    if checksum.is_symlink() or not checksum.is_file():
        raise RuntimeError("Snapshot manifest checksum must be a regular file")
    checksum_line = checksum.read_text(encoding="ascii").split()
    if len(checksum_line) != 2 or checksum_line[0] != expected or checksum_line[1] != "snapshot.json":
        raise RuntimeError("Snapshot manifest checksum mismatch")
    required = {"database.dump", "storage.tar"}
    contents = manifest.get("contents")
    if manifest.get("schema") != "vfx-files-uat-snapshot/v1" or not isinstance(contents, dict) or set(contents) != required:
        raise RuntimeError("Snapshot has an unsupported structure")
    if manifest.get("unit_project") == target_project:
        raise RuntimeError("Restore requires a different target UAT unit")
    source_head = manifest.get("source_head", "")
    if not re_full_sha(source_head) or not source_head.startswith(source_short):
        raise RuntimeError("Target package source does not match snapshot source")
    for name, item in contents.items():
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Snapshot payload must be a regular file: {name}")
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise RuntimeError(f"Snapshot payload checksum mismatch: {name}")
    return manifest, expected


def re_full_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def create_snapshot(args: argparse.Namespace) -> None:
    target = args.output.resolve()
    if target.exists():
        raise RuntimeError("Refusing to overwrite an existing snapshot directory")
    target.mkdir(parents=True)
    project = args.project
    run_compose(args.root, args.env_file, project, args.compose, "stop", "worker", "api")
    db = run_compose(
        args.root, args.env_file, project, args.compose,
        "exec", "-T", "postgres", "pg_dump", "-U", "vfxfiles_uat", "-d", "vfxfiles_uat", "-Fc", "--no-owner",
    )
    state = run_compose(
        args.root, args.env_file, project, args.compose,
        "run", "--rm", "-T", "--no-deps", "--entrypoint", "/bin/tar", "api",
        "-cpf", "-", "-C", "/var/lib/vfx-files", "staging", "fake",
    )
    (target / "database.dump").write_bytes(db)
    (target / "storage.tar").write_bytes(state)
    source = subprocess.check_output(["git", "-C", str(args.root), "rev-parse", "HEAD"], text=True).strip()
    manifest = {
        "schema": "vfx-files-uat-snapshot/v1",
        "unit_project": project,
        "source_head": source,
        "application_image_id": image_id(project, args.env_file, args.compose, args.root),
        "contents": {
            name: {"bytes": (target / name).stat().st_size, "sha256": digest(target / name)}
            for name in ("database.dump", "storage.tar")
        },
        "secrets_included": False,
        "restore_policy": "new disposable unit only; Worker remains gated until state, package and artifact verification is complete",
    }
    payload = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (target / "snapshot.json").write_bytes(payload)
    manifest_hash = hashlib.sha256(payload).hexdigest()
    (target / "snapshot.sha256").write_text(f"{manifest_hash}  snapshot.json\n", encoding="ascii")
    for name, item in manifest["contents"].items():
        if digest(target / name) != item["sha256"]:
            raise RuntimeError(f"Snapshot readback failed for {name}")
    print(json.dumps({"result": "PASS", "source_head": source, "manifest_sha256": manifest_hash,
                      "database_sha256": manifest["contents"]["database.dump"]["sha256"],
                      "storage_sha256": manifest["contents"]["storage.tar"]["sha256"],
                      "worker_state": "stopped", "secrets_included": False}, sort_keys=True))


def restore_snapshot(args: argparse.Namespace) -> None:
    source = args.snapshot.resolve()
    manifest, expected = verify_snapshot(source, args.project, args.source_short)
    running = run_compose(args.root, args.env_file, args.project, args.compose, "ps", "--quiet", "api", "worker").decode().splitlines()
    if any(running):
        raise RuntimeError("Refusing restore while the target API or Worker is running")
    run_compose(args.root, args.env_file, args.project, args.compose,
                "up", "-d", "postgres", "redis", "egress-guard")
    for service in ("postgres", "redis", "egress-guard"):
        wait_service_healthy(args.root, args.env_file, args.project, args.compose, service)
    table_count = run_compose(args.root, args.env_file, args.project, args.compose,
                              "exec", "-T", "postgres", "psql", "-U", "vfxfiles_uat", "-d", "vfxfiles_uat", "-Atc",
                              "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')").decode().strip()
    if table_count != "0":
        raise RuntimeError("Refusing restore into a database that already has application tables")
    occupied = run_compose(args.root, args.env_file, args.project, args.compose,
                           "run", "--rm", "-T", "--no-deps", "--entrypoint", "/bin/sh", "api",
                           "-ec", "test -z \"$(find /var/lib/vfx-files/staging /var/lib/vfx-files/fake -mindepth 1 -print -quit)\"")
    run_compose(args.root, args.env_file, args.project, args.compose,
                "exec", "-T", "postgres", "pg_restore", "-U", "vfxfiles_uat", "-d", "vfxfiles_uat",
                "--clean", "--if-exists", "--no-owner", "--no-acl", data=(source / "database.dump").read_bytes())
    run_compose(args.root, args.env_file, args.project, args.compose,
                "run", "--rm", "-T", "--no-deps", "--entrypoint", "/bin/tar", "api",
                "-xpf", "-", "-C", "/var/lib/vfx-files", data=(source / "storage.tar").read_bytes())
    (args.output / "restore-manifest.sha256").write_text(expected + "\n", encoding="ascii")
    run_compose(args.root, args.env_file, args.project, args.compose, "up", "-d", "api")
    print(json.dumps({"result": "RESTORED_AWAITING_VALIDATION", "manifest_sha256": expected,
                      "api_started": True, "worker_started": False,
                      "next": "compare sanitized source/restore reports and verify object bytes before the explicit restore-gate procedure"}, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="mode", required=True)
    for mode in ("backup", "restore"):
        command = sub.add_parser(mode)
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--compose", type=Path, required=True)
        command.add_argument("--env-file", type=Path, required=True)
        command.add_argument("--project", required=True)
        command.add_argument("--output", type=Path, required=True)
        if mode == "restore":
            command.add_argument("--snapshot", type=Path, required=True)
            command.add_argument("--source-short", required=True)
        command.set_defaults(func=create_snapshot if mode == "backup" else restore_snapshot)
    return result


if __name__ == "__main__":
    args = parser().parse_args()
    if args.mode == "backup":
        args.output.parent.mkdir(parents=True, exist_ok=True)
    else:
        args.output.mkdir(parents=True, exist_ok=True)
    args.func(args)
