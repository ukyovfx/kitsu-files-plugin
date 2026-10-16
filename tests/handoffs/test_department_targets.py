from uuid import uuid4

from plugin.handoffs.targets import department_recipient_is_current_member, department_target_matches, handoff_is_visible_to_actor


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
        project_member=True, department_member=True, task_assignee=True, project_role="user",
    )
    assert not department_recipient_is_current_member(
        actor_id=other, designated_person_id=actor, active=True,
        project_member=True, department_member=True, task_assignee=True, project_role="user",
    )
    assert not department_recipient_is_current_member(
        actor_id=actor, designated_person_id=None, active=True,
        project_member=True, department_member=False, task_assignee=True, project_role="user",
    )


def test_department_recipient_must_have_a_role_allowed_to_respond():
    actor = uuid4()
    memberships = {
        "actor_id": actor,
        "designated_person_id": None,
        "active": True,
        "project_member": True,
        "department_member": True,
        "task_assignee": True,
    }
    assert department_recipient_is_current_member(**memberships, project_role="user")
    assert department_recipient_is_current_member(**memberships, project_role="supervisor")
    assert not department_recipient_is_current_member(**memberships, project_role="client")
    assert not department_recipient_is_current_member(**memberships, project_role="vendor")
    assert not department_recipient_is_current_member(**memberships, project_role=None)


def test_department_inbox_visibility_requires_current_recipient_eligibility():
    actor, sender, task = uuid4(), uuid4(), uuid4()
    common = {
        "actor_id": actor, "sender_id": sender, "recipient_id": None,
        "recipient_task_id": task, "source_task_visible": False,
        "recipient_task_visible": True,
    }
    assert not handoff_is_visible_to_actor(**common, recipient_is_currently_eligible=False)
    assert handoff_is_visible_to_actor(**common, recipient_is_currently_eligible=True)
    assert not handoff_is_visible_to_actor(**{**common, "recipient_task_visible": False}, recipient_is_currently_eligible=True)


def test_person_handoff_visibility_is_limited_to_named_recipient():
    actor, sender, other = uuid4(), uuid4(), uuid4()
    common = {
        "actor_id": actor, "sender_id": sender, "recipient_task_id": None,
        "source_task_visible": False, "recipient_task_visible": True,
        "recipient_is_currently_eligible": False,
    }
    assert handoff_is_visible_to_actor(**common, recipient_id=actor)
    assert not handoff_is_visible_to_actor(**common, recipient_id=other)
