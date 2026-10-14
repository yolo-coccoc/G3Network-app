"""What the charger knows about itself, shared with its web page.

The charger runs in an asyncio loop; the web page is served from a separate
thread. Both go through ``SimulatorState``, whose methods take one lock, so
the page always reads a consistent snapshot. The state lives in memory only:
it starts empty at every power-on, like a charger's screen.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from settings import ChargerSettings

# How many heartbeats and server requests the page can show.
HEARTBEAT_HISTORY = 100
SERVER_REQUEST_HISTORY = 20

# A heartbeat counts as on time when its gap from the previous one is within
# this many seconds of the interval, or this share of it, whichever is larger.
ON_TIME_TOLERANCE_SECONDS = 1.0
ON_TIME_TOLERANCE_SHARE = 0.1


def utc_now() -> datetime:
    """Return the charger's clock: the current time in UTC."""
    return datetime.now(timezone.utc)


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


def parse_frame(raw_frame: str) -> Any:
    """Decode an OCPP-J frame for display, keeping the text if it is not JSON.

    Args:
        raw_frame: The frame as sent or received.

    Returns:
        The decoded JSON value, or the raw text.
    """
    try:
        return json.loads(raw_frame)
    except ValueError:
        return raw_frame


@dataclass
class HeartbeatRecord:
    """One heartbeat as the charger saw it.

    Attributes:
        number: Sequence number since power-on.
        sent_at: When the charger sent it.
        interval_s: The interval in force when it was sent.
        gap_s: Seconds since the previous heartbeat; ``None`` for the first
            one of a connection.
        answered_at: When the answer arrived; ``None`` without an answer.
        round_trip_ms: Time to the answer, in milliseconds.
        server_time: The server's ``currentTime``, as sent.
        clock_offset_s: Server clock minus charger clock.
        outcome: ``pending``, ``answered``, ``callerror`` or ``no answer``.
        error: The CALLERROR text, if any.
    """

    number: int
    sent_at: datetime
    interval_s: float
    gap_s: float | None
    answered_at: datetime | None = None
    round_trip_ms: float | None = None
    server_time: str | None = None
    clock_offset_s: float | None = None
    outcome: str = "pending"
    error: str | None = None

    def check(self) -> str:
        """Judge the gap against the interval.

        Returns:
            ``first`` (nothing to compare with), ``on time``, ``late`` or
            ``early``.
        """
        if self.gap_s is None:
            return "first"
        tolerance = max(
            ON_TIME_TOLERANCE_SECONDS, self.interval_s * ON_TIME_TOLERANCE_SHARE
        )
        deviation = self.gap_s - self.interval_s
        if abs(deviation) <= tolerance:
            return "on time"
        return "late" if deviation > 0 else "early"


