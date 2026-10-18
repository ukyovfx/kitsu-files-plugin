"""Resolve immutable v1 catalog selections to the shared folder tree."""

from __future__ import annotations

import re


ROOT_BINDING = "verified_root_is_production_root"
TREE_ROOT_ID = "vfx-files-v1:production-root"
ROOT_LAYOUT = (
    ("vfx-files-v1:00-production", "00_Production"),
    ("vfx-files-v1:01-shared-assets", "01_Shared_Assets"),
    ("vfx-files-v1:02-shots", "02_Shots"),
    ("vfx-files-v1:03-editorial", "03_Editorial"),
    ("vfx-files-v1:04-delivery", "04_Delivery"),
)
TASK_CHILDREN = (
    ("vfx-files-v1:working", "Working"),
    ("vfx-files-v1:publish", "Publish"),
    ("vfx-files-v1:shared-outputs", "Shared Outputs"),
    ("vfx-files-v1:delivery", "Final Delivery"),
)
OVERRIDE_KEYS = {
    "production": {
        "production.00_production", "production.shared_assets", "production.shots",
        "production.editorial", "production.delivery",
    },
    "asset": {"asset.folder"},
    "sequence": {"sequence.episode", "sequence.folder"},
    "shot": {"shot.folder"},
    "task": {"task.folder", "task.working", "task.publish", "task.shared_outputs", "task.delivery"},
}
_SAFE_NAME = re.compile(r"^(?!\.{1,2}$)[^/\\\x00-\x1f\x7f]+$")
_TOKEN = re.compile(r"\{([a-z_]+)\}")
_TOKENS_BY_SCOPE = {
    "production": set(),
    "asset": {"asset"},
    "sequence": {"episode", "sequence"},
    "shot": {"episode", "sequence", "shot"},
    "task": {"task"},
}


def available_override_keys(scope: str) -> list[str]:
    return sorted(OVERRIDE_KEYS[scope])


def _node(folder_id: str, name: str, *, role: str | None = None, children=None):
    value = {"folder_id": folder_id, "name": name, "children": list(children or [])}
    if role:
        value["role"] = role
    return value


def _safe_override(scope: str, key: str, value: object) -> str:
    if key not in OVERRIDE_KEYS[scope] or not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError("Template override is not declared for the selected scope")
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError("Template override must be one safe folder name")
    tokens = _TOKEN.findall(value)
    if any(token not in _TOKENS_BY_SCOPE[scope] for token in tokens):
        raise ValueError("Template override contains an unavailable token")
    # Removing recognized placeholders must leave a valid single path component.
    if not _SAFE_NAME.fullmatch(_TOKEN.sub("x", value)):
        raise ValueError("Template override must be one safe folder name")
    return value


