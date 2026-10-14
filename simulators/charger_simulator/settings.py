"""Settings of the charger simulator.

Read from a ``KEY=VALUE`` file (``.env`` beside this module by default,
template ``.env.example``), with the process environment taking precedence.
Nothing is read from any other project: the folder runs on its own. Standard
library only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

SIMULATOR_DIR = Path(__file__).resolve().parent

# The settings file used when ``--config`` is not given.
DEFAULT_SETTINGS_FILE = SIMULATOR_DIR / ".env"

# Every key the simulator reads, with its default; ``None`` = required.
SETTING_DEFAULTS: dict[str, str | None] = {
    "CHARGER_SERVER_URL": None,
    "CHARGER_ID": None,
    "CHARGER_VENDOR": "G3 Simulator",
    "CHARGER_MODEL": "CHARGER-SIM-16",
    "CHARGER_SERIAL": "",
    "CHARGER_FIRMWARE": "charger-sim-0.1",
    "CHARGER_HEARTBEAT_INTERVAL": "",
    "CHARGER_UI_HOST": "127.0.0.1",
    "CHARGER_UI_PORT": "8090",
}


class SettingsError(Exception):
    """A setting is missing or has an invalid value."""


@dataclass(frozen=True)
class ChargerSettings:
    """What the charger is told on its configuration screen, plus its web page.

    Attributes:
        server_url: Central system URL the charger ID is appended to
            (``ws://<ip>:<port>/ocpp/``).
        charger_id: The charger's OCPP identity (chargePointId).
        vendor: ``chargePointVendor`` sent at boot.
        model: ``chargePointModel`` sent at boot.
        serial: ``chargePointSerialNumber`` sent at boot; ``None`` = not sent.
        firmware: ``firmwareVersion`` sent at boot.
        heartbeat_interval: Seconds between heartbeats overriding the
            server's interval; ``None`` = use the server's.
        ui_host: Address the web page listens on.
        ui_port: Port the web page listens on.
    """

    server_url: str
    charger_id: str
    vendor: str
    model: str
    serial: str | None
    firmware: str
    heartbeat_interval: float | None
    ui_host: str
    ui_port: int

    @property
    def endpoint(self) -> str:
        """The WebSocket URL the charger connects to: server URL + charger ID."""
        separator = "" if self.server_url.endswith("/") else "/"
        return f"{self.server_url}{separator}{quote(self.charger_id, safe='')}"


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


def load_settings(path: Path = DEFAULT_SETTINGS_FILE) -> ChargerSettings:
    """Load the settings: environment first, then the file, then the defaults.

    Args:
        path: The settings file.

    Returns:
        The validated settings.

    Raises:
        SettingsError: A required key is empty, the URL is not ``ws://`` or
            ``wss://``, the heartbeat interval is not a positive number, or
            the UI port is not a port number.
    """
    file_values = read_settings_file(path)
    values: dict[str, str] = {}
    for key, default in SETTING_DEFAULTS.items():
        value = os.environ.get(key, file_values.get(key, default or ""))
        if default is None and not value:
            raise SettingsError(f"{key} is not set (in {path} or the environment)")
        values[key] = value

    server_url = values["CHARGER_SERVER_URL"]
    parts = urlsplit(server_url)
    if parts.scheme not in ("ws", "wss") or not parts.netloc:
        raise SettingsError(
            "CHARGER_SERVER_URL must look like ws://<ip>:<port>/ocpp/, "
            f"got {server_url!r}"
        )
    return ChargerSettings(
        server_url=server_url,
        charger_id=values["CHARGER_ID"],
        vendor=values["CHARGER_VENDOR"],
        model=values["CHARGER_MODEL"],
        serial=values["CHARGER_SERIAL"] or None,
        firmware=values["CHARGER_FIRMWARE"],
        heartbeat_interval=_parse_interval(values["CHARGER_HEARTBEAT_INTERVAL"]),
        ui_host=values["CHARGER_UI_HOST"],
        ui_port=_parse_port(values["CHARGER_UI_PORT"]),
    )


def _parse_interval(text: str) -> float | None:
    """Parse ``CHARGER_HEARTBEAT_INTERVAL``.

    Args:
        text: The raw value; empty = not set.

    Returns:
        The interval in seconds, or ``None`` when not set.

    Raises:
        SettingsError: Not a number above 0.
    """
    if not text:
        return None
    try:
        interval = float(text)
    except ValueError as error:
        raise SettingsError(
            f"CHARGER_HEARTBEAT_INTERVAL must be a number, got {text!r}"
        ) from error
    if interval <= 0:
        raise SettingsError("CHARGER_HEARTBEAT_INTERVAL must be above 0")
    return interval


def _parse_port(text: str) -> int:
    """Parse ``CHARGER_UI_PORT``.

    Args:
        text: The raw value.

    Returns:
        The port number.

    Raises:
        SettingsError: Not an integer between 1 and 65535.
    """
    try:
        port = int(text)
    except ValueError as error:
        raise SettingsError(
            f"CHARGER_UI_PORT must be a number, got {text!r}"
        ) from error
    if not 1 <= port <= 65535:
        raise SettingsError("CHARGER_UI_PORT must be between 1 and 65535")
    return port
