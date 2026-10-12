#!/usr/bin/env python3
"""Run the disposable-page seeder without writing or printing its JWT output."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def read_env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name == key:
            return value
    raise ValueError("required synthetic test setting is missing")


def safe_line(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("seeder returned an invalid field")
    text = str(value)
    if not text.strip() or "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError("seeder returned an invalid field")
    return text


def write_accounts(env_file: Path, result: dict) -> Path:
    emails = result.get("login_emails")
    if not isinstance(emails, dict):
        raise ValueError("seeder response is incomplete")
    account_names = ("manager", "artist_a", "artist_b", "supervisor")
    lines = [
        f"Production: {safe_line(result.get('production_name'))}",
        f"Production ID: {safe_line(result.get('production_id'))}",
        f"Task ID: {safe_line(result.get('task_id'))}",
    ]
    for name in account_names:
        email = safe_line(emails.get(name))
        if email.rpartition("@")[2] != "example.invalid":
            raise ValueError("seeder did not return synthetic-only account identifiers")
        lines.append(f"{name.replace('_', ' ').title()}: {email}")
    lines.extend(
        [
            f"Synthetic account password: {read_env_value(env_file, 'VFX_FILES_UAT_TEST_PASSWORD')}",
            "Synthetic UAT records only.",
        ]
    )
    target = env_file.parent / "ACCOUNTS.txt"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def run(project: str, env_file: Path, compose: Path) -> Path:
    if not re.fullmatch(r"vfx-files-uat-[a-z0-9][a-z0-9-]{0,23}", project):
        raise ValueError("invalid isolated UAT project name")
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(env_file),
            "--file",
            str(compose),
            "exec",
            "--no-TTY",
            "api",
            "/opt/zou/env/bin/python",
            "/opt/vfx-files-uat/seed_installed_page_flow.py",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise RuntimeError("synthetic seeder failed")
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise ValueError("seeder response is invalid")
    return write_accounts(env_file, result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = run(args.project, args.env_file, args.compose)
    except Exception:
        print("Synthetic seeder did not complete; sensitive output was suppressed.", file=sys.stderr)
        return 1
    print(f"Synthetic UAT accounts saved with mode 0600 to {path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
