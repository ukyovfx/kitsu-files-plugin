"""Read and validate a folder-only template tree under an authorized root."""

from dataclasses import dataclass

from ..storage.base import AmbiguousOutcome, StorageAdapter, StorageObject


MAX_TEMPLATE_DEPTH = 32
MAX_TEMPLATE_NODES = 10_000


@dataclass(frozen=True)
class TemplateFolder:
    provider_folder_id: str
    name: str
    children: tuple["TemplateFolder", ...] = ()


def discover_template_tree(
    adapter: StorageAdapter,
    *,
    allowed_root_id: str,
    template_folder_id: str,
    max_depth: int = MAX_TEMPLATE_DEPTH,
    max_nodes: int = MAX_TEMPLATE_NODES,
) -> TemplateFolder:
    """Return a complete folder tree without reading or copying file contents.

    Display names are retained verbatim but never used as identity. Incomplete
    provider searches and malformed ancestry fail closed so a partial template
    cannot silently become the source of a provisioning configuration.
    """
    if max_depth < 0 or max_nodes < 1:
        raise ValueError("Template discovery limits must be positive")

    capabilities = adapter.capabilities(allowed_root_id)
    if not capabilities.root_visible or capabilities.root_id != allowed_root_id:
        raise PermissionError("Authorized storage root is not visible")
    adapter.assert_parent_within_root(template_folder_id)

    root = adapter.get(template_folder_id)
    if root is None or root.trashed or root.mime_type != "folder":
        raise PermissionError("Template root is not an accessible folder")
    _assert_folder_ancestry(root, allowed_root_id, capabilities.drive_id)

    visited: set[str] = set()
    node_count = 0

    def build(folder: StorageObject, depth: int) -> TemplateFolder:
        nonlocal node_count
        if depth > max_depth:
            raise ValueError("Template exceeds the configured maximum depth")
        if folder.object_id in visited:
            raise ValueError("Template contains a duplicate folder identity or cycle")
        if not folder.name or not folder.name.strip():
            raise ValueError("Template folder has no usable display name")
        visited.add(folder.object_id)
        node_count += 1
        if node_count > max_nodes:
            raise ValueError("Template exceeds the configured maximum node count")

        children: list[TemplateFolder] = []
        page_token = None
        while True:
            listing = adapter.list_children(folder.object_id, page_token)
            if not listing.complete:
                raise AmbiguousOutcome("Provider returned an incomplete template listing")
            for child in listing.items:
                if child.trashed or child.mime_type != "folder":
                    continue
                if child.parent_id != folder.object_id:
                    raise AmbiguousOutcome("Provider returned a template folder with unexpected ancestry")
                _assert_folder_ancestry(child, allowed_root_id, capabilities.drive_id)
                children.append(build(child, depth + 1))
            if listing.next_page_token is None:
                break
            if listing.next_page_token == page_token:
                raise AmbiguousOutcome("Provider repeated a template pagination token")
            page_token = listing.next_page_token

        return TemplateFolder(folder.object_id, folder.name, tuple(children))

    return build(root, 0)


def _assert_folder_ancestry(folder: StorageObject, allowed_root_id: str, drive_id: str | None) -> None:
    if folder.object_id != allowed_root_id and folder.parent_id is None:
        raise PermissionError("Template folder is outside the authorized root")
    if drive_id is not None and folder.drive_id != drive_id:
        raise PermissionError("Template folder belongs to a different Shared Drive")
