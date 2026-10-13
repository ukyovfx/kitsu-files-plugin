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
    return re.sub(r"\{(sequence|shot|asset|task)\}", lambda match: values.get(match.group(1), match.group(0)), template_name)


def build_folder_plan(
    *,
    tree: dict[str, Any],
    role_mappings: dict[str, str],
    production_id: str,
    shot_id: str,
    shot_name: str,
    task_id: str,
    task_name: str,
    sequence_id: str | None = None,
    sequence_name: str | None = None,
    asset_id: str | None = None,
    asset_name: str | None = None,
) -> list[PlannedFolder]:
    """Build only the template path through the configured Task role.

    Kitsu UUIDs identify dynamic Sequence, Shot and Task folders. Unmapped
    template directories keep their provider template folder IDs beneath the
    preceding scope, so nested directory structure is retained without using
    display names as identity.
    """
    if "shot" in role_mappings and "asset" in role_mappings:
        raise ValueError("Map either a Shot or Asset entity, not both")
    entity_role = "shot" if "shot" in role_mappings else "asset" if "asset" in role_mappings else None
    if entity_role is None:
        raise ValueError("Template must map a Shot or Asset role")
    entity_node = role_mappings.get(entity_role)
    task_node = role_mappings.get("task")
    entity_path = _find_path(tree, entity_node) if entity_node else None
    task_path = _find_path(tree, task_node) if task_node else None
    if entity_path is None or task_path is None or task_path[:len(entity_path)] != entity_path:
        raise ValueError("Task template role must be a descendant of its Shot or Asset role")
    if not entity_path:
        raise ValueError("Shot or Asset template role cannot be the template root")

    sequence_node = role_mappings.get("sequence")
    if sequence_node:
        sequence_path = _find_path(tree, sequence_node)
        if entity_role != "shot" or sequence_path is None or len(sequence_path) >= len(entity_path) or entity_path[:len(sequence_path)] != sequence_path:
            raise ValueError("Sequence template role must be an ancestor of the Shot role")
        if not sequence_id or not sequence_name:
            raise ValueError("A Sequence-mapped template requires a resolved Kitsu Sequence")

    role_by_node = {node_id: role for role, node_id in role_mappings.items()}
    entity_values = {
        "sequence": (sequence_id, sequence_name),
        "shot": (shot_id, shot_name),
        "asset": (asset_id, asset_name),
        "task": (task_id, task_name),
    }
    output: list[PlannedFolder] = []
    parent_scope = f"production:{production_id}:root"
    for node in task_path:
        template_id = node["folder_id"]
        role = role_by_node.get(template_id)
        if role in entity_values:
            entity_id, entity_name = entity_values[role]
            if not entity_id or not entity_name:
                raise ValueError(f"Template requires a resolved Kitsu {role.title()}")
            scope = f"production:{production_id}:{role}:{entity_id}"
            expanded = _expand_name(node["name"], {key: value[1] for key, value in entity_values.items() if value[1]})
            display_name = expanded if expanded != node["name"] else entity_name
        else:
            scope = f"{parent_scope}:template:{template_id}"
            display_name = _expand_name(node["name"], {key: value[1] for key, value in entity_values.items() if value[1]})
        if not display_name.strip():
            raise ValueError("Template produced an empty folder name")
        output.append(PlannedFolder(template_id, scope, parent_scope, display_name))
        parent_scope = scope
    # The configured Task root may contain arbitrary nested working/output directories.
    # Preserve that hierarchy and use template IDs, never names, for identity.
    def append_children(node: dict[str, Any], parent: str) -> None:
        for child in node.get("children", []):
            scope = f"{parent}:template:{child['folder_id']}"
            display = _expand_name(child["name"], {key: value[1] for key, value in entity_values.items() if value[1]})
            if not display.strip():
                raise ValueError("Template produced an empty folder name")
            output.append(PlannedFolder(child["folder_id"], scope, parent, display))
            append_children(child, scope)

    task_template_node = _find_node(tree, task_node)
    append_children(task_template_node, parent_scope)
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
):
    """Validate current Production/task/template bindings, then ensure the path."""
    from sqlalchemy import select
    from zou.app.models.entity import Entity
    from zou.app.models.entity_type import EntityType
    from zou.app.models.task import Task
    from zou.app.models.task_type import TaskType

    from ..models import SharedTemplateConfiguration
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
        parent = session.get(Entity, entity.parent_id) if entity.parent_id is not None else None
        sequence_id = sequence_name = None
        if "sequence" in mappings:
            if parent is None or parent.project_id != production_id:
                raise FolderProvisioningError("Configured Sequence role has no Kitsu Sequence ancestor")
            parent_type = session.get(EntityType, parent.entity_type_id)
            sequence_type_name = (getattr(parent_type, "name", "") or "").casefold()
            sequence_type_code = (getattr(parent_type, "code", "") or "").casefold()
            if "sequence" not in (sequence_type_name, sequence_type_code):
                raise FolderProvisioningError("Configured Sequence role does not resolve to a Kitsu Sequence entity")
            sequence_id, sequence_name = str(parent.id), parent.name

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
        )
        if result.state != "ready" or result.provider_folder_id is None:
            raise FolderProvisioningError("Provider folder creation needs reconciliation")
        parent_provider_id = result.provider_folder_id
    return parent_provider_id