def materialize_template_selection(selections: object):
    """Return deterministic folder tree, role IDs, and explicit root semantics.

    The selected verified Drive root is the Production's folder. Static template
    folders are children of that root. Episode, Sequence, Shot, Asset and Task
    nodes are keyed by their stable Kitsu UUID when provisioned.
    """
    from .providers import _TEMPLATE_CATALOG

    if not isinstance(selections, list) or not selections:
        raise ValueError("At least one template selection is required")
    selected = {}
    overrides = {}
    for item in selections:
        if not isinstance(item, dict) or set(item) != {"scope", "template_id", "revision", "overrides"}:
            raise ValueError("Template selection is invalid")
        scope = item["scope"]
        if scope not in _TEMPLATE_CATALOG or scope in selected:
            raise ValueError("Template selection scope is unsupported or duplicated")
        template_id, _label, _pattern, _tokens = _TEMPLATE_CATALOG[scope]
        if item["template_id"] != template_id:
            raise ValueError("Template selection ID is not present in the current catalog")
        if item["revision"] != 1:
            raise ValueError("Template selection revision is stale")
        item_overrides = item["overrides"]
        if not isinstance(item_overrides, dict):
            raise ValueError("Template overrides must be an object")
        overrides[scope] = {key: _safe_override(scope, key, value) for key, value in item_overrides.items()}
        selected[scope] = item
    # A root-only configuration is useful while KitsuSync is still being
    # configured. It cannot be previewed or activated until at least one
    # entity and Task scope are selected (enforced below the config boundary).
    if ({"asset", "shot", "sequence"} & selected.keys()) and (
        "task" not in selected or not ({"asset", "shot"} & selected.keys())
    ):
        raise ValueError("Entity templates require Task and Shot or Asset templates")
    if "sequence" in selected and "shot" not in selected:
        raise ValueError("Sequence templates require a Shot template")

    def label(scope, key, default):
        return overrides.get(scope, {}).get(key, default)

    task_nodes = {}
    role_ids = {"asset": [], "episode": [], "sequence": [], "shot": [], "task": []}

    asset_tree = []
    if "asset" in selected:
        asset_task = _node(
            "vfx-files-v1:task-asset", label("task", "task.folder", "{task}"), role="task",
            children=[_node(folder_id, label("task", f"task.{folder_id.split(':')[-1].replace('-', '_')}", name)) for folder_id, name in TASK_CHILDREN],
        )
        asset_node = _node("vfx-files-v1:asset", label("asset", "asset.folder", "{asset}"), role="asset", children=[asset_task])
        asset_tree = [asset_node]
        role_ids["asset"].append("vfx-files-v1:asset")
        role_ids["task"].append("vfx-files-v1:task-asset")

    shot_tree = []
    if "shot" in selected:
        shot_task = _node(
            "vfx-files-v1:task-shot", label("task", "task.folder", "{task}"), role="task",
            children=[_node(folder_id, label("task", f"task.{folder_id.split(':')[-1].replace('-', '_')}", name)) for folder_id, name in TASK_CHILDREN],
        )
        shot_node = _node("vfx-files-v1:shot", label("shot", "shot.folder", "{shot}"), role="shot", children=[shot_task])
        if "sequence" in selected:
            sequence_node = _node("vfx-files-v1:sequence", label("sequence", "sequence.folder", "{sequence}"), role="sequence", children=[shot_node])
            episode_node = _node("vfx-files-v1:episode", label("sequence", "sequence.episode", "{episode}"), role="episode", children=[sequence_node])
            shot_tree = [episode_node]
            role_ids["episode"].append("vfx-files-v1:episode")
            role_ids["sequence"].append("vfx-files-v1:sequence")
        else:
            shot_tree = [shot_node]
        role_ids["shot"].append("vfx-files-v1:shot")
        role_ids["task"].append("vfx-files-v1:task-shot")

    roots = []
    for folder_id, default_name in ROOT_LAYOUT:
        key = {
            "00-production": "production.00_production",
            "01-shared-assets": "production.shared_assets",
            "02-shots": "production.shots",
            "03-editorial": "production.editorial",
            "04-delivery": "production.delivery",
        }[folder_id.split(":")[-1]]
        children = asset_tree if folder_id.endswith("01-shared-assets") else shot_tree if folder_id.endswith("02-shots") else []
        roots.append(_node(folder_id, label("production", key, default_name), children=children))
    tree = {"folder_id": TREE_ROOT_ID, "name": "", "children": roots}
    return tree, {role: values for role, values in role_ids.items() if values}, ROOT_BINDING


def validate_sync_template_scope(selections: object, scope: object, *, allow_empty: bool = True):
    """Require the selected immutable template roles to cover sync scope.

    KitsuSync may save the root/connection before enabling hierarchy sync. In
    that setup state an all-false scope is valid, but it cannot be activated or
    previewed. Every enabled hierarchy kind must have an explicit selected
    template role; Task folders require at least one selected parent entity.
    """
    if not isinstance(scope, dict):
        raise ValueError("Sync scope must be an object")
    tree, role_mappings, _root_binding = materialize_template_selection(selections)
    enabled = {name for name in ("episodes", "sequences", "shots", "assets", "tasks") if scope.get(name) is True}
    if not enabled:
        if allow_empty:
            return tree, role_mappings
        raise ValueError("At least one Task hierarchy scope must be enabled")
    if "tasks" not in enabled:
        raise ValueError("Enabled hierarchy scopes require Task synchronization")
    if not ({"shots", "assets"} & enabled):
        raise ValueError("Task synchronization requires an enabled Shot or Asset parent scope")
    required_roles = {
        "episodes": "episode",
        "sequences": "sequence",
        "shots": "shot",
        "assets": "asset",
        "tasks": "task",
    }
    missing = [role for name, role in required_roles.items() if name in enabled and role not in role_mappings]
    if missing:
        raise ValueError("Selected templates do not cover enabled sync scope")
    if "episodes" in enabled and "sequences" not in enabled:
        raise ValueError("Episode synchronization requires Sequence synchronization")
    if "sequences" in enabled and "shots" not in enabled:
        raise ValueError("Sequence synchronization requires Shot synchronization")
    return tree, role_mappings
