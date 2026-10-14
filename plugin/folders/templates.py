"""Safe shared-template discovery and optimistic configuration updates."""

from dataclasses import dataclass
from uuid import UUID

from ..storage.base import AmbiguousOutcome, StorageAdapter
from ..storage.drive_url import parse_google_drive_folder_url
from .discovery import TemplateFolder, discover_template_tree


ROLE_NAMES = {
    "sequence", "shot", "task", "asset", "input", "working", "publish",
    "review", "handoff", "delivery", "editorial", "shared_assets",
}


class TemplateConflict(Exception):
    pass


class TemplateDenied(Exception):
    pass


@dataclass(frozen=True)
class TemplateCandidate:
    root_id: str
    drive_id: str | None
    templates: tuple[TemplateFolder, ...]


def _tree_ids(tree: TemplateFolder) -> set[str]:
    found = {tree.provider_folder_id}
    for child in tree.children:
        found.update(_tree_ids(child))
    return found


def _is_ancestor(tree: TemplateFolder, ancestor_id: str, target_id: str) -> bool:
    if tree.provider_folder_id == ancestor_id:
        return any(_contains_folder(child, target_id) for child in tree.children)
    return any(_is_ancestor(child, ancestor_id, target_id) for child in tree.children)


def _contains_folder(tree: TemplateFolder, folder_id: str) -> bool:
    return tree.provider_folder_id == folder_id or any(_contains_folder(child, folder_id) for child in tree.children)


def _to_json(tree: TemplateFolder) -> dict:
    return {
        "folder_id": tree.provider_folder_id,
        "name": tree.name,
        "children": [_to_json(child) for child in tree.children],
    }


def discover_candidates(adapter: StorageAdapter, *, root_url: str, authorized_root_id: str) -> TemplateCandidate:
    candidate_root_id = parse_google_drive_folder_url(root_url)
    if candidate_root_id != authorized_root_id:
        raise TemplateDenied("Imported Drive root does not match the Production authorized StorageConnection")
    capabilities = adapter.capabilities(authorized_root_id)
    if not capabilities.root_visible or capabilities.root_id != authorized_root_id:
        raise TemplateDenied("Configured Drive root is not accessible")
    page_token = None
    candidates = []
    while True:
        listing = adapter.list_children(authorized_root_id, page_token)
        if not listing.complete:
            raise AmbiguousOutcome("Drive root listing is incomplete; template discovery cannot be trusted")
        for item in listing.items:
            if item.trashed or item.mime_type != "folder":
                continue
            if item.parent_id != authorized_root_id:
                raise AmbiguousOutcome("Drive returned a candidate folder outside the authorized root")
            candidates.append(discover_template_tree(
                adapter,
                allowed_root_id=authorized_root_id,
                template_folder_id=item.object_id,
            ))
        if listing.next_page_token is None:
            break
        if listing.next_page_token == page_token:
            raise AmbiguousOutcome("Drive repeated a pagination token")
        page_token = listing.next_page_token
    return TemplateCandidate(authorized_root_id, capabilities.drive_id, tuple(candidates))


