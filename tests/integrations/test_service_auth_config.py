import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _configure_management_service_auth(app, environ):
    from plugin.integrations.service_config import configure_management_service_auth

    configure_management_service_auth(app, environ)


SETTINGS = {
    "VFX_FILES_KITSUSYNC_ISSUER": "https://kitsusync.test",
    "VFX_FILES_KITSUSYNC_AUDIENCE": "https://plugin.test/api/plugins/vfx_files/integrations/v1",
    "VFX_FILES_KITSUSYNC_SUBJECT": "synthetic-kitsusync-service",
    "VFX_FILES_KITSUSYNC_CLIENT_ID": "ks239-1015z",
}


def test_management_service_settings_are_copied_from_environment():
    app = SimpleNamespace(config={})

    _configure_management_service_auth(app, SETTINGS)

    assert {key: app.config[key] for key in SETTINGS} == SETTINGS


def test_mapping_is_idempotent_and_does_not_replace_explicit_settings():
    explicit = "https://explicit-issuer.test"
    app = SimpleNamespace(config={"VFX_FILES_KITSUSYNC_ISSUER": explicit})

    _configure_management_service_auth(app, SETTINGS)
    _configure_management_service_auth(app, SETTINGS)

    assert app.config["VFX_FILES_KITSUSYNC_ISSUER"] == explicit
    assert {key for key in app.config if key in SETTINGS} == set(SETTINGS)


def test_plugin_route_registration_maps_settings_to_zou_application(monkeypatch):
    plugin = importlib.import_module("plugin")
    host_app = SimpleNamespace(config={})
    zou_package = ModuleType("zou")
    zou_package.__path__ = []
    zou_app_module = ModuleType("zou.app")
    zou_app_module.app = host_app
    zou_package.app = zou_app_module
    resources = ModuleType("plugin.resources")
    resources.routes = []
    monkeypatch.setitem(sys.modules, "zou", zou_package)
    monkeypatch.setitem(sys.modules, "zou.app", zou_app_module)
    monkeypatch.setitem(sys.modules, "plugin.resources", resources)
    for key, value in SETTINGS.items():
        monkeypatch.setenv(key, value)
    plugin.__dict__.pop("routes", None)

    try:
        assert plugin.routes == []
        assert {key: host_app.config[key] for key in SETTINGS} == SETTINGS
    finally:
        plugin.__dict__.pop("routes", None)


def test_explicit_application_settings_are_not_overridden():
    explicit = "https://explicit-issuer.test"
    app = SimpleNamespace(config={"VFX_FILES_KITSUSYNC_ISSUER": explicit})

    _configure_management_service_auth(app, SETTINGS)

    assert app.config["VFX_FILES_KITSUSYNC_ISSUER"] == explicit
    assert app.config["VFX_FILES_KITSUSYNC_AUDIENCE"] == SETTINGS[
        "VFX_FILES_KITSUSYNC_AUDIENCE"
    ]


@pytest.mark.parametrize("missing_value", [None, "", "   ", "https://issuer.test\n"])
def test_missing_or_malformed_environment_setting_stays_unconfigured(missing_value):
    environment = dict(SETTINGS)
    if missing_value is None:
        environment.pop("VFX_FILES_KITSUSYNC_CLIENT_ID")
    else:
        environment["VFX_FILES_KITSUSYNC_CLIENT_ID"] = missing_value
    app = SimpleNamespace(config={})

    _configure_management_service_auth(app, environment)

    assert "VFX_FILES_KITSUSYNC_CLIENT_ID" not in app.config
