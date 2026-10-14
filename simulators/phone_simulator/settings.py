"""Settings of the phone simulator.

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

PHONE_DIR = Path(__file__).resolve().parent

# The settings file used when ``--config`` is not given.
DEFAULT_SETTINGS_FILE = PHONE_DIR / ".env"

# Every key the simulator reads, with its default (all optional).
SETTING_DEFAULTS: dict[str, str] = {
    "PHONE_API_URL": "http://127.0.0.1:8000",
    "PHONE_UI_HOST": "127.0.0.1",
    "PHONE_UI_PORT": "8093",
    "PHONE_LOGIN_PHONE": "",
    "PHONE_LOGIN_PASSWORD": "",
    "PHONE_BANK_ADMIN_PHONE": "",
    "PHONE_BANK_ADMIN_PASSWORD": "",
}


class SettingsError(Exception):
    """A setting has an invalid value."""


@dataclass(frozen=True)
class PhoneSettings:
    """Where the backend API is, the login to prefill, and the simulated bank.

    Attributes:
        api_url: Base URL of the backend API, without a trailing slash.
        ui_host: Address the phone's page listens on.
        ui_port: Port the phone's page listens on.
        login_phone: Phone number prefilled on the login screen.
        login_password: Password prefilled on the login screen.
        bank_admin_phone: An internal admin (HEAD_ADMIN / CO_ADMIN) the
            simulated bank logs in as to confirm a transfer; empty = no bank
            button.
        bank_admin_password: That admin's password.
    """

    api_url: str
    ui_host: str
    ui_port: int
    login_phone: str
    login_password: str
    bank_admin_phone: str
    bank_admin_password: str

    @property
    def bank_enabled(self) -> bool:
        """Whether the simulated bank can confirm top-ups."""
        return bool(self.bank_admin_phone and self.bank_admin_password)


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


def load_settings(path: Path = DEFAULT_SETTINGS_FILE) -> PhoneSettings:
    """Load the settings: environment first, then the file, then the defaults.

    Args:
        path: The settings file.

    Returns:
        The validated settings.

    Raises:
        SettingsError: The API URL is not ``http(s)://``, or the UI port is
            not a port number.
    """
    file_values = read_settings_file(path)
    values = {
        key: os.environ.get(key, file_values.get(key, default))
        for key, default in SETTING_DEFAULTS.items()
    }
    api_url = values["PHONE_API_URL"].rstrip("/")
    parts = urlsplit(api_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SettingsError(
            f"PHONE_API_URL must look like http://<host>:<port>, got {api_url!r}"
        )
    try:
        ui_port = int(values["PHONE_UI_PORT"])
    except ValueError as error:
        raise SettingsError("PHONE_UI_PORT must be a number") from error
    if not 1 <= ui_port <= 65535:
        raise SettingsError("PHONE_UI_PORT must be between 1 and 65535")
    return PhoneSettings(
        api_url=api_url,
        ui_host=values["PHONE_UI_HOST"],
        ui_port=ui_port,
        login_phone=values["PHONE_LOGIN_PHONE"],
        login_password=values["PHONE_LOGIN_PASSWORD"],
        bank_admin_phone=values["PHONE_BANK_ADMIN_PHONE"],
        bank_admin_password=values["PHONE_BANK_ADMIN_PASSWORD"],
    )