def normalize_role_mappings(tree: TemplateFolder, values: object) -> dict[str, str]:
    if not isinstance(values, dict):
        raise ValueError("Template role mappings must be an object")
    available = _tree_ids(tree)
    mappings = {}
    for role, folder_id in values.items():
        if role not in ROLE_NAMES:
            raise ValueError("Template role mapping contains an unsupported role")
        ids = [folder_id] if isinstance(folder_id, str) else folder_id
        if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or item not in available for item in ids):
            raise ValueError("Template role mapping must reference folders in the selected template")
        mappings[role] = ids[0] if len(ids) == 1 else ids
    entity_roles = [role for role in ("shot", "asset") if role in mappings]
    task_ids = _mapping_ids(mappings.get("task"))
    if not entity_roles or not task_ids:
        raise ValueError("A Shot or Asset role and a Task role are required before provisioning can be activated")
    for role in entity_roles:
        for entity_id in _mapping_ids(mappings[role]):
            if not any(_is_ancestor(tree, entity_id, task_id) for task_id in task_ids):
                raise ValueError("Task template role must be a descendant of each selected Shot or Asset role")
    sequence_ids = _mapping_ids(mappings.get("sequence"))
    if sequence_ids and ("shot" not in entity_roles or any(
        not any(_is_ancestor(tree, sequence_id, shot_id) for sequence_id in sequence_ids)
        for shot_id in _mapping_ids(mappings["shot"])
    )):
        raise ValueError("Sequence template role must be an ancestor of a Shot role")
    episode_ids = _mapping_ids(mappings.get("episode"))
    if episode_ids and (not sequence_ids or any(
        not any(_is_ancestor(tree, episode_id, sequence_id) for episode_id in episode_ids)
        for sequence_id in sequence_ids
    )):
        raise ValueError("Episode template role must be an ancestor of a Sequence role")
    return mappings


def _mapping_ids(value):
    if isinstance(value, str):
        return [value]
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


def save_configuration(
    session_factory,
    *,
    production_id: UUID,
    storage_connection_id: UUID,
    allowed_root_id: str,
    tree: TemplateFolder,
    role_mappings: dict[str, str],
    actor_id: UUID,
    expected_version: int | None,
):
    from sqlalchemy import select
    from ..models import SharedTemplateConfiguration, StorageConnection

    with session_factory.begin() as session:
        connection = session.scalar(
            select(StorageConnection).where(StorageConnection.id == storage_connection_id).with_for_update()
        )
        if (
            connection is None
            or not connection.enabled
            or connection.production_id != production_id
            or connection.allowed_root_id != allowed_root_id
        ):
            raise TemplateDenied("Production StorageConnection is unavailable or does not authorize this root")
        row = session.scalar(
            select(SharedTemplateConfiguration)
            .where(
                SharedTemplateConfiguration.production_id == production_id,
                SharedTemplateConfiguration.storage_connection_id == storage_connection_id,
            )
            .with_for_update()
        )
        if row is None:
            if expected_version not in (None, 0):
                raise TemplateConflict("Shared template configuration changed; reload before editing")
            row = SharedTemplateConfiguration(
                production_id=production_id,
                storage_connection_id=storage_connection_id,
                allowed_root_id=allowed_root_id,
                template_folder_id=tree.provider_folder_id,
                template_tree=_to_json(tree),
                role_mappings=role_mappings,
                version=1,
                enabled=False,
                updated_by=actor_id,
            )
            session.add(row)
        else:
            if expected_version != row.version:
                raise TemplateConflict("Shared template configuration changed; reload before editing")
            row.allowed_root_id = allowed_root_id
            row.template_folder_id = tree.provider_folder_id
            row.template_tree = _to_json(tree)
            row.role_mappings = role_mappings
            row.version += 1
            row.enabled = False
            row.updated_by = actor_id
        session.flush()
        return row


def set_configuration_enabled(
    session_factory,
    *,
    production_id: UUID,
    actor_id: UUID,
    expected_version: int,
    enabled: bool,
    storage_connection_id: UUID | None = None,
    authorized_root_id: str | None = None,
):
    from sqlalchemy import select
    from ..models import SharedTemplateConfiguration

    with session_factory.begin() as session:
        row = session.scalar(
            select(SharedTemplateConfiguration)
            .where(SharedTemplateConfiguration.production_id == production_id)
            .with_for_update()
        )
        if row is None or row.version != expected_version:
            raise TemplateConflict("Shared template configuration changed; reload before activating")
        if enabled and (
            row.storage_connection_id != storage_connection_id
            or row.allowed_root_id != authorized_root_id
        ):
            raise TemplateDenied("Shared template does not match the currently authorized StorageConnection")
        row.enabled = enabled
        row.updated_by = actor_id
        session.flush()
        return row
