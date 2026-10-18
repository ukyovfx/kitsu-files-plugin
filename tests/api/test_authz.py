from dataclasses import dataclass
from uuid import UUID

import pytest

from plugin.authz import (
    Actor,
    Capability,
    NotFound,
    PermissionDenied,
    ResolvedScope,
    assert_compatible_host,
    assert_same_origin_mutation,
    authorize,
    issue_csrf_token,
    resolve_request_scope,
    verify_csrf_token,
)
from plugin.compat import SCHEMA_EPOCH, WORKER_BUILD

PRODUCTION = UUID("00000000-0000-4000-8000-000000000001")
TASK = UUID("00000000-0000-4000-8000-000000000002")


@dataclass
class HostTask:
    project_id: UUID


def test_iframe_context_is_not_authority():
    actor = Actor(UUID("00000000-0000-4000-8000-000000000009"), "user")
    resolved = resolve_request_scope(
        actor,
        str(PRODUCTION),
        str(TASK),
        lambda value: object() if value == PRODUCTION else None,
        lambda value: HostTask(PRODUCTION) if value == TASK else None,
    )
    authorize(actor, Capability.READ, resolved)

    with pytest.raises(NotFound):
        resolve_request_scope(
            actor,
            "spoofed-production",
            str(TASK),
            lambda _value: object(),
            lambda _value: HostTask(PRODUCTION),
        )


def test_task_must_belong_to_requested_production():
    actor = Actor(UUID("00000000-0000-4000-8000-000000000009"), "user")
    with pytest.raises(NotFound):
        resolve_request_scope(
            actor,
            str(PRODUCTION),
            str(TASK),
            lambda _value: object(),
            lambda _value: HostTask(UUID("00000000-0000-4000-8000-000000000003")),
        )


def test_unknown_role_denied():
    with pytest.raises(PermissionDenied):
        authorize(Actor(UUID(int=1), "unknown"), Capability.READ, ResolvedScope(PRODUCTION, None, None))


def test_cookie_mutation_requires_csrf_token():
    with pytest.raises(PermissionDenied):
        assert_same_origin_mutation(None, "https://kitsu.example")
    with pytest.raises(PermissionDenied):
        verify_csrf_token("test-secret", UUID(int=1), None)


def test_csrf_token_is_identity_bound():
    actor_id = UUID(int=1)
    token = issue_csrf_token("test-secret", actor_id)
    verify_csrf_token("test-secret", actor_id, token)
    with pytest.raises(PermissionDenied):
        verify_csrf_token("test-secret", UUID(int=2), token)


def test_csrf_token_rejects_other_signing_secret():
    token = issue_csrf_token("test-secret", UUID(int=1))
    with pytest.raises(PermissionDenied):
        verify_csrf_token("other-secret", UUID(int=1), token)


def test_cross_origin_mutation_rejected():
    with pytest.raises(PermissionDenied):
        assert_same_origin_mutation("https://attacker.example", "https://kitsu.example")


def test_worker_rejects_schema_epoch_mismatch():
    with pytest.raises(RuntimeError):
        assert_compatible_host("1.0.94", 3, "2")


def test_worker_accepts_only_rehearsed_zou_versions_and_current_schema_build():
    assert_compatible_host("1.0.94", SCHEMA_EPOCH, WORKER_BUILD)
    assert_compatible_host("1.0.96", SCHEMA_EPOCH, WORKER_BUILD)
    with pytest.raises(RuntimeError):
        assert_compatible_host("1.0.95", SCHEMA_EPOCH, WORKER_BUILD)
    with pytest.raises(RuntimeError):
        assert_compatible_host("1.0.97", SCHEMA_EPOCH, WORKER_BUILD)
    with pytest.raises(RuntimeError):
        assert_compatible_host("1.0.96", SCHEMA_EPOCH - 1, WORKER_BUILD)
    with pytest.raises(RuntimeError):
        assert_compatible_host("1.0.96", SCHEMA_EPOCH, str(int(WORKER_BUILD) - 1))
