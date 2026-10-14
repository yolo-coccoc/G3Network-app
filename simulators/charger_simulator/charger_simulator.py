"""OCPP 1.6J charger simulator: start it, and a charger is switched on.

An independent stand-in for a real charger: it needs only ``ocpp`` and
``websockets`` (``requirements.txt``) and its own ``.env``; it reads nothing
from any other project. It runs until stopped (Ctrl+C), as a powered-on
charger does:

1. **Power on:** open the web page, connect to ``CHARGER_SERVER_URL`` +
   ``CHARGER_ID`` with the ``ocpp1.6`` subprotocol, and send
   ``BootNotification`` until it is accepted (OCPP 1.6 section 4.2).
2. **Alive:** a ``Heartbeat`` every interval (the server's, or
   ``CHARGER_HEARTBEAT_INTERVAL``).
3. **Connection lost** (closed, network down, no answer within 30 s, or the
   handshake refused): reconnect after 5 s, doubling up to 60 s. A reconnect
   is not a reboot: heartbeats resume without a new ``BootNotification``,
   unless none was accepted since power-on.

The web page (``CHARGER_UI_HOST:CHARGER_UI_PORT``, ``http://127.0.0.1:8090/``
by default) shows the charger and its connection, as a charger's screen
would; the OCPP messages go to the console log only.

Run (from this folder, with its venv)::

    .venv/bin/python charger_simulator.py            # Windows: .venv\\Scripts\\python

``--config <file>`` reads another settings file (one per charger).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from charger import SimulatedCharger, log
from settings import (
    DEFAULT_SETTINGS_FILE,
    ChargerSettings,
    SettingsError,
    load_settings,
)
from state import SimulatorState
from web import WebPage
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Subprotocol

OCPP16_SUBPROTOCOL = Subprotocol("ocpp1.6")

# Wait before the first reconnect; doubled after each failed try up to the
# maximum, and reset once a connection opens.
RECONNECT_FIRST_DELAY_SECONDS = 5.0
RECONNECT_MAX_DELAY_SECONDS = 60.0


class PowerState:
    """What the charger remembers across connections while powered on.

    Attributes:
        heartbeat_interval: Seconds between heartbeats, set by the first
            accepted boot; ``None`` until then, so the next connection boots.
    """

    def __init__(self) -> None:
        """Start powered on but not yet booted."""
        self.heartbeat_interval: float | None = None


async def keep_alive(charger: SimulatedCharger, power: PowerState) -> None:
    """Boot if needed, then send heartbeats until the connection fails.

    Args:
        charger: The charger on the current connection.
        power: Kept across connections (the interval after the boot).

    Raises:
        TimeoutError: A boot or heartbeat got no answer in time.
        OCPPError: The server answered the boot with a CALLERROR.
        ConnectionClosed: The connection closed while sending.
    """
    interval = power.heartbeat_interval
    if interval is None:
        interval = await charger.boot_until_accepted()
        power.heartbeat_interval = interval
    else:
        log(f"   reconnected: no new boot, heartbeats resume every {interval:g} s")
    while True:
        await asyncio.sleep(interval)
        await charger.heartbeat()


async def run_connection(
    settings: ChargerSettings, power: PowerState, state: SimulatorState
) -> None:
    """Open one connection and keep it alive until it fails.

    Listening and heartbeating run side by side; whichever stops first ends
    the connection (a closed socket stops the listener at once, without
    waiting for the next heartbeat's timeout).

    Args:
        settings: Where to connect, and the boot fields.
        power: Kept across connections.
        state: The live state.

    Raises:
        InvalidStatus: The server refused the handshake (404: unknown ID).
        OSError: The server cannot be reached.
        ConnectionClosed: The connection closed.
        TimeoutError: The server stopped answering.
        OCPPError: The server answered the boot with a CALLERROR.
    """
    log(f"connecting to {settings.endpoint}")
    state.connecting()
    # ping_interval=None: the charger sends no WebSocket pings of its own
    # (only OCPP frames count as signs of life for a central system); the
    # server's pings are still answered by the library.
    async with connect(
        settings.endpoint, subprotocols=[OCPP16_SUBPROTOCOL], ping_interval=None
    ) as connection:
        log(f"connected (subprotocol {connection.subprotocol})")
        state.connected()
        charger = SimulatedCharger(settings, connection)
        tasks = {
            asyncio.create_task(charger.start(), name="listen"),
            asyncio.create_task(keep_alive(charger, power), name="keep-alive"),
        }
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, ConnectionClosed):
                    await task
        for task in done:
            task.result()


def describe_failure(error: BaseException) -> str:
    """Say in one line why a connection ended.

    Args:
        error: What ended the connection.

    Returns:
        A short human-readable reason.
    """
    if isinstance(error, InvalidStatus):
        body = error.response.body.decode(errors="replace").strip()
        reason = f"handshake refused: HTTP {error.response.status_code} {body}"
        if error.response.status_code == 404:
            reason += " (is CHARGER_ID registered on the server?)"
        return reason
    if isinstance(error, ConnectionClosed):
        return f"connection closed: {error}"
    if isinstance(error, TimeoutError):
        return "the server stopped answering"
    if isinstance(error, OSError):
        return f"cannot reach the server: {error}"
    return f"{type(error).__name__}: {error}"


async def live(settings: ChargerSettings, state: SimulatorState) -> None:
    """Run the charger until cancelled, reconnecting whenever it drops.

    Args:
        settings: The charger's settings.
        state: The live state.
    """
    power = PowerState()
    delay = RECONNECT_FIRST_DELAY_SECONDS
    while True:
        try:
            await run_connection(settings, power, state)
            reason = "connection ended"
        except (InvalidStatus, OSError, ConnectionClosed, TimeoutError) as error:
            reason = describe_failure(error)
        except Exception as error:  # noqa: BLE001 - a living charger never exits
            reason = f"unexpected {type(error).__name__}: {error}"
        if state.is_connected():
            delay = RECONNECT_FIRST_DELAY_SECONDS
        state.disconnected(reason, delay)
        log(f"!! {reason}; reconnecting in {delay:g} s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)


def main(arguments: Sequence[str] | None = None) -> int:
    """Load the settings, open the web page and run the charger until Ctrl+C.

    Args:
        arguments: Command-line arguments; ``None`` = ``sys.argv``.

    Returns:
        ``0`` after Ctrl+C, ``2`` when the settings are invalid or the web
        page's port is taken.
    """
    parser = argparse.ArgumentParser(description="OCPP 1.6J charger simulator.")
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

    state = SimulatorState(settings)
    try:
        page = WebPage(state)
    except OSError as error:
        print(
            f"web page: cannot listen on {settings.ui_host}:{settings.ui_port} "
            f"({error}); set CHARGER_UI_PORT to a free port",
            file=sys.stderr,
        )
        return 2
    page.start()
    log(f"charger {settings.charger_id} powered on - open {page.url}")
    try:
        asyncio.run(live(settings, state))
    except KeyboardInterrupt:
        log(f"charger {settings.charger_id} powered off")
    finally:
        page.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
