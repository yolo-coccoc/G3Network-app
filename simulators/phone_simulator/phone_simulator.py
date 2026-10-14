"""Phone simulator: the driver's mobile app, on a page.

An independent tool: standard library only, its own ``.env``, nothing read
from any other project. It plays one driver's phone against the backend's
HTTP API, the way the mobile app will: log in, scan a charger's QR code to
start a charge, watch it, stop it, read the receipt, and top up the wallet
(paid by a simulated bank). The page (``http://127.0.0.1:8093/`` by default)
is shaped like a phone screen.

Needs the backend API reachable at ``PHONE_API_URL`` (on a laptop, through an
SSH tunnel to the server's port 8000).

Run (from this folder)::

    python3 phone_simulator.py

``--config <file>`` reads another settings file (one per phone).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from phone import PhoneApp
from settings import DEFAULT_SETTINGS_FILE, SettingsError, load_settings
from web import WebPage


def main(arguments: Sequence[str] | None = None) -> int:
    """Load the settings and serve the phone's page until Ctrl+C.

    Args:
        arguments: Command-line arguments; ``None`` = ``sys.argv``.

    Returns:
        ``0`` after Ctrl+C, ``2`` when the settings are invalid or the page's
        port is taken.
    """
    parser = argparse.ArgumentParser(description="Phone (driver app) simulator.")
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

    try:
        page = WebPage(PhoneApp(settings))
    except OSError as error:
        print(
            f"web page: cannot listen on {settings.ui_host}:{settings.ui_port} "
            f"({error}); set PHONE_UI_PORT to a free port",
            file=sys.stderr,
        )
        return 2
    page.start()
    bank = "on" if settings.bank_enabled else "off (PHONE_BANK_ADMIN_* not set)"
    print(
        f"phone on {page.url} (backend API {settings.api_url}; simulated bank {bank})",
        flush=True,
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("phone switched off", flush=True)
    finally:
        page.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
