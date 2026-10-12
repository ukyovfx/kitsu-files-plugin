import json
import os
import stat
import subprocess

import pytest

from deploy.uat.tools import seed_linux


def test_seed_harness_writes_synthetic_accounts_without_persisting_jwt(tmp_path):
    env_file = tmp_path / "uat.env"
    env_file.write_text("VFX_FILES_UAT_TEST_PASSWORD=synthetic-only\n", encoding="utf-8")
    result = {
        "production_name": "Synthetic E2E",
        "production_id": "prod-uuid",
        "task_id": "task-uuid",
        "login_emails": {
            "manager": "manager@example.invalid",
            "artist_a": "artist-a@example.invalid",
            "artist_b": "artist-b@example.invalid",
            "supervisor": "supervisor@example.invalid",
        },
        "access_token": "jwt-must-never-be-written",
    }
    target = seed_linux.write_accounts(env_file, result)
    saved = target.read_text(encoding="utf-8")
    assert "Synthetic account password: synthetic-only" in saved
    assert "example.invalid" in saved
    assert "jwt-must-never-be-written" not in saved
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_seed_harness_rejects_non_synthetic_accounts(tmp_path):
    env_file = tmp_path / "uat.env"
    env_file.write_text("VFX_FILES_UAT_TEST_PASSWORD=synthetic-only\n", encoding="utf-8")
    result = {
        "production_name": "Synthetic E2E",
        "production_id": "prod-uuid",
        "task_id": "task-uuid",
        "login_emails": {
            "manager": "person@example.com",
            "artist_a": "artist-a@example.invalid",
            "artist_b": "artist-b@example.invalid",
            "supervisor": "supervisor@example.invalid",
        },
    }
    with pytest.raises(ValueError, match="synthetic-only"):
        seed_linux.write_accounts(env_file, result)
    assert not (tmp_path / "ACCOUNTS.txt").exists()


def test_seed_harness_captures_token_response_in_memory_and_suppresses_stderr(tmp_path, monkeypatch):
    env_file = tmp_path / "uat.env"
    compose = tmp_path / "compose.yaml"
    env_file.write_text("VFX_FILES_UAT_TEST_PASSWORD=synthetic-only\n", encoding="utf-8")
    result = {
        "production_name": "Synthetic E2E",
        "production_id": "prod-uuid",
        "task_id": "task-uuid",
        "login_emails": {
            "manager": "manager@example.invalid",
            "artist_a": "artist-a@example.invalid",
            "artist_b": "artist-b@example.invalid",
            "supervisor": "supervisor@example.invalid",
        },
        "access_token": "sensitive-test-token",
    }
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps(result).encode())

    monkeypatch.setattr(seed_linux.subprocess, "run", fake_run)
    target = seed_linux.run("vfx-files-uat-demo", env_file, compose)
    assert target.exists()
    assert seen["stderr"] is subprocess.DEVNULL
    assert "sensitive-test-token" not in str(seen["command"])
    assert "sensitive-test-token" not in target.read_text(encoding="utf-8")
