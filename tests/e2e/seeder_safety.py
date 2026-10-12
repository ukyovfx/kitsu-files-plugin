"""Pure safety helpers for the disposable installed-page test seeder."""

import base64
import binascii
import json
import os
import time
from datetime import timedelta
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit


def validated_disposable_root_url(environ=None):
    env = os.environ if environ is None else environ
    if env.get("VFX_FILES_DISPOSABLE_TEST_MODE") != "1":
        raise RuntimeError("Refusing to seed without disposable test mode")

    raw_url = env.get("VFX_FILES_E2E_ROOT_URL")
    if not raw_url:
        raise RuntimeError("A disposable test root URL is required")

    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Invalid disposable test root URL") from exc

    if (
        parsed.scheme not in {"https", "http"}
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError("Disposable test root URL must be a credential-free origin")
    if port is not None and not 1 <= port <= 65535:
        raise RuntimeError("Invalid disposable test root URL port")

    hostname = hostname.casefold().rstrip(".")
    try:
        is_loopback = ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = False
    is_reserved_test_name = hostname == "localhost" or hostname.endswith(
        (".localhost", ".test", ".example", ".invalid")
    )
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
        raise RuntimeError("HTTPS is required except for loopback test hosts")
    has_disposable_host_ack = (
        env.get("VFX_FILES_DISPOSABLE_HOST_ACK")
        == "DISPOSABLE_PINNED_KITSU_ZOU_ONLY"
    )
    if not (is_loopback or is_reserved_test_name) and not has_disposable_host_ack:
        raise RuntimeError("A non-reserved target requires disposable host acknowledgement")

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def token_has_short_expiry(token, now=None):
    current_time = time.time() if now is None else now
    parts = token.split(".")
    if len(parts) != 3:
        return False
    payload = parts[1].replace("-", "+").replace("_", "/")
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, UnicodeError, binascii.Error):
        return False
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    return (
        isinstance(expiry, (int, float))
        and not isinstance(expiry, bool)
        and current_time < expiry <= current_time + 901
    )


def new_synthetic_manager(person_type, suffix):
    return person_type(
        first_name="Synthetic",
        last_name=f"Manager{suffix}",
        email=f"manager-{suffix}@example.invalid",
        role="manager",
    )


def token_expiry_config():
    return timedelta(minutes=15)
