import ast
import base64
import binascii
import json
import os
import time
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest


def _load_seed_function(name):
    source_path = Path(__file__).with_name("seed_installed_page_flow.py")
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    )
    isolated_module = ast.Module(body=[function], type_ignores=[])
    namespace = {
        "base64": base64,
        "binascii": binascii,
        "ip_address": ip_address,
        "json": json,
        "os": os,
        "time": time,
        "urlsplit": urlsplit,
        "urlunsplit": urlunsplit,
    }
    exec(compile(isolated_module, str(source_path), "exec"), namespace)
    return namespace[name]


def test_seed_refuses_to_run_without_explicit_disposable_mode():
    validate = _load_seed_function("_validated_disposable_root_url")

    with pytest.raises(RuntimeError, match="disposable test mode"):
        validate({"VFX_FILES_E2E_ROOT_URL": "https://kitsu.test"})


def test_seed_requires_a_root_url():
    validate = _load_seed_function("_validated_disposable_root_url")

    with pytest.raises(RuntimeError, match="root URL"):
        validate({"VFX_FILES_DISPOSABLE_TEST_MODE": "1"})


def test_reserved_test_host_is_accepted_only_with_https_or_loopback_http():
    validate = _load_seed_function("_validated_disposable_root_url")
    env = {
        "VFX_FILES_DISPOSABLE_TEST_MODE": "1",
        "VFX_FILES_E2E_ROOT_URL": "https://kitsu.test/",
    }

    assert validate(env) == "https://kitsu.test"
    env["VFX_FILES_E2E_ROOT_URL"] = "http://127.0.0.1:8080/"
    assert validate(env) == "http://127.0.0.1:8080"


def test_non_reserved_host_requires_explicit_disposable_host_acknowledgement():
    validate = _load_seed_function("_validated_disposable_root_url")
    env = {
        "VFX_FILES_DISPOSABLE_TEST_MODE": "1",
        "VFX_FILES_E2E_ROOT_URL": "https://203.0.113.7",
    }

    with pytest.raises(RuntimeError, match="disposable host acknowledgement"):
        validate(env)

    env["VFX_FILES_DISPOSABLE_HOST_ACK"] = "DISPOSABLE_PINNED_KITSU_ZOU_ONLY"
    assert validate(env) == "https://203.0.113.7"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@kitsu.test",
        "https://kitsu.test/path",
        "https://kitsu.test/?token=example",
        "https://kitsu.test/#fragment",
        "https://kitsu.test:99999",
        "http://kitsu.test",
    ],
)
def test_seed_rejects_ambiguous_or_unsafe_root_urls(url):
    validate = _load_seed_function("_validated_disposable_root_url")
    env = {
        "VFX_FILES_DISPOSABLE_TEST_MODE": "1",
        "VFX_FILES_E2E_ROOT_URL": url,
    }

    with pytest.raises(RuntimeError):
        validate(env)


def test_seed_emits_only_short_lived_tokens():
    check_expiry = _load_seed_function("_token_has_short_expiry")

    def token_with_expiry(expiry):
        payload = base64.urlsafe_b64encode(
            json.dumps({"exp": expiry}).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return f"e30.{payload}.synthetic-test-signature"

    assert check_expiry(token_with_expiry(1_800), now=1_000)
    assert not check_expiry(token_with_expiry(999), now=1_000)
    assert not check_expiry(token_with_expiry(1_902), now=1_000)
    assert not check_expiry("not-a-jwt", now=1_000)


def test_seed_manager_is_created_with_a_synthetic_identity():
    create_manager = _load_seed_function("_new_synthetic_manager")

    class PersonFactory:
        def __init__(self, **fields):
            self.fields = fields

    manager = create_manager(PersonFactory, "test123456")

    assert manager.fields["first_name"] == "Synthetic"
    assert manager.fields["email"] == "manager-test123456@example.invalid"
    assert manager.fields["role"] == "manager"
