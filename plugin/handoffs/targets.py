"""Pure fail-closed predicates for Department/Task handoff boundaries."""


def department_target_matches(*, source_project_id, source_entity_id, target_project_id, target_entity_id, target_department_id, requested_department_id, department_exists: bool, task_type_in_project: bool) -> bool:
    return bool(
        department_exists
        and task_type_in_project
        and source_project_id == target_project_id
        and source_entity_id == target_entity_id
        and target_department_id is not None
        and target_department_id == requested_department_id
    )


def department_recipient_is_current_member(
    *, actor_id, designated_person_id, active: bool, project_member: bool,
    department_member: bool, task_assignee: bool, project_role: str | None,
) -> bool:
    return bool(
        active and project_member and department_member and task_assignee
        and project_role in {"admin", "manager", "supervisor", "user"}
        and (designated_person_id is None or designated_person_id == actor_id)
    )


def handoff_is_visible_to_actor(
    *, actor_id, sender_id, recipient_id, recipient_task_id,
    source_task_visible: bool, recipient_task_visible: bool,
    recipient_is_currently_eligible: bool,
) -> bool:
    """Only a source Task reader or the actual current recipient sees a Handoff."""
    if source_task_visible and sender_id == actor_id:
        return True
    if not recipient_task_visible:
        return False
    if recipient_task_id is None:
        return recipient_id == actor_id
    return recipient_is_currently_eligible
