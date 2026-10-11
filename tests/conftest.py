import os
import importlib
import sys

import pytest


if plugin_folder := os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER"):
    if plugin_folder not in sys.path:
        sys.path.insert(0, plugin_folder)
    # Zou imports an installed plugin by its manifest ID (`vfx_files`). The
    # test tree's service modules use the local `plugin` package name; alias
    # the model module so SQLAlchemy sees one class/table registry in-host.
    try:
        sys.modules["plugin.models"] = importlib.import_module("vfx_files.models")
    except ImportError:
        pass


@pytest.fixture(scope="session")
def zou_app():
    pytest.importorskip("zou")
    from zou.app import config, create_app

    plugin_folder = os.environ.get("VFX_FILES_TEST_PLUGIN_FOLDER")
    if plugin_folder:
        config.PLUGIN_FOLDER = plugin_folder

    app = create_app()
    context = app.app_context()
    context.push()
    yield app
    context.pop()


@pytest.fixture(scope="session")
def zou_db_factory(zou_app):
    from sqlalchemy.orm import sessionmaker
    from zou.app import db

    return sessionmaker(bind=db.engine, expire_on_commit=False)
