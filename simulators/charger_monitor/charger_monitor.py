"""Charger monitor: a web page that shows the OCPP traffic of the backend.

An independent tool: standard library only, its own ``.env``, nothing read
from any other project. It runs next to the backend (on the VM) and reads the
backend's OCPP message log through the HTTP API, logged in as an internal
staff account. The page (``http://127.0.0.1:8095/`` by default) has two tabs:

* **Monitoring**, per charger: its connection, the startup message
  (BootNotification and the backend's answer), the heartbeat interval check
  and the live message feed.
* **Configuration**, per charger: get its configuration (read-only) and send
  changes (one ``ChangeConfiguration`` per filled field), through the
  backend's command channel.
* **Chargers**: register, edit and delete chargers (location, guns, price).

Needs the backend API running (``make backend-dev``); the OCPP gateway
(``make charging-ocpp-dev``) is what fills the log.

Run (from this folder)::

    python3 charger_monitor.py

``--config <file>`` reads another settings file.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from api_client import ApiClient, ApiError
from chargers import ChargerAdmin
from configuration import ConfigurationService
from settings import DEFAULT_SETTINGS_FILE, SettingsError, load_settings
from traffic import TrafficReader
from web import WebPage


def main(arguments: Sequence[str] | None = None) -> int:
    """Load the settings, check the login, and serve the page until Ctrl+C.

    A failed first login is reported but does not stop the monitor: the page
    shows the error and every refresh tries again (the API may start later).

    Args:
        arguments: Command-line arguments; ``None`` = ``sys.argv``.

    Returns:
        ``0`` after Ctrl+C, ``2`` when the settings are invalid or the page's
        port is taken.
    """
    parser = argparse.ArgumentParser(description="Charger OCPP traffic monitor.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_SETTINGS_FILE,
        help=f"settings file (default: {DEFAULT_SETTINGS_FILE})",
    )
    args = parser.parse_args(arguments)
    try:
        settings = load_settings(args.config)
    except SettingsError as error:
        print(f"settings: {error}", file=sys.stderr)
        return 2

    client = ApiClient(settings)
    reader = TrafficReader(client)
    try:
        page = WebPage(
            settings, reader, ConfigurationService(client), ChargerAdmin(client)
        )
    except OSError as error:
        print(
            f"web page: cannot listen on {settings.ui_host}:{settings.ui_port} "
            f"({error}); set MONITOR_UI_PORT to a free port",
            file=sys.stderr,
        )
        return 2
    page.start()
    print(f"charger monitor on {page.url} (backend API {settings.api_url})", flush=True)
    try:
        chargers = reader.list_chargers()
        print(f"logged in; {len(chargers)} charger(s) visible", flush=True)
    except ApiError as error:
        print(f"backend API: {error.detail} (the page will keep trying)", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("charger monitor stopped", flush=True)
    finally:
        page.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
