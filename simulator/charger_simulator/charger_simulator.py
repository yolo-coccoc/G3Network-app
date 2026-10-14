"""OCPP 1.6J charger simulator: a charger that stays connected and alive.

An independent stand-in for a real charger: it imports nothing from the
backend or the other simulator tools and needs only ``ocpp`` and
``websockets`` (``requirements.txt``). It runs until stopped (Ctrl+C), the
way a powered-on charger does:

1. **Power on:** connect to ``CHARGER_SERVER_URL`` + ``CHARGER_ID`` (from
   ``.env``, template ``.env.example``) with the ``ocpp1.6`` subprotocol and
   send ``BootNotification`` until it is accepted (OCPP 1.6 section 4.2).
2. **Alive:** a ``Heartbeat`` every interval (the server's, or
   ``CHARGER_HEARTBEAT_INTERVAL``), each one logged with its round trip and
   the charger clock's offset from the server's ``currentTime``.
3. **Connection lost** (closed by the server, network down, no answer to a
   heartbeat within 30 s, or the handshake refused): wait and reconnect,
   5 s doubling to at most 60 s between tries. A reconnect is not a reboot:
   the charger resumes its heartbeats without a new ``BootNotification``,
   unless it never got one accepted since it was started.

Requests from the server are logged; none is supported yet, so each is
answered ``NotImplemented``.

Run it with any Python 3.12 that has the two libraries, e.g. from the
repository's ``backend/``::

    uv run python ../simulator/charger_simulator/charger_simulator.py

``--config <file>`` reads another settings file (one per charger);
``--frames`` also prints every raw OCPP frame.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from charger import ChargerCounters, SimulatedCharger, say
from settings import (
    DEFAULT_SETTINGS_FILE,
    ChargerSettings,
    SettingsError,
    load_settings,
)
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Subprotocol

OCPP16_SUBPROTOCOL = Subprotocol("ocpp1.6")

# Wait before the first reconnect; doubled after each failed try up to the
# maximum, and reset once a connection opens.
RECONNECT_FIRST_DELAY_SECONDS = 5.0
RECONNECT_MAX_DELAY_SECONDS = 60.0


class ChargerState:
    """What the charger remembers across connections while powered on.

    Attributes:
        heartbeat_interval: Seconds between heartbeats, set by the first
            accepted boot; ``None`` until then, so the next connection boots.
    """

    def __init__(self) -> None:
        """Start powered on but not yet booted."""
        self.heartbeat_interval: float | None = None


async def keep_alive(charger: SimulatedCharger, state: ChargerState) -> None:
    """Boot if needed, then send heartbeats until the connection fails.

    Args:
        charger: The charger on the current connection.
        state: Kept across connections (the interval after the boot).

    Raises:
        TimeoutError: A boot or heartbeat got no answer in time.
        OCPPError: The server answered the boot with a CALLERROR.
        ConnectionClosed: The connection closed while sending.
    """
    interval = state.heartbeat_interval
    if interval is None:
        interval = await charger.boot_until_accepted()
        state.heartbeat_interval = interval
    else:
        say(f"   reconnected: no new boot, heartbeats resume every {interval:g} s")
    while True:
        await asyncio.sleep(interval)
        await charger.heartbeat()


async def run_connection(
    settings: ChargerSettings, state: ChargerState, counters: ChargerCounters
) -> None:
    """Open one connection and keep it alive until it fails.

    Listening and heartbeating run side by side; whichever stops first ends
    the connection (a closed socket stops the listener at once, without
    waiting for the next heartbeat's timeout).

    Args:
        settings: Where to connect, and the boot fields.
        state: Kept across connections.
        counters: The simulator's running totals.

    Raises:
        InvalidStatus: The server refused the handshake (404: unknown ID).
        OSError: The server cannot be reached.
        ConnectionClosed: The connection closed.
        TimeoutError: The server stopped answering.
        OCPPError: The server answered the boot with a CALLERROR.
    """
    say(f"connecting to {settings.endpoint}")
    # ping_interval=None: the charger sends no WebSocket pings of its own
    # (only OCPP frames count for the backend's online check); the server's
    # pings are still answered by the library.
    async with connect(
        settings.endpoint, subprotocols=[OCPP16_SUBPROTOCOL], ping_interval=None
    ) as connection:
        counters.connections += 1
        say(f"connected (subprotocol {connection.subprotocol})")
        charger = SimulatedCharger(settings, connection, counters)
        tasks = {
            asyncio.create_task(charger.start(), name="listen"),
            asyncio.create_task(keep_alive(charger, state), name="keep-alive"),
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
            reason += " (is CHARGER_ID provisioned on the backend?)"
        return reason
    if isinstance(error, ConnectionClosed):
        return f"connection closed: {error}"
    if isinstance(error, TimeoutError):
        return "the server stopped answering"
    return f"{type(error).__name__}: {error}"


async def live(settings: ChargerSettings, counters: ChargerCounters) -> None:
    """Run the charger until cancelled, reconnecting whenever it drops.

    Args:
        settings: The charger's settings.
        counters: The simulator's running totals.
    """
    state = ChargerState()
    delay = RECONNECT_FIRST_DELAY_SECONDS
    while True:
        connections_before = counters.connections
        try:
            await run_connection(settings, state, counters)
            reason = "connection ended"
        except (InvalidStatus, OSError, ConnectionClosed, TimeoutError) as error:
            reason = describe_failure(error)
        except Exception as error:  # noqa: BLE001 - a living charger never exits
            reason = f"unexpected {type(error).__name__}: {error}"
        if counters.connections > connections_before:
            delay = RECONNECT_FIRST_DELAY_SECONDS
        say(f"!! {reason}; reconnecting in {delay:g} s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)


def print_summary(counters: ChargerCounters) -> None:
    """Print the running totals when the simulator stops.

    Args:
        counters: The simulator's running totals.
    """
    uptime = datetime.now(timezone.utc) - counters.started_at
    print()
    print("Summary")
    print(f"  uptime             {str(uptime).split('.')[0]}")
    print(f"  connections        {counters.connections}")
    print(f"  boots answered     {counters.boots}")
    print(
        f"  heartbeats         {counters.heartbeats_answered} answered, "
        f"{counters.heartbeats_failed} CALLERROR"
    )
    if counters.last_clock_offset_s is not None:
        print(
            f"  clock offset       {counters.last_clock_offset_s:+.2f} s at the "
            "last heartbeat (server minus charger)"
        )
    if counters.server_requests:
        requests = ", ".join(f"{a} x{n}" for a, n in counters.server_requests.items())
        print(f"  server requests    {requests}")


def main(arguments: Sequence[str] | None = None) -> int:
    """Load the settings and run the charger until Ctrl+C.

    Args:
        arguments: Command-line arguments; ``None`` = ``sys.argv``.

    Returns:
        ``0`` after Ctrl+C, ``2`` when the settings are invalid.
    """
    parser = argparse.ArgumentParser(description="OCPP 1.6J charger simulator.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_SETTINGS_FILE,
        help=f"settings file (default: {DEFAULT_SETTINGS_FILE})",
    )
    parser.add_argument(
        "--frames", action="store_true", help="also print every raw OCPP frame"
    )
    args = parser.parse_args(arguments)
    logging.basicConfig(format="%(message)s", level=logging.WARNING)
    if args.frames:
        logging.getLogger("ocpp").setLevel(logging.INFO)

    try:
        settings = load_settings(args.config)
    except SettingsError as error:
        print(f"settings: {error}", file=sys.stderr)
        return 2

    say(f"charger {settings.charger_id} powered on")
    counters = ChargerCounters()
    try:
        asyncio.run(live(settings, counters))
    except KeyboardInterrupt:
        say(f"charger {settings.charger_id} powered off (Ctrl+C)")
    print_summary(counters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
