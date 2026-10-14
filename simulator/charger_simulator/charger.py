"""The simulated charger's side of one OCPP 1.6J connection.

Wraps the ``ocpp`` library's ``ChargePoint``: the charger's own messages
(``BootNotification``, ``Heartbeat``) and the answers to the server's
requests. No request is supported yet, so each one is answered with a
``NotImplemented`` CALLERROR (the backend's post-boot ``GetConfiguration``
included).
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ocpp.exceptions import OCPPError
from ocpp.messages import CallError
from ocpp.v16 import ChargePoint, call
from ocpp.v16.enums import RegistrationStatus
from settings import ChargerSettings
from websockets.asyncio.client import ClientConnection

# Seconds to wait for the server's answer to one of our messages.
RESPONSE_TIMEOUT_SECONDS = 30

# Interval used when the server answers a boot with ``interval`` 0, which 1.6
# leaves to the charger.
FALLBACK_INTERVAL_SECONDS = 60.0

# Wait before booting again after ``Pending`` / ``Rejected`` with interval 0.
FALLBACK_BOOT_RETRY_SECONDS = 10


def say(text: str) -> None:
    """Print one log line prefixed with the charger's UTC clock.

    Args:
        text: The line to print.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"{now}Z  {text}", flush=True)


def parse_ocpp_time(text: str) -> datetime | None:
    """Parse an OCPP timestamp, reading one without a timezone as UTC.

    Args:
        text: The server's ``currentTime``.

    Returns:
        The time, timezone-aware; ``None`` when it cannot be parsed.
    """
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class ChargerCounters:
    """Running totals since the simulator started, printed when it stops.

    Attributes:
        started_at: When the simulator started (UTC).
        connections: WebSocket connections opened.
        boots: BootNotifications answered (any status).
        heartbeats_answered: Heartbeats the server answered.
        heartbeats_failed: Heartbeats answered with a CALLERROR.
        last_clock_offset_s: Server clock minus charger clock at the last
            heartbeat; ``None`` before the first one.
        server_requests: Requests the server sent, by action.
    """

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    connections: int = 0
    boots: int = 0
    heartbeats_answered: int = 0
    heartbeats_failed: int = 0
    last_clock_offset_s: float | None = None
    server_requests: dict[str, int] = field(default_factory=dict)


class SimulatedCharger(ChargePoint):  # type: ignore[misc]
    """The charger on one WebSocket connection.

    A new instance is made for every connection; the counters are shared.

    Attributes:
        settings: The charger's settings (boot fields).
        counters: The simulator's running totals.
    """

    def __init__(
        self,
        settings: ChargerSettings,
        connection: ClientConnection,
        counters: ChargerCounters,
    ) -> None:
        """Bind the library's charge point to one connection.

        Args:
            settings: The charger's settings.
            connection: The open WebSocket connection.
            counters: The simulator's running totals.
        """
        super().__init__(
            settings.charger_id, connection, response_timeout=RESPONSE_TIMEOUT_SECONDS
        )
        self.settings = settings
        self.counters = counters

    async def route_message(self, raw_msg: str) -> None:
        """Print each request from the server, then answer or route it.

        A request with no handler is answered here with a ``NotImplemented``
        CALLERROR, the frame the library would send, without the traceback
        the library logs for it. Everything else, answers to our own calls
        included, goes to the library.

        Args:
            raw_msg: One raw OCPP-J frame.
        """
        with suppress(ValueError, TypeError, IndexError):
            frame = json.loads(raw_msg)
            if frame[0] == 2:
                unique_id, action = str(frame[1]), str(frame[2])
                requests = self.counters.server_requests
                requests[action] = requests.get(action, 0) + 1
                if action not in self.route_map:
                    reply = CallError(
                        unique_id=unique_id,
                        error_code="NotImplemented",
                        error_description=f"No handler for {action} yet",
                        error_details={},
                    )
                    await self._send(reply.to_json())
                    say(f"<- {action} from the server: answered NotImplemented")
                    return
                say(f"<- {action} from the server")
        await super().route_message(raw_msg)

    async def boot_until_accepted(self) -> float:
        """Send BootNotification until the server accepts it.

        Per 1.6 section 4.2, after ``Pending`` or ``Rejected`` the charger
        sends no other message and boots again after the returned interval.

        Returns:
            The heartbeat interval to use, in seconds: the
            ``CHARGER_HEARTBEAT_INTERVAL`` override, else the server's, else
            the fallback when the server returned 0.

        Raises:
            OCPPError: The server answered the boot with a CALLERROR.
            TimeoutError: No answer within the response timeout.
        """
        settings = self.settings
        while True:
            say(f"-> BootNotification ({settings.vendor} {settings.model})")
            response = await self.call(
                call.BootNotification(
                    charge_point_vendor=settings.vendor,
                    charge_point_model=settings.model,
                    charge_point_serial_number=settings.serial,
                    firmware_version=settings.firmware,
                ),
                suppress=False,
            )
            self.counters.boots += 1
            status = RegistrationStatus(response.status)
            say(
                f"<- BootNotification {status.value}, interval "
                f"{response.interval} s, currentTime {response.current_time}"
            )
            if status is RegistrationStatus.accepted:
                break
            retry = response.interval or FALLBACK_BOOT_RETRY_SECONDS
            say(f"   boot not accepted: booting again in {retry} s")
            await asyncio.sleep(retry)

        if settings.heartbeat_interval is not None:
            say(
                f"   heartbeat every {settings.heartbeat_interval:g} s "
                f"(CHARGER_HEARTBEAT_INTERVAL; the server asked "
                f"{response.interval} s)"
            )
            return settings.heartbeat_interval
        if response.interval <= 0:
            say(f"   server left the interval open: {FALLBACK_INTERVAL_SECONDS:g} s")
            return FALLBACK_INTERVAL_SECONDS
        say(f"   heartbeat every {response.interval} s (the server's interval)")
        return float(response.interval)

    async def heartbeat(self) -> None:
        """Send one Heartbeat and print the round trip and clock offset.

        The offset compares the server's ``currentTime`` with the charger's
        clock halfway through the round trip. A CALLERROR is printed and
        counted; the next heartbeat still goes out.

        Raises:
            TimeoutError: No answer within the response timeout; the caller
                treats the connection as dead.
        """
        number = self.counters.heartbeats_answered + self.counters.heartbeats_failed + 1
        sent_at = datetime.now(timezone.utc)
        started = time.monotonic()
        try:
            response = await self.call(call.Heartbeat(), suppress=False)
        except OCPPError as error:
            self.counters.heartbeats_failed += 1
            say(f"<> Heartbeat #{number}: CALLERROR {type(error).__name__}: {error}")
            return
        round_trip = time.monotonic() - started
        self.counters.heartbeats_answered += 1
        server_time = parse_ocpp_time(response.current_time)
        if server_time is None:
            say(
                f"<> Heartbeat #{number}: unreadable currentTime {response.current_time!r}"
            )
            return
        offset = server_time.timestamp() - (sent_at.timestamp() + round_trip / 2)
        self.counters.last_clock_offset_s = offset
        say(
            f"<> Heartbeat #{number}: currentTime {response.current_time}, "
            f"round trip {round_trip * 1000:.0f} ms, clock offset {offset:+.2f} s"
        )
