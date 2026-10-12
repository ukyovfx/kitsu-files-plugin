"""Safe, network-free import of a Google Drive folder URL."""

import re
from urllib.parse import urlsplit


_FOLDER_PATH = re.compile(r"^/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]{5,200})/?$")


def parse_google_drive_folder_url(value: str) -> str:
    """Return the folder ID from a canonical Drive URL; never fetch or retain URL tokens."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("A Google Drive folder URL is required")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid Google Drive folder URL") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "drive.google.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
    ):
        raise ValueError("Only standard HTTPS Google Drive folder URLs are accepted")
    match = _FOLDER_PATH.fullmatch(parsed.path)
    if not match:
        raise ValueError("URL does not identify a Google Drive folder")
    return match.group(1)
