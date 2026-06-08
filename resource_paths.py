from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def is_http_url(value: object) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def resource_exists(value: object) -> bool:
    if is_http_url(value):
        return True
    if not value:
        return False
    try:
        return Path(str(value)).exists()
    except (OSError, ValueError):
        return False


def display_resource(value: object) -> object:
    return value


def is_local_file(value: object) -> bool:
    if is_http_url(value) or not value:
        return False
    try:
        return Path(str(value)).is_file()
    except (OSError, ValueError):
        return False


def safe_unlink(value: object) -> None:
    if not is_local_file(value):
        return
    try:
        Path(str(value)).unlink(missing_ok=True)
    except OSError:
        pass
