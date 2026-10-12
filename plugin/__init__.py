"""VFX Studio Files Zou plugin."""


def __getattr__(name):
    if name == "routes":
        from .resources import routes

        globals()["routes"] = routes
        return routes
    raise AttributeError(name)
