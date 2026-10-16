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


def test_asset_task_template_includes_nested_working_and_output_folders_by_template_id():
    tree = {"folder_id": "root", "name": "Assets", "children": [
        {"folder_id": "asset", "name": "{asset}", "children": [
            {"folder_id": "task", "name": "{task}", "children": [
                {"folder_id": "working", "name": "Working", "children": [{"folder_id": "cache", "name": "Cache", "children": []}]},
                {"folder_id": "publish", "name": "Publish", "children": []},
            ]},
        ]},
    ]}
    plan = build_folder_plan(
        tree=tree,
        role_mappings={"asset": "asset", "task": "task"},
        production_id="prod", shot_id="", shot_name="", task_id="task-uuid", task_name="Model",
        asset_id="asset-uuid", asset_name="Hero",
    )
    assert [item.display_name for item in plan] == ["Hero", "Model", "Working", "Cache", "Publish"]
    assert [item.scope_key for item in plan][-3:] == [
        "production:prod:task:task-uuid:template:working",
        "production:prod:task:task-uuid:template:working:template:cache",
        "production:prod:task:task-uuid:template:publish",
    ]


def test_materialized_tree_supports_mixed_asset_and_shot_branches_with_optional_episode_scope():
    from plugin.integrations.template_materialization import materialize_template_selection
    from plugin.integrations.providers import templates_payload

    selections = []
    for scope in ("production", "asset", "sequence", "shot", "task"):
        template = templates_payload(scope)["templates"][0]
        selections.append({
            "scope": scope, "template_id": template["id"], "revision": template["revision"], "overrides": {},
        })
    tree, roles, _root = materialize_template_selection(selections)

    shot_plan = build_folder_plan(
        tree=tree, role_mappings=roles, production_id="prod",
        shot_id="shot-id", shot_name="SH010", sequence_id="sequence-id", sequence_name="SQ01",
        episode_id="episode-id", episode_name="EP01", task_id="shot-task", task_name="comp",
        included_roles={"sequence"},
    )
    asset_plan = build_folder_plan(
        tree=tree, role_mappings=roles, production_id="prod",
        shot_id="", shot_name="", asset_id="asset-id", asset_name="Hero",
        task_id="asset-task", task_name="model", included_roles=set(),
    )

    assert [(item.display_name, item.scope_key) for item in shot_plan[:5]] == [
        ("00_Production", "production:prod:root:template:vfx-files-v1:00-production"),
        ("01_Shared_Assets", "production:prod:root:template:vfx-files-v1:01-shared-assets"),
        ("02_Shots", "production:prod:root:template:vfx-files-v1:02-shots"),
        ("SQ01", "production:prod:sequence:sequence-id"),
        ("SH010", "production:prod:shot:shot-id"),
    ]
    root_scope = "production:prod:root"
    assert [item.parent_scope_key for item in shot_plan[:3]] == [root_scope] * 3
    assert shot_plan[3].parent_scope_key == shot_plan[2].scope_key
    assert all("episode:episode-id" not in item.scope_key for item in shot_plan)
    assert ("Hero", "production:prod:asset:asset-id") in [(item.display_name, item.scope_key) for item in asset_plan]
    assert ("model", "production:prod:task:asset-task") in [(item.display_name, item.scope_key) for item in asset_plan]
