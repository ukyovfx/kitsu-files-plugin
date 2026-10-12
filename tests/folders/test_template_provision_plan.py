import pytest

from plugin.folders.provisioning import build_folder_plan


def test_folder_plan_uses_kitsu_uuids_and_preserves_nested_template_names():
    tree = {
        "folder_id": "template-root",
        "name": "Shared Template",
        "children": [
            {
                "folder_id": "sequence-role",
                "name": "{sequence}",
                "children": [
                    {
                        "folder_id": "shot-role",
                        "name": "{shot}",
                        "children": [
                            {
                                "folder_id": "working-dir",
                                "name": "Working",
                                "children": [{"folder_id": "task-role", "name": "{task}", "children": []}],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    plan = build_folder_plan(
        tree=tree,
        role_mappings={"sequence": "sequence-role", "shot": "shot-role", "task": "task-role"},
        production_id="prod-uuid",
        shot_id="shot-uuid",
        shot_name="sh010",
        sequence_id="sequence-uuid",
        sequence_name="seq01",
        task_id="task-uuid",
        task_name="comp",
    )

    assert [(item.display_name, item.scope_key) for item in plan] == [
        ("seq01", "production:prod-uuid:sequence:sequence-uuid"),
        ("sh010", "production:prod-uuid:shot:shot-uuid"),
        ("Working", "production:prod-uuid:shot:shot-uuid:template:working-dir"),
        ("comp", "production:prod-uuid:task:task-uuid"),
    ]
    assert [item.template_folder_id for item in plan] == ["sequence-role", "shot-role", "working-dir", "task-role"]


def test_task_template_role_must_be_descendant_of_shot_role():
    tree = {
        "folder_id": "template-root", "name": "Template", "children": [
            {"folder_id": "shot-role", "name": "Shot", "children": []},
            {"folder_id": "task-role", "name": "Task", "children": []},
        ],
    }
    with pytest.raises(ValueError, match="descendant"):
        build_folder_plan(
            tree=tree,
            role_mappings={"shot": "shot-role", "task": "task-role"},
            production_id="prod-uuid", shot_id="shot-uuid", shot_name="sh010",
            task_id="task-uuid", task_name="comp",
        )
