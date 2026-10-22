"""Bounded selection of objects independently readable by a personal grant.

drive.file listings can be filtered by scope. A preview describes visible files,
never certifies that a selected folder was copied in full.
"""
from ..integrations.personal_google import PersonalSourceDenied

FOLDER = "application/vnd.google-apps.folder"
MAX_DEPTH = 32


def component(name):
    if (not isinstance(name, str) or not name.strip() or len(name) > 255
            or name in (".", "..") or any(c in name for c in "/\\")
            or any(ord(c) < 32 for c in name)):
        raise PersonalSourceDenied("unsupported_file_name")
    return name


def relative_parts(name):
    if not isinstance(name, str) or len(name) > 1024:
        raise PersonalSourceDenied("unsupported_file_name")
    parts = [component(p) for p in name.split("/")]
    if len(parts) > MAX_DEPTH:
        raise PersonalSourceDenied("selection_too_deep")
    return parts


def resolve_selected_file(reader, file_id, root_id=None):
    meta = reader.get_metadata(file_id)
    if meta.get("trashed") or str(meta.get("mimeType", "")).startswith("application/vnd.google-apps."):
        raise PersonalSourceDenied("unsupported_drive_object")
    parts = [component(meta.get("name"))]
    if root_id is not None and (not isinstance(root_id, str) or not root_id):
        raise PersonalSourceDenied("invalid_drive_object")
    if root_id:
        current, seen = meta, {file_id}
        for _ in range(MAX_DEPTH - 1):
            parents = current.get("parents", [])
            if len(parents) != 1 or parents[0] in seen:
                raise PersonalSourceDenied("folder_ancestry_unverified")
            parent_id = parents[0]
            seen.add(parent_id)
            current = reader.get_metadata(parent_id)
            if current.get("trashed") or current.get("mimeType") != FOLDER:
                raise PersonalSourceDenied("folder_ancestry_unverified")
            parts.insert(0, component(current.get("name")))
            if parent_id == root_id:
                break
        else:
            raise PersonalSourceDenied("selection_too_deep")
    name = "/".join(parts)
    relative_parts(name)
    return meta, name


def preview_selection(reader, selections, *, max_files=2000):
    if not isinstance(selections, list) or not selections or len(selections) > max_files:
        raise PersonalSourceDenied("selection_too_large")
    files, visited, paths = [], set(), set()
    stack = [(entry.get("drive_file_id"), None, 0) for entry in reversed(selections) if isinstance(entry, dict)]
    if len(stack) != len(selections):
        raise PersonalSourceDenied("invalid_drive_object")
    while stack:
        file_id, root_id, depth = stack.pop()
        if depth >= MAX_DEPTH or len(visited) >= max_files * 2 + 32:
            raise PersonalSourceDenied("selection_too_large")
        if not isinstance(file_id, str) or not file_id or len(file_id) > 200:
            raise PersonalSourceDenied("invalid_drive_object")
        if file_id in visited:
            raise PersonalSourceDenied("duplicate_file_name")
        visited.add(file_id)
        meta = reader.get_metadata(file_id)
        if meta.get("trashed"):
            raise PersonalSourceDenied("drive_object_unavailable")
        component(meta.get("name"))
        if meta.get("mimeType") == FOLDER:
            if meta.get("capabilities", {}).get("canListChildren") is not True:
                raise PersonalSourceDenied("folder_access_unverified")
            children = reader.list_children(file_id)
            for child in reversed(children):
                if not isinstance(child, dict) or file_id not in child.get("parents", []):
                    raise PersonalSourceDenied("folder_ancestry_unverified")
                stack.append((child.get("id"), root_id or file_id, depth + 1))
            continue
        meta, name = resolve_selected_file(reader, file_id, root_id)
        size = meta.get("size")
        if not str(size).isdigit():
            raise PersonalSourceDenied("unsupported_drive_object")
        if name.casefold() in paths:
            raise PersonalSourceDenied("duplicate_file_name")
        paths.add(name.casefold())
        files.append({"id": file_id, "name": name, "sizeBytes": int(size), "root_folder_id": root_id})
        if len(files) > max_files:
            raise PersonalSourceDenied("selection_too_large")
    if not files:
        raise PersonalSourceDenied("folder_no_readable_files")
    return {"files": files, "scope_limited": True}
