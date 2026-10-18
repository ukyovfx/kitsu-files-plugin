from copy import deepcopy

import pytest

from plugin.integrations.providers import templates_payload
from plugin.integrations.template_materialization import (
    ROOT_BINDING,
    materialize_template_selection,
    validate_sync_template_scope,
)


def _selection(scope, **overrides):
    template = templates_payload(scope)["templates"][0]
    return {
        "scope": scope,
        "template_id": template["id"],
        "revision": template["revision"],
        "overrides": overrides,
    }


def test_materializes_mixed_shot_and_asset_tasks_under_verified_production_root():
    tree, roles, root_binding = materialize_template_selection([
        _selection("production"),
        _selection("asset"),
        _selection("sequence"),
        _selection("shot"),
        _selection("task"),
    ])

    assert root_binding == ROOT_BINDING == "verified_root_is_production_root"
    assert roles["asset"] == ["vfx-files-v1:asset"]
    assert roles["shot"] == ["vfx-files-v1:shot"]
    assert roles["task"] == ["vfx-files-v1:task-asset", "vfx-files-v1:task-shot"]
    assert roles["episode"] == ["vfx-files-v1:episode"]
    assert roles["sequence"] == ["vfx-files-v1:sequence"]
    assert tree["folder_id"] == "vfx-files-v1:production-root"
    assert [node["name"] for node in tree["children"]] == [
        "00_Production", "01_Shared_Assets", "02_Shots", "03_Editorial", "04_Delivery"
    ]
    assert tree["children"][1]["children"][0]["folder_id"] == roles["asset"][0]
    assert tree["children"][2]["children"][0]["folder_id"] == roles["episode"][0]


def test_materialization_is_deterministic_and_applies_only_declared_node_overrides():
    selections = [_selection("production", **{"production.delivery": "04_Final"}), _selection("shot"), _selection("task")]
    first = materialize_template_selection(selections)
    second = materialize_template_selection(deepcopy(selections))

    assert first == second
    assert first[0]["children"][-1]["name"] == "04_Final"


@pytest.mark.parametrize("override", ["", "../escape", "folder/name", "\\\\server", ".", ".."])
def test_materialization_rejects_unsafe_folder_name_override(override):
    with pytest.raises(ValueError):
        materialize_template_selection([_selection("production", **{"production.delivery": override}), _selection("shot"), _selection("task")])


def test_materialization_rejects_unknown_or_wrong_scope_template_selection():
    invalid = _selection("shot")
    invalid["template_id"] = "unpublished-template"
    with pytest.raises(ValueError):
        materialize_template_selection([invalid, _selection("task")])

    with pytest.raises(ValueError):
        materialize_template_selection([_selection("sequence"), _selection("task")])


def test_sync_scope_requires_matching_materialized_entity_and_task_roles():
    selections = [_selection("production"), _selection("shot"), _selection("task")]
    validate_sync_template_scope(selections, {
        "episodes": False, "sequences": False, "shots": True, "assets": False, "tasks": True,
    })
    with pytest.raises(ValueError):
        validate_sync_template_scope(selections, {
            "episodes": False, "sequences": False, "shots": False, "assets": False, "tasks": True,
        })
    with pytest.raises(ValueError):
        validate_sync_template_scope(selections, {
            "episodes": False, "sequences": False, "shots": True, "assets": True, "tasks": True,
        })
    with pytest.raises(ValueError):
        validate_sync_template_scope(selections, {
            "episodes": True, "sequences": False, "shots": True, "assets": False, "tasks": True,
        })


def test_root_only_configuration_is_storable_but_not_previewable():
    selections = [_selection("production")]
    empty_scope = {"episodes": False, "sequences": False, "shots": False, "assets": False, "tasks": False}
    tree, roles = validate_sync_template_scope(selections, empty_scope)
    assert tree["folder_id"] == "vfx-files-v1:production-root"
    assert roles == {}
    with pytest.raises(ValueError):
        validate_sync_template_scope(selections, empty_scope, allow_empty=False)

    from plugin.integrations.configuration import ConfigurationError, validate_desired_config

    root_only_payload = {
        "contract_version": "1.0",
        "connection_id": "10000000-0000-4000-8000-000000000001",
        "drive": {"account_mode": "shared_drive", "drive_id": "drive", "owner_subject": None,
                  "root_id": "root", "root_state": "verified"},
        "template_selection": {"selections": selections},
        "sync_policy": {"mode": "new_production", "scope": empty_scope,
                        "notify_new_deliveries": False},
    }
    assert validate_desired_config(root_only_payload) == root_only_payload
    root_only_payload["sync_policy"]["scope"] = dict(empty_scope, tasks=True)
    with pytest.raises(ConfigurationError) as rejected:
        validate_desired_config(root_only_payload)
    assert rejected.value.code == "invalid_event"
