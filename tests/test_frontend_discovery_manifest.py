from pathlib import Path
import tomlkit


def test_files_frontend_is_task_only_not_a_project_application():
    manifest_path = Path(__file__).parents[1] / "plugin" / "manifest.toml"
    manifest = tomlkit.parse(manifest_path.read_text(encoding="utf-8"))

    assert manifest["frontend_task_enabled"] is True
    assert manifest["frontend_project_enabled"] is False
    assert manifest["frontend_studio_enabled"] is False
