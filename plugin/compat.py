"""Adapters for the exact Kitsu/Zou pair recorded in environment/host-lock.yaml."""

HOST_KITSU_VERSION = "1.0.70"
HOST_ZOU_VERSION = "1.0.94"
SCHEMA_EPOCH = 5
WORKER_BUILD = "5"


def assert_runtime_host() -> None:
    from zou import __version__ as zou_version

    if zou_version != HOST_ZOU_VERSION:
        raise RuntimeError(
            "VFX Files supports only the pinned Zou 1.0.94 host until a compatibility rehearsal passes"
        )


def current_actor_role(project_id):
    """Resolve Zou's effective project role only after project membership check."""
    from zou.app.services import permissions_service

    permissions_service.check_project_access(str(project_id))
    from zou.app.utils import permissions

    return permissions.get_effective_role()


def authorize_task_mutation(actor, task_id, capability):
    """Use Zou's authoritative assignment and project-role helpers for writes."""
    from zou.app.models.task import Task
    from zou.app.services import permissions_service

    task = Task.query.filter_by(id=task_id).one_or_none()
    if task is None:
        return False
    role = actor.project_role
    if role in ("admin", "manager"):
        return permissions_service.has_manager_project_access(str(task.project_id))
    if role == "supervisor" and capability in ("upload", "publish", "handoff"):
        return permissions_service.check_working_on_task(str(task.id))
    if role == "supervisor" and capability == "approve":
        return permissions_service.check_working_on_task(str(task.id))
    if role == "user" and capability in ("upload", "publish", "handoff"):
        return permissions_service.check_working_on_task(str(task.id))
    return False


def authorize_task_read(actor, task_id):
    """Require current Production authority and assignment for Task content."""
    from zou.app.models.task import Task
    from zou.app.services import permissions_service

    task = Task.query.filter_by(id=task_id).one_or_none()
    if task is None:
        return False
    role = actor.project_role
    if role in ("admin", "manager"):
        return permissions_service.has_manager_project_access(str(task.project_id))
    if role in ("supervisor", "user"):
        return permissions_service.check_working_on_task(str(task.id))
    return False
