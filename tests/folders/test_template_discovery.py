import pytest

from plugin.folders.discovery import discover_template_tree
from plugin.storage.base import AmbiguousOutcome, StorageObject
from plugin.storage.fake import FakeStorage


def test_template_discovery_preserves_nested_folder_names_and_ignores_media():
    storage = FakeStorage(root_id="allowed-root", drive_id="shared-drive")
    storage.objects.update({
        "template-root": StorageObject("template-root", "allowed-root", 0, None, "shared-drive", "folder", name="Comp Template"),
        "sequence": StorageObject("sequence", "template-root", 0, None, "shared-drive", "folder", name="{sequence}"),
        "shot": StorageObject("shot", "sequence", 0, None, "shared-drive", "folder", name="{shot}"),
        "task": StorageObject("task", "shot", 0, None, "shared-drive", "folder", name="{task}"),
        "media": StorageObject("media", "shot", 10, None, "shared-drive", "video/mp4", name="reference.mov"),
    })
    tree = discover_template_tree(storage, allowed_root_id="allowed-root", template_folder_id="template-root")

    assert tree.name == "Comp Template"
    assert [(node.name, node.provider_folder_id) for node in tree.children] == [("{sequence}", "sequence")]
    shot = tree.children[0].children[0]
    assert (shot.name, [node.name for node in shot.children]) == ("{shot}", ["{task}"])


def test_discovery_fails_closed_on_incomplete_child_listing():
    storage = FakeStorage(root_id="allowed-root")
    storage.incomplete_listings = True
    storage.objects["template-root"] = StorageObject("template-root", "allowed-root", 0, None, None, "folder", name="Template")

    with pytest.raises(AmbiguousOutcome):
        discover_template_tree(storage, allowed_root_id="allowed-root", template_folder_id="template-root")


def test_discovery_rejects_template_folder_outside_authorized_root():
    storage = FakeStorage(root_id="allowed-root")
    storage.objects["outside"] = StorageObject("outside", "elsewhere", 0, None, None, "folder", name="Outside")

    with pytest.raises(PermissionError):
        discover_template_tree(storage, allowed_root_id="allowed-root", template_folder_id="outside")


def test_duplicate_display_names_remain_distinct_by_provider_id():
    storage = FakeStorage(root_id="allowed-root")
    storage.objects.update({
        "template-root": StorageObject("template-root", "allowed-root", 0, None, None, "folder", name="Template"),
        "left": StorageObject("left", "template-root", 0, None, None, "folder", name="Work"),
        "right": StorageObject("right", "template-root", 0, None, None, "folder", name="Work"),
    })

    tree = discover_template_tree(storage, allowed_root_id="allowed-root", template_folder_id="template-root")

    assert [(node.name, node.provider_folder_id) for node in tree.children] == [("Work", "left"), ("Work", "right")]
