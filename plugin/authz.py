"""Fail-closed authorization primitives for the Kitsu Files plugin.

The request's iframe query parameters never construct an Actor or grant access.
The host adapter must supply the actor role and authoritative Zou lookups.
"""

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable
from uuid import UUID

from .compat import HOST_ZOU_VERSION, SCHEMA_EPOCH, WORKER_BUILD


class NotFound(Exception):
    """Used for malformed or out-of-scope identifiers to avoid enumeration."""


class PermissionDenied(Exception):
    pass


class Capability(str, Enum):
    READ = "read"
    UPLOAD = "upload"
    PUBLISH = "publish"
    APPROVE = "approve"
    PROVISION = "provision"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class Actor:
    actor_id: UUID
    project_role: str
    active: bool = True


@dataclass(frozen=True)
class ResolvedScope:
    production_id: UUID
    task_id: UUID | None
    task_project_id: UUID | None


_MANAGERS = {"admin", "manager"}
_SUPERVISORS = _MANAGERS | {"supervisor"}


def resolve_request_scope(
    actor: Actor,
    production_id: str,
    task_id: str | None,
    get_production: Callable[[UUID], object | None],
    get_task: Callable[[UUID], object | None],
) -> ResolvedScope:
    """Resolve project/task through host lookups and enforce their relationship."""
    if not actor.active:
        raise PermissionDenied("Inactive actor")
    try:
        production_uuid = UUID(production_id)
        task_uuid = UUID(task_id) if task_id is not None else None
    except (ValueError, TypeError, AttributeError):
        raise NotFound from None

    production = get_production(production_uuid)
    if production is None:
        raise NotFound

    if task_uuid is None:
        return ResolvedScope(production_uuid, None, None)

    task = get_task(task_uuid)
    if task is None:
        raise NotFound
    task_project_id = getattr(task, "project_id", None)
    if str(task_project_id) != str(production_uuid):
        raise NotFound
    return ResolvedScope(production_uuid, task_uuid, production_uuid)


def authorize(actor: Actor, capability: Capability, scope: ResolvedScope) -> None:
    """Apply the approved role matrix; unknown roles and incomplete scopes deny."""
    if not actor.active:
        raise PermissionDenied("Inactive actor")
    role = actor.project_role
    if capability == Capability.PROVISION:
        allowed = role in _MANAGERS
    elif capability == Capability.APPROVE:
        allowed = role in _SUPERVISORS
    else:
        allowed = role in _MANAGERS | {"supervisor", "user"}
    if not allowed:
        raise PermissionDenied("Role has no Files capability")
    if capability in (Capability.UPLOAD, Capability.PUBLISH, Capability.HANDOFF) and scope.task_id is None:
        raise PermissionDenied("A task-scoped capability requires a task")
    if scope.task_id is not None and scope.task_project_id != scope.production_id:
        raise PermissionDenied("Task is outside the requested Production")


def assert_same_origin_mutation(origin: str | None, request_origin: str) -> None:
    """Require an explicit same-origin Origin header on browser mutations."""
    if origin is None or origin.rstrip("/") != request_origin.rstrip("/"):
        raise PermissionDenied("Cross-origin mutation rejected")


def issue_csrf_token(secret_key: str, actor_id: UUID) -> str:
    """Issue a short-lived CSRF token bound to the authenticated host identity."""
    if not secret_key:
        raise RuntimeError("Kitsu secret key is unavailable")
    payload = f"{actor_id}:{int(time.time())}:{secrets.token_urlsafe(12)}".encode("ascii")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signing_key = hmac.new(secret_key.encode("utf-8"), b"vfx-files-csrf-v1", hashlib.sha256).digest()
    signature = hmac.new(signing_key, encoded, hashlib.sha256).digest()
    return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def verify_csrf_token(secret_key: str, actor_id: UUID, token: str | None, max_age: int = 900) -> None:
    if not secret_key or not token:
        raise PermissionDenied("CSRF token required")
    try:
        encoded, signature = token.encode("ascii").split(b".", 1)
        signing_key = hmac.new(secret_key.encode("utf-8"), b"vfx-files-csrf-v1", hashlib.sha256).digest()
        expected = hmac.new(signing_key, encoded, hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(signature + b"=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("invalid signature")
        payload = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4)).decode("ascii")
        value, issued_at, nonce = payload.split(":", 2)
        age = int(time.time()) - int(issued_at)
        if not nonce or age < 0 or age > max_age:
            raise ValueError("invalid timestamp")
    except (ValueError, UnicodeError):
        raise PermissionDenied("Invalid or expired CSRF token") from None
    if value != str(actor_id):
        raise PermissionDenied("CSRF token identity mismatch")


def assert_bearer_header(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise PermissionDenied("Bearer header required")
    token = authorization[7:].strip()
    if not token or " " in token:
        raise PermissionDenied("Malformed bearer header")
    return token


def assert_compatible_host(host_version: str, schema_epoch: int, worker_build: str) -> None:
    if host_version != HOST_ZOU_VERSION or schema_epoch != SCHEMA_EPOCH or worker_build != WORKER_BUILD:
        raise RuntimeError("Files plugin host/schema/worker compatibility mismatch")