class SimulatorState:
    """The charger's live state, safe to read from the web thread.

    Attributes:
        settings: The charger's settings (shown on the page).
        powered_on_at: When the simulator started.
    """

    def __init__(self, settings: ChargerSettings) -> None:
        """Start powered on, disconnected, not booted.

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
        self._boot: dict[str, Any] | None = None
        self._boot_attempts = 0
        self._heartbeat_interval: float | None = None
        self._interval_source = ""
        self._heartbeats: deque[HeartbeatRecord] = deque(maxlen=HEARTBEAT_HISTORY)
        self._heartbeat_count = 0
        self._last_heartbeat_sent: datetime | None = None
        self._next_heartbeat_at: datetime | None = None
        self._server_requests: deque[dict[str, Any]] = deque(
            maxlen=SERVER_REQUEST_HISTORY
        )

    # --- connection -----------------------------------------------------

    def connecting(self) -> None:
        """Record that the charger is opening a connection."""
        with self._lock:
            self._connection = "connecting"
            self._connection_detail = self.settings.endpoint
            self._next_retry_at = None

    def connected(self) -> None:
        """Record an open connection; the heartbeat gap restarts from it."""
        with self._lock:
            self._connection = "connected"
            self._connection_detail = ""
            self._connected_since = utc_now()
            self._connections += 1
            self._last_heartbeat_sent = None

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
            self._next_heartbeat_at = None
            self._next_retry_at = datetime.fromtimestamp(
                utc_now().timestamp() + retry_in_s, timezone.utc
            )

    # --- startup (BootNotification) ---------------------------------------

    def boot_sent(self, unique_id: str, raw_frame: str) -> None:
        """Record the BootNotification the charger just sent.

        Args:
            unique_id: The frame's message ID, to match its answer.
            raw_frame: The frame as sent.
        """
        with self._lock:
            self._boot_attempts += 1
            self._boot = {
                "unique_id": unique_id,
                "sent_at": utc_now(),
                "request": parse_frame(raw_frame),
                "answered_at": None,
                "response": None,
                "status": None,
                "interval": None,
            }

    def boot_answer_received(self, unique_id: str, raw_frame: str) -> None:
        """Attach the server's answer to the BootNotification it belongs to.

        Args:
            unique_id: The answer's message ID.
            raw_frame: The answer as received (CALLRESULT or CALLERROR).
        """
        with self._lock:
            if self._boot is None or self._boot["unique_id"] != unique_id:
                return
            frame = parse_frame(raw_frame)
            self._boot["answered_at"] = utc_now()
            self._boot["response"] = frame
            if isinstance(frame, list) and frame and frame[0] == 3:
                payload = frame[2] if len(frame) > 2 else {}
                if isinstance(payload, dict):
                    self._boot["status"] = payload.get("status")
                    self._boot["interval"] = payload.get("interval")
            else:
                self._boot["status"] = "CALLERROR"

    def is_boot_answer(self, unique_id: str) -> bool:
        """Tell whether a message ID is the pending BootNotification's.

        Args:
            unique_id: An answer's message ID.

        Returns:
            ``True`` when it answers the last BootNotification sent.
        """
        with self._lock:
            return self._boot is not None and self._boot["unique_id"] == unique_id

    def heartbeat_interval_set(self, interval_s: float, source: str) -> None:
        """Record the heartbeat interval in force after an accepted boot.

        Args:
            interval_s: Seconds between heartbeats.
            source: ``server``, ``setting`` or ``fallback``.
        """
        with self._lock:
            self._heartbeat_interval = interval_s
            self._interval_source = source

    def server_request(self, action: str, answer: str) -> None:
        """Record a request the server sent and how the charger answered it.

        Args:
            action: The OCPP action.
            answer: What the charger answered (e.g. ``NotImplemented``).
        """
        with self._lock:
            self._server_requests.appendleft(
                {"at": iso(utc_now()), "action": action, "answer": answer}
            )

    # --- heartbeats -------------------------------------------------------

    def next_heartbeat_due(self, due_in_s: float) -> None:
        """Record when the next heartbeat goes out (for the page's countdown).

        Args:
            due_in_s: Seconds until it is sent.
        """
        with self._lock:
            self._next_heartbeat_at = datetime.fromtimestamp(
                utc_now().timestamp() + due_in_s, timezone.utc
            )

    def heartbeat_sent(self) -> HeartbeatRecord:
        """Record a heartbeat as sent and return its record to complete later.

        Returns:
            The new record (``pending`` until its answer is recorded).
        """
        with self._lock:
            now = utc_now()
            self._heartbeat_count += 1
            gap = (
                now.timestamp() - self._last_heartbeat_sent.timestamp()
                if self._last_heartbeat_sent
                else None
            )
            record = HeartbeatRecord(
                number=self._heartbeat_count,
                sent_at=now,
                interval_s=self._heartbeat_interval or 0.0,
                gap_s=gap,
            )
            self._last_heartbeat_sent = now
            self._next_heartbeat_at = None
            self._heartbeats.appendleft(record)
            return record

    def heartbeat_answered(
        self, record: HeartbeatRecord, server_time: str, clock_offset_s: float | None
    ) -> None:
        """Complete a heartbeat with the server's answer.

        Args:
            record: The record from ``heartbeat_sent``.
            server_time: The ``currentTime`` the server sent.
            clock_offset_s: Server clock minus charger clock, if readable.
        """
        with self._lock:
            record.answered_at = utc_now()
            record.round_trip_ms = (
                record.answered_at.timestamp() - record.sent_at.timestamp()
            ) * 1000
            record.server_time = server_time
            record.clock_offset_s = clock_offset_s
            record.outcome = "answered"

    def heartbeat_failed(
        self, record: HeartbeatRecord, outcome: str, error: str
    ) -> None:
        """Complete a heartbeat that got a CALLERROR or no answer.

        Args:
            record: The record from ``heartbeat_sent``.
            outcome: ``callerror`` or ``no answer``.
            error: What went wrong.
        """
        with self._lock:
            record.outcome = outcome
            record.error = error

    # --- page -------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return everything the page shows, as JSON-ready values.

        Returns:
            The charger, its connection, the startup exchange, the heartbeat
            interval and the recent heartbeats (newest first).
        """
        with self._lock:
            settings = self.settings
            boot = None
            if self._boot is not None:
                boot = dict(self._boot)
                boot["sent_at"] = iso(boot["sent_at"])
                boot["answered_at"] = iso(boot["answered_at"])
            heartbeats = [
                {
                    "number": record.number,
                    "sent_at": iso(record.sent_at),
                    "interval_s": record.interval_s,
                    "gap_s": record.gap_s,
                    "check": record.check(),
                    "answered_at": iso(record.answered_at),
                    "round_trip_ms": record.round_trip_ms,
                    "server_time": record.server_time,
                    "clock_offset_s": record.clock_offset_s,
                    "outcome": record.outcome,
                    "error": record.error,
                }
                for record in self._heartbeats
            ]
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
                "boot": boot,
                "boot_attempts": self._boot_attempts,
                "heartbeat": {
                    "interval_s": self._heartbeat_interval,
                    "interval_source": self._interval_source,
                    "count": self._heartbeat_count,
                    "next_at": iso(self._next_heartbeat_at),
                    "recent": heartbeats,
                },
                "server_requests": list(self._server_requests),
            }
