"""VFX Studio Files Zou plugin."""


def __getattr__(name):
    if name == "routes":
        # Zou's create_app() assigns its Flask instance before loading plugin
        # routes. Populate plugin-owned service-auth settings at that startup
        # boundary rather than adding an environment fallback to request auth.
        from zou.app import app as zou_app

        from .integrations.service_config import configure_management_service_auth

        configure_management_service_auth(zou_app)
        from .resources import routes

        globals()["routes"] = routes
        return routes
    raise AttributeError(name)
