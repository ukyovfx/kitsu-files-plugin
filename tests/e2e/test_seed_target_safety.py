import base64
import json
from datetime import timedelta

import pytest

from seeder_safety import (
    create_short_lived_person_token,
    new_synthetic_manager,
    token_expiry_config,
    token_has_short_expiry,
    validated_disposable_root_url,
)


def test_seed_refuses_to_run_without_explicit_disposable_mode():
    validate = validated_disposable_root_url

    with pytest.raises(RuntimeError, match="disposable test mode"):
        validate({"VFX_FILES_E2E_ROOT_URL": "https://kitsu.test"})


def test_seed_requires_a_root_url():
    validate = validated_disposable_root_url

    with pytest.raises(RuntimeError, match="root URL"):
        validate({"VFX_FILES_DISPOSABLE_TEST_MODE": "1"})


def test_reserved_test_host_is_accepted_only_with_https_or_loopback_http():
    validate = validated_disposable_root_url
    env = {
        "VFX_FILES_DISPOSABLE_TEST_MODE": "1",
        "VFX_FILES_E2E_ROOT_URL": "https://kitsu.test/",
    }

    assert validate(env) == "https://kitsu.test"
    env["VFX_FILES_E2E_ROOT_URL"] = "http://127.0.0.1:8080/"
    assert validate(env) == "http://127.0.0.1:8080"


def test_non_reserved_host_requires_explicit_disposable_host_acknowledgement():
    validate = validated_disposable_root_url
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
    validate = validated_disposable_root_url
    env = {
        "VFX_FILES_DISPOSABLE_TEST_MODE": "1",
        "VFX_FILES_E2E_ROOT_URL": url,
    }

    with pytest.raises(RuntimeError):
        validate(env)


def test_seed_emits_only_short_lived_tokens():
    check_expiry = token_has_short_expiry

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
    create_manager = new_synthetic_manager

    class PersonFactory:
        def __init__(self, **fields):
            self.fields = fields

    manager = create_manager(PersonFactory, "test123456")

    assert manager.fields["first_name"] == "Synthetic"
    assert manager.fields["email"] == "manager-test123456@example.invalid"
    assert manager.fields["role"] == "manager"


def test_seed_tokens_use_a_fifteen_minute_expiry():
    assert token_expiry_config() == timedelta(minutes=15)


def test_seed_uses_explicit_short_lived_jwt_and_persists_jti():
    class Person:
        id = "synthetic-person-id"
        is_bot = False

        def save(self):
            self.saved = True

    person = Person()
    issued = {}

    def create_token(**kwargs):
        issued.update(kwargs)
        return "synthetic.jwt.value"

    token = create_short_lived_person_token(
        person, create_token, lambda value: "synthetic-jti"
    )

    assert token == "synthetic.jwt.value"
    assert issued["identity"] == person.id
    assert issued["additional_claims"] == {"identity_type": "person_api"}
    assert issued["expires_delta"] == timedelta(minutes=15)
    assert person.jti == "synthetic-jti"
    assert person.saved
