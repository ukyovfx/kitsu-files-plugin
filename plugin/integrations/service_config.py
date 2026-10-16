"""Zou application settings for the KitsuSync service integration."""

from __future__ import annotations

import os
from collections.abc import Mapping

MANAGEMENT_AUTH_SETTING_KEYS = (
    "VFX_FILES_KITSUSYNC_ISSUER",
    "VFX_FILES_KITSUSYNC_AUDIENCE",
    "VFX_FILES_KITSUSYNC_SUBJECT",
    "VFX_FILES_KITSUSYNC_CLIENT_ID",
)


def configure_management_service_auth(app, environ: Mapping[str, str] | None = None) -> None:
    """Copy deployment environment settings into Zou's Flask app at startup.

    Zou loads its own config module before registering plugins. These VFX
    integration settings are intentionally plugin-owned, so map only missing
    keys here; explicit host configuration keeps precedence. Invalid or
    absent values remain unset and the request verifier fails closed.
    """
    source = os.environ if environ is None else environ
    for key in MANAGEMENT_AUTH_SETTING_KEYS:
        if key in app.config:
            continue
        value = source.get(key)
        if isinstance(value, str) and value and value == value.strip():
            app.config[key] = value
