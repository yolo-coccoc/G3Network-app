"""OCPP 1.6J charger simulator: start it, and a charger is switched on.

An independent stand-in for a real charger: it needs only ``ocpp`` and
``websockets`` (``requirements.txt``) and its own ``.env``; it reads nothing
from any other project. It runs until stopped (Ctrl+C), as a powered-on
charger does:

1. **Power on:** open the web page, connect to ``CHARGER_SERVER_URL`` +
   ``CHARGER_ID`` with the ``ocpp1.6`` subprotocol, and send
   ``BootNotification`` until it is accepted (OCPP 1.6 section 4.2).
2. **Alive:** a ``Heartbeat`` every ``HeartbeatInterval`` seconds (set by
   the boot answer or ``CHARGER_HEARTBEAT_INTERVAL``, then by any
   ``ChangeConfiguration``); ``GetConfiguration`` and ``ChangeConfiguration``
   are answered (``configuration.py``).
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

from charger import SimulatedCharger
from configuration import ChargerConfiguration
from settings import (
    DEFAULT_SETTINGS_FILE,
    ChargerSettings,
    SettingsError,
    load_settings,
)
from state import SimulatorState, log
from station import ChargingStation
from web import WebPage
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Subprotocol

OCPP16_SUBPROTOCOL = Subprotocol("ocpp1.6")

# Wait before the first reconnect; doubled after each failed try up to the
# maximum, and reset once a connection opens.
RECONNECT_FIRST_DELAY_SECONDS = 5.0
RECONNECT_MAX_DELAY_SECONDS = 60.0

# How often the heartbeat loop re-reads HeartbeatInterval, so a change from
# the server applies at once instead of after the current wait.
HEARTBEAT_TICK_SECONDS = 0.5


class PowerState:
    """What the charger remembers across connections while powered on.

    Attributes:
        booted: Whether a BootNotification was accepted since power-on; until
            then, every new connection boots first.
        configuration: The charger's OCPP configuration keys.
        station: The charger's guns and charging logic.
    """

    def __init__(self) -> None:
        """Start powered on, not yet booted, with the default configuration."""
        self.booted = False
        self.configuration = ChargerConfiguration()
        self.station = ChargingStation(self.configuration)


async def keep_alive(charger: SimulatedCharger, power: PowerState) -> None:
    """Boot if needed, then send heartbeats until the connection fails.

    After the boot (or a reconnect) every connector's status is announced
    and the station's queued messages are sent alongside the heartbeats. The
    wait before each heartbeat re-reads ``HeartbeatInterval`` every
    ``HEARTBEAT_TICK_SECONDS``, so a ``ChangeConfiguration`` takes effect at
    once (a shorter interval can make the next heartbeat due immediately).

    Args:
        charger: The charger on the current connection.
        power: Kept across connections (boot done, configuration).

    Raises:
        TimeoutError: A boot or heartbeat got no answer in time.
        OCPPError: The server answered the boot with a CALLERROR.
        ConnectionClosed: The connection closed while sending.
    """
    configuration = power.configuration
    if not power.booted:
        await charger.boot_until_accepted()
        power.booted = True
    else:
        log(
            "   reconnected: no new boot, heartbeats resume every "
            f"{configuration.heartbeat_interval} s"
        )
    power.station.announce_statuses()
    sender = asyncio.create_task(power.station.serve(charger), name="station-sender")
    loop = asyncio.get_running_loop()
    last_sent = loop.time()
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_TICK_SECONDS)
            if sender.done():
                sender.result()  # a closed connection ends this connection too
            if loop.time() - last_sent >= configuration.heartbeat_interval:
                last_sent = loop.time()
                await charger.heartbeat()
    finally:
        sender.cancel()
        with suppress(asyncio.CancelledError, ConnectionClosed):
            await sender


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
        charger = SimulatedCharger(
            settings, connection, power.configuration, power.station
        )
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


async def live(
    settings: ChargerSettings, state: SimulatorState, power: PowerState
) -> None:
    """Run the charger until cancelled, reconnecting whenever it drops.

    The station's clock (charging, timers) runs for the whole power-on
    period, connected or not.

    Args:
        settings: The charger's settings.
        state: The live connection state.
        power: The charger's configuration and station.
    """
    clock = asyncio.create_task(power.station.run(), name="station-clock")
    try:
        await reconnect_forever(settings, state, power)
    finally:
        clock.cancel()
        with suppress(asyncio.CancelledError):
            await clock


async def reconnect_forever(
    settings: ChargerSettings, state: SimulatorState, power: PowerState
) -> None:
    """Keep a connection open, reconnecting with a growing delay.

    Args:
        settings: The charger's settings.
        state: The live connection state.
        power: Kept across connections.
    """
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
    power = PowerState()
    try:
        page = WebPage(state, power.station)
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
        asyncio.run(live(settings, state, power))
    except KeyboardInterrupt:
        log(f"charger {settings.charger_id} powered off")
    finally:
        page.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
