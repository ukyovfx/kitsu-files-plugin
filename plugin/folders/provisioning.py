"""Materialize the configured folder-only template for a Kitsu Task."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .service import FolderProvisioningError, PostgresFolderStore, provision_folder


@dataclass(frozen=True)
class PlannedFolder:
    template_folder_id: str
    scope_key: str
    parent_scope_key: str
    display_name: str


def _find_path(tree: dict[str, Any], target_id: str, path: list[dict[str, Any]] | None = None):
    path = [] if path is None else path
    if tree["folder_id"] == target_id:
        return path
    for child in tree.get("children", []):
        found = _find_path(child, target_id, [*path, child])
        if found is not None:
            return found
    return None


def _expand_name(template_name: str, values: dict[str, str]) -> str:
    return re.sub(r"\{(episode|sequence|shot|asset|task)\}", lambda match: values.get(match.group(1), match.group(0)), template_name)


def _mapping_ids(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return value
    return []


def build_folder_plan(
    *,
    tree: dict[str, Any],
    role_mappings: dict[str, str | list[str]],
    production_id: str,
    shot_id: str,
    shot_name: str,
    task_id: str,
    task_name: str,
    sequence_id: str | None = None,
    sequence_name: str | None = None,
    episode_id: str | None = None,
    episode_name: str | None = None,
    asset_id: str | None = None,
    asset_name: str | None = None,
    included_roles: set[str] | None = None,
) -> list[PlannedFolder]:
    """Plan mixed Shot/Asset Task paths from the immutable selected tree.

    The selected verified root is the Production folder. Static root siblings
    and the selected entity branch use stable template IDs; Kitsu UUIDs identify
    dynamic Episode, Sequence, Shot, Asset and Task folders. Names never select
    or adopt an existing provider folder.
    """
    mappings = {role: _mapping_ids(value) for role, value in role_mappings.items()}
    entity_role = "shot" if shot_id and "shot" in mappings else "asset" if asset_id and "asset" in mappings else None
    if entity_role is None:
        raise ValueError("Template has no mapping for this Kitsu Task entity")
    entity_id, entity_name = (shot_id, shot_name) if entity_role == "shot" else (asset_id, asset_name)
    entity_path = next((path for node_id in mappings[entity_role] if (path := _find_path(tree, node_id)) is not None), None)
    if not entity_path:
        raise ValueError("Entity template role is not present in the selected tree")
    task_path = None
    task_node = None
    for candidate_id in mappings.get("task", []):
        candidate_path = _find_path(tree, candidate_id)
        if candidate_path is not None and candidate_path[:len(entity_path)] == entity_path:
            task_path, task_node = candidate_path, _find_node(tree, candidate_id)
            break
    if task_path is None or task_node is None:
        raise ValueError("Task template role must be a descendant of the selected Shot or Asset role")

    role_by_node = {node_id: role for role, node_ids in mappings.items() for node_id in node_ids}
    values = {
        "episode": (episode_id, episode_name),
        "sequence": (sequence_id, sequence_name),
        "shot": (shot_id, shot_name),
        "asset": (asset_id, asset_name),
        "task": (task_id, task_name),
    }
    names = {key: value[1] for key, value in values.items() if value[1]}
    included_roles = included_roles if included_roles is not None else set(role_by_node.values())
    output: list[PlannedFolder] = []
    root_scope = f"production:{production_id}:root"
    parent_scope = root_scope
    path_ids = {node["folder_id"] for node in task_path}
    for root_child in tree.get("children", []):
        if role_by_node.get(root_child["folder_id"]) and root_child["folder_id"] not in path_ids:
            continue
        if root_child["folder_id"] in path_ids:
            for node in task_path:
                template_id = node["folder_id"]
                role = role_by_node.get(template_id)
                if role in ("episode", "sequence") and role not in included_roles:
                    continue
                if role in values:
                    role_id, role_name = values[role]
                    if not role_id or not role_name:
                        if role in ("episode", "sequence"):
                            continue
                        raise ValueError(f"Template requires a resolved Kitsu {role.title()}")
                    scope = f"production:{production_id}:{role}:{role_id}"
                    expanded = _expand_name(node["name"], names)
                    display_name = expanded if expanded != node["name"] else role_name
                else:
                    scope = f"{parent_scope}:template:{template_id}"
                    display_name = _expand_name(node["name"], names)
                if not display_name.strip():
                    raise ValueError("Template produced an empty folder name")
                output.append(PlannedFolder(template_id, scope, parent_scope, display_name))
                parent_scope = scope
            continue
        display = _expand_name(root_child["name"], names)
        if not display.strip():
            raise ValueError("Template produced an empty folder name")
        scope = f"{root_scope}:template:{root_child['folder_id']}"
        output.append(PlannedFolder(root_child["folder_id"], scope, root_scope, display))

    def append_children(node: dict[str, Any], parent: str) -> None:
        for child in node.get("children", []):
            scope = f"{parent}:template:{child['folder_id']}"
            display = _expand_name(child["name"], names)
            if not display.strip():
                raise ValueError("Template produced an empty folder name")
            output.append(PlannedFolder(child["folder_id"], scope, parent, display))
            append_children(child, scope)

    append_children(task_node, parent_scope)
    return output


def _find_node(tree: dict[str, Any], target_id: str) -> dict[str, Any] | None:
    if tree["folder_id"] == target_id:
        return tree
    for child in tree.get("children", []):
        found = _find_node(child, target_id)
        if found is not None:
            return found
    return None


def provision_task_folders(
    session_factory,
    adapter,
    *,
    production_id: UUID,
    task_id: UUID,
    configuration_id: UUID,
    configuration_version: int,
    before_external=None,
    allow_name_adoption: bool = True,
):
    """Validate current Production/task/template bindings, then ensure the path."""
    from sqlalchemy import select
    from zou.app.models.entity import Entity
    from zou.app.models.entity_type import EntityType
    from zou.app.models.task import Task
    from zou.app.models.task_type import TaskType

    from ..models import KitsuSyncDesiredConfiguration, SharedTemplateConfiguration
    from ..integrations.storage_authority import selected_storage_connection
    from ..storage.base import resolve_storage_adapter

    with session_factory() as session:
        config = session.scalar(
            select(SharedTemplateConfiguration).where(
                SharedTemplateConfiguration.id == configuration_id,
                SharedTemplateConfiguration.production_id == production_id,
            )
        )
        connection = selected_storage_connection(session, production_id) if config is not None else None
        task = session.get(Task, task_id)
        task_type = session.get(TaskType, task.task_type_id) if task is not None else None
        if (
            config is None or connection is None or task is None
            or not config.enabled or config.version != configuration_version
            or connection.production_id != production_id or not connection.enabled
            or connection.id != config.storage_connection_id
            or config.allowed_root_id != connection.allowed_root_id
            or task.project_id != production_id or task_type is None or task_type.for_entity not in ("Shot", "Asset")
        ):
            raise FolderProvisioningError("Current Shot Task, template or Production storage configuration is unavailable")
        entity = session.get(Entity, task.entity_id)
        if entity is None or entity.project_id != production_id:
            raise FolderProvisioningError("Kitsu Shot is missing or outside the requested Production")
        production_root_id = connection.allowed_root_id
        tree = dict(config.template_tree)
        mappings = dict(config.role_mappings)
        connection_id = connection.id
        job_adapter = resolve_storage_adapter(adapter, connection)
        entity_id = str(entity.id)
        entity_name = entity.name
        task_name = task.name
        sequence_id = sequence_name = episode_id = episode_name = None
        parent = session.get(Entity, entity.parent_id) if entity.parent_id is not None else None
        visited = set()
        while parent is not None and parent.id not in visited:
            visited.add(parent.id)
            if parent.project_id != production_id:
                raise FolderProvisioningError("Kitsu hierarchy ancestor is outside the requested Production")
            parent_type = session.get(EntityType, parent.entity_type_id)
            type_names = {
                (getattr(parent_type, "name", "") or "").casefold(),
                (getattr(parent_type, "code", "") or "").casefold(),
            }
            if "sequence" in type_names and sequence_id is None:
                sequence_id, sequence_name = str(parent.id), parent.name
            if "episode" in type_names and episode_id is None:
                episode_id, episode_name = str(parent.id), parent.name
            parent = session.get(Entity, parent.parent_id) if parent.parent_id is not None else None
        sync_config = session.get(KitsuSyncDesiredConfiguration, production_id)
        if sync_config is None:
            included_roles = None
        else:
            sync_scope = sync_config.desired_config.get("sync_policy", {}).get("scope", {})
            included_roles = set()
            if sync_scope.get("episodes"):
                included_roles.add("episode")
            if sync_scope.get("sequences"):
                included_roles.add("sequence")

    plan = build_folder_plan(
        tree=tree,
        role_mappings=mappings,
        production_id=str(production_id),
        shot_id=entity_id if task_type.for_entity == "Shot" else "",
        shot_name=entity_name if task_type.for_entity == "Shot" else "",
        asset_id=entity_id if task_type.for_entity == "Asset" else None,
        asset_name=entity_name if task_type.for_entity == "Asset" else None,
        sequence_id=sequence_id,
        sequence_name=sequence_name,
        episode_id=episode_id,
        episode_name=episode_name,
        included_roles=included_roles,
        task_id=str(task_id),
        task_name=task_name,
    )
    store = PostgresFolderStore(session_factory)
    parent_provider_id = production_root_id
    for planned in plan:
        # Re-read the feature fence immediately before each external folder
        # mutation. Updates and deactivation take effect without waiting for the
        # job retry interval.
        with session_factory() as session:
            current = session.scalar(select(SharedTemplateConfiguration).where(SharedTemplateConfiguration.id == configuration_id))
            current_connection = selected_storage_connection(session, production_id)
            if (
                current is None or current_connection is None or current_connection.id != connection_id or not current.enabled
                or current.version != configuration_version or not current_connection.enabled
                or current.allowed_root_id != current_connection.allowed_root_id
                or current_connection.allowed_root_id != production_root_id
            ):
                raise FolderProvisioningError("Template or storage configuration changed while provisioning")
        if before_external is not None and not before_external():
            raise FolderProvisioningError("Sync authorization, activation or lease fence changed")
        result = provision_folder(
            job_adapter,
            store,
            storage_connection_id=str(connection_id),
            allowed_root_id=production_root_id,
            canonical_scope_key=planned.scope_key,
            parent_id=parent_provider_id,
            display_name=planned.display_name,
            allow_name_adoption=allow_name_adoption,
        )
        if result.state != "ready" or result.provider_folder_id is None:
            raise FolderProvisioningError("Provider folder creation needs reconciliation")
        parent_provider_id = result.provider_folder_id
    return parent_provider_id
