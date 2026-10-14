"""Settings of the charger monitor.

Read from a ``KEY=VALUE`` file (``.env`` beside this module by default,
template ``.env.example``), with the process environment taking precedence.
Nothing is read from any other project: the folder runs on its own. Standard
library only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MONITOR_DIR = Path(__file__).resolve().parent

# The settings file used when ``--config`` is not given.
DEFAULT_SETTINGS_FILE = MONITOR_DIR / ".env"

# Every key the monitor reads, with its default; ``None`` = required.
SETTING_DEFAULTS: dict[str, str | None] = {
    "MONITOR_API_URL": "http://127.0.0.1:8000",
    "MONITOR_ADMIN_PHONE": None,
    "MONITOR_ADMIN_PASSWORD": None,
    "MONITOR_UI_HOST": "127.0.0.1",
    "MONITOR_UI_PORT": "8095",
}


class SettingsError(Exception):
    """A setting is missing or has an invalid value."""


@dataclass(frozen=True)
class MonitorSettings:
    """Where the backend API is, who to log in as, and where the page listens.

    Attributes:
        api_url: Base URL of the backend API, without a trailing slash.
        admin_phone: Phone number of an internal staff account (HEAD_ADMIN,
            CO_ADMIN...) allowed to read the OCPP message log.
        admin_password: That account's password.
        ui_host: Address the web page listens on.
        ui_port: Port the web page listens on.
    """

    api_url: str
    admin_phone: str
    admin_password: str
    ui_host: str
    ui_port: int


def read_settings_file(path: Path) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines, skipping blanks and ``#`` comments.

    Args:
        path: The file; a missing file reads as empty.

    Returns:
        The values, unquoted and stripped.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_settings(path: Path = DEFAULT_SETTINGS_FILE) -> MonitorSettings:
    """Load the settings: environment first, then the file, then the defaults.

    Args:
        path: The settings file.

    Returns:
        The validated settings.

    Raises:
        SettingsError: A required key is empty, the API URL is not
            ``http(s)://``, or the UI port is not a port number.
    """
    file_values = read_settings_file(path)
    values: dict[str, str] = {}
    for key, default in SETTING_DEFAULTS.items():
        value = os.environ.get(key, file_values.get(key, default or ""))
        if default is None and not value:
            raise SettingsError(f"{key} is not set (in {path} or the environment)")
        values[key] = value

    api_url = values["MONITOR_API_URL"].rstrip("/")
    parts = urlsplit(api_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SettingsError(
            f"MONITOR_API_URL must look like http://<host>:<port>, got {api_url!r}"
        )
    try:
        ui_port = int(values["MONITOR_UI_PORT"])
    except ValueError as error:
        raise SettingsError("MONITOR_UI_PORT must be a number") from error
    if not 1 <= ui_port <= 65535:
        raise SettingsError("MONITOR_UI_PORT must be between 1 and 65535")
    return MonitorSettings(
        api_url=api_url,
        admin_phone=values["MONITOR_ADMIN_PHONE"],
        admin_password=values["MONITOR_ADMIN_PASSWORD"],
        ui_host=values["MONITOR_UI_HOST"],
        ui_port=ui_port,
    )
