"""Adapters for the exact Kitsu/Zou pair recorded in environment/host-lock.yaml."""

HOST_KITSU_VERSION = "1.0.70"
HOST_ZOU_VERSION = "1.0.94"
SCHEMA_EPOCH = 11
WORKER_BUILD = "11"


def assert_runtime_host() -> None:
    from zou import __version__ as zou_version

    if zou_version != HOST_ZOU_VERSION:
        raise RuntimeError(
            "VFX Files supports only the pinned Zou 1.0.94 host until a compatibility rehearsal passes"
        )


def current_actor_role(project_id):
    """Resolve the authenticated Zou identity's current Production role by UUID."""
    from uuid import UUID

    from flask_jwt_extended import get_jwt_identity
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink

    from .authz import PermissionDenied

    try:
        actor_id = UUID(str(get_jwt_identity()))
        production_id = UUID(str(project_id))
    except (ValueError, TypeError, AttributeError):
        raise PermissionDenied("Invalid Kitsu identity or Production") from None
    person = Person.query.filter_by(id=actor_id).one_or_none()
    if person is None or not getattr(person, "active", True):
        raise PermissionDenied("Kitsu identity is unavailable")
    global_role = getattr(person.role, "code", person.role)
    if global_role == "admin":
        return "admin"
    membership = ProjectPersonLink.query.filter_by(project_id=production_id, person_id=actor_id).one_or_none()
    if membership is None:
        raise PermissionDenied("Kitsu identity has no Production membership")
    role = getattr(membership.role, "code", membership.role)
    if role not in {"manager", "supervisor", "user", "client", "vendor"}:
        raise PermissionDenied("Kitsu Production role is unsupported")
    return role


def authorize_task_mutation(actor, task_id, capability):
    """Use current Zou membership/assignment rows for each task mutation."""
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import Task, TaskPersonLink

    task = Task.query.filter_by(id=task_id).one_or_none()
    if task is None:
        return False
    role = actor.project_role
    if role == "admin":
        return True
    if role == "manager":
        return ProjectPersonLink.query.filter_by(project_id=task.project_id, person_id=actor.actor_id).one_or_none() is not None
    if role in ("supervisor", "user") and capability in ("upload", "publish", "handoff", "approve"):
        return TaskPersonLink.query.filter_by(task_id=task.id, person_id=actor.actor_id).one_or_none() is not None
    return False


def authorize_task_read(actor, task_id):
    """Require current Production membership or explicit Task assignment."""
    from zou.app.models.project import ProjectPersonLink
    from zou.app.models.task import Task, TaskPersonLink

    task = Task.query.filter_by(id=task_id).one_or_none()
    if task is None:
        return False
    role = actor.project_role
    if role == "admin":
        return True
    if role == "manager":
        return ProjectPersonLink.query.filter_by(project_id=task.project_id, person_id=actor.actor_id).one_or_none() is not None
    if role in ("supervisor", "user"):
        return TaskPersonLink.query.filter_by(task_id=task.id, person_id=actor.actor_id).one_or_none() is not None
    return False
