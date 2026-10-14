"""What the charger knows about itself, shared with its web page.

The charger runs in an asyncio loop; the web page is served from a separate
thread. Both go through ``SimulatorState``, whose methods take one lock, so
the page always reads a consistent snapshot. The state lives in memory only:
it starts empty at every power-on, like a charger's screen.

It holds the charger's own state (identity, connection), not the OCPP
messages it exchanges: watching the traffic belongs to a monitoring page on
the server side, not to the device.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from settings import ChargerSettings


def utc_now() -> datetime:
    """Return the charger's clock: the current time in UTC."""
    return datetime.now(timezone.utc)


def log(text: str) -> None:
    """Print one console line prefixed with the charger's UTC clock.

    Args:
        text: The line to print.
    """
    now = utc_now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"{now}Z  {text}", flush=True)


def iso_now() -> str:
    """Return the current UTC time as an OCPP timestamp (milliseconds, ``Z``)."""
    return utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso(moment: datetime | None) -> str | None:
    """Format a time for the page (ISO 8601, milliseconds, ``Z``).

    Args:
        moment: The time, or ``None``.

    Returns:
        The text, or ``None`` when there is no time.
    """
    if moment is None:
        return None
    return (
        moment.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class SimulatorState:
    """The charger's live state, safe to read from the web thread.

    Attributes:
        settings: The charger's settings (shown on the page).
        powered_on_at: When the simulator started.
    """

    def __init__(self, settings: ChargerSettings) -> None:
        """Start powered on and connecting.

        Args:
            settings: The charger's settings.
        """
        self.settings = settings
        self.powered_on_at = utc_now()
        self._lock = threading.Lock()
        self._connection = "connecting"
        self._connection_detail = ""
        self._connected_since: datetime | None = None
        self._connections = 0
        self._next_retry_at: datetime | None = None

    def connecting(self) -> None:
        """Record that the charger is opening a connection."""
        with self._lock:
            self._connection = "connecting"
            self._connection_detail = self.settings.endpoint
            self._next_retry_at = None

    def connected(self) -> None:
        """Record an open connection."""
        with self._lock:
            self._connection = "connected"
            self._connection_detail = ""
            self._connected_since = utc_now()
            self._connections += 1

    def disconnected(self, reason: str, retry_in_s: float) -> None:
        """Record a lost or refused connection and when the next try is.

        Args:
            reason: Why the connection ended.
            retry_in_s: Seconds until the next connection attempt.
        """
        with self._lock:
            self._connection = "disconnected"
            self._connection_detail = reason
            self._connected_since = None
            self._next_retry_at = datetime.fromtimestamp(
                utc_now().timestamp() + retry_in_s, timezone.utc
            )

    def is_connected(self) -> bool:
        """Tell whether the charger currently has an open connection.

        Returns:
            ``True`` while connected.
        """
        with self._lock:
            return self._connection == "connected"

    def snapshot(self) -> dict[str, Any]:
        """Return everything the page shows, as JSON-ready values.

        Returns:
            The charger's identity and its connection.
        """
        with self._lock:
            settings = self.settings
            return {
                "now": iso(utc_now()),
                "charger": {
                    "id": settings.charger_id,
                    "server_url": settings.server_url,
                    "endpoint": settings.endpoint,
                    "vendor": settings.vendor,
                    "model": settings.model,
                    "serial": settings.serial,
                    "firmware": settings.firmware,
                    "powered_on_at": iso(self.powered_on_at),
                },
                "connection": {
                    "status": self._connection,
                    "detail": self._connection_detail,
                    "connected_since": iso(self._connected_since),
                    "connections": self._connections,
                    "next_retry_at": iso(self._next_retry_at),
                },
            }
