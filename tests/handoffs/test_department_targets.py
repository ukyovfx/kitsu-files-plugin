from uuid import uuid4

from plugin.handoffs.targets import department_recipient_is_current_member, department_target_matches


def test_department_target_requires_same_production_entity_and_exact_task_type_department():
    production, entity, department = uuid4(), uuid4(), uuid4()
    assert department_target_matches(
        source_project_id=production, source_entity_id=entity,
        target_project_id=production, target_entity_id=entity,
        target_department_id=department, requested_department_id=department,
        department_exists=True, task_type_in_project=True,
    )
    assert not department_target_matches(
        source_project_id=production, source_entity_id=entity,
        target_project_id=uuid4(), target_entity_id=entity,
        target_department_id=department, requested_department_id=department,
        department_exists=True, task_type_in_project=True,
    )
    assert not department_target_matches(
        source_project_id=production, source_entity_id=entity,
        target_project_id=production, target_entity_id=uuid4(),
        target_department_id=department, requested_department_id=department,
        department_exists=True, task_type_in_project=True,
    )
    assert not department_target_matches(
        source_project_id=production, source_entity_id=entity,
        target_project_id=production, target_entity_id=entity,
        target_department_id=uuid4(), requested_department_id=department,
        department_exists=True, task_type_in_project=True,
    )
    assert not department_target_matches(
        source_project_id=production, source_entity_id=entity,
        target_project_id=production, target_entity_id=entity,
        target_department_id=department, requested_department_id=department,
        department_exists=True, task_type_in_project=False,
    )


def test_department_accept_checks_current_membership_and_optional_designated_recipient():
    actor, other = uuid4(), uuid4()
    assert department_recipient_is_current_member(
        actor_id=actor, designated_person_id=actor, active=True,
        project_member=True, department_member=True, task_assignee=True,
    )
    assert not department_recipient_is_current_member(
        actor_id=other, designated_person_id=actor, active=True,
        project_member=True, department_member=True, task_assignee=True,
    )
    assert not department_recipient_is_current_member(
        actor_id=actor, designated_person_id=None, active=True,
        project_member=True, department_member=False, task_assignee=True,
    )
