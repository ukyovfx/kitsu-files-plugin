"""Fail readiness when the Kitsu API or authenticated Files route is missing."""

from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def validate_plugin_auth_status(status: int) -> bool:
    """The installed health endpoint must exist and reject anonymous requests."""
    return status == 401


def _status(url: str) -> int:
    try:
        with urlopen(url, timeout=3) as response:
            return response.status
    except HTTPError as error:
        return error.code
    except URLError:
        return 0


def main() -> int:
    if _status("http://127.0.0.1/") != 200:
        return 1
    if not validate_plugin_auth_status(
        _status("http://127.0.0.1:5000/plugins/vfx_files/health")
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
