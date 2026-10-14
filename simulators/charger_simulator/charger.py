"""The simulated charger's side of one OCPP 1.6J connection.

Wraps the ``ocpp`` library's ``ChargePoint``: the charger's own messages
(``BootNotification``, ``Heartbeat``) and the answers to the server's
requests. Everything it sends and receives that the web page shows is
recorded in ``SimulatorState``. No server request is supported yet, so each
is answered with a ``NotImplemented`` CALLERROR (the server's post-boot
``GetConfiguration`` included).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import datetime, timezone

from ocpp.exceptions import OCPPError
from ocpp.messages import CallError
from ocpp.v16 import ChargePoint, call
from ocpp.v16.enums import RegistrationStatus
from settings import ChargerSettings
from state import SimulatorState, utc_now
from websockets.asyncio.client import ClientConnection

# Seconds to wait for the server's answer to one of our messages.
RESPONSE_TIMEOUT_SECONDS = 30

# Interval used when the server answers a boot with ``interval`` 0, which 1.6
# leaves to the charger.
FALLBACK_INTERVAL_SECONDS = 60.0

# Wait before booting again after ``Pending`` / ``Rejected`` with interval 0.
FALLBACK_BOOT_RETRY_SECONDS = 10


def log(text: str) -> None:
    """Print one console line prefixed with the charger's UTC clock.

    Args:
        text: The line to print.
    """
    now = utc_now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
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


def frame_parts(raw_frame: str) -> tuple[int | None, str, str]:
    """Read an OCPP-J frame's type, message ID and action.

    Args:
        raw_frame: The frame text.

    Returns:
        ``(message type, unique ID, action)``; the type is ``None`` and the
        rest empty when the frame is not OCPP-shaped. Answers have no action.
    """
    with suppress(ValueError, TypeError, IndexError):
        frame = json.loads(raw_frame)
        message_type = int(frame[0])
        action = str(frame[2]) if message_type == 2 else ""
        return message_type, str(frame[1]), action
    return None, "", ""


class SimulatedCharger(ChargePoint):  # type: ignore[misc]
    """The charger on one WebSocket connection.

    A new instance is made for every connection; the state is shared.

    Attributes:
        settings: The charger's settings (boot fields, interval override).
        state: The simulator's live state, shown on the web page.
    """

    def __init__(
        self,
        settings: ChargerSettings,
        connection: ClientConnection,
        state: SimulatorState,
    ) -> None:
        """Bind the library's charge point to one connection.

        Args:
            settings: The charger's settings.
            connection: The open WebSocket connection.
            state: The simulator's live state.
        """
        super().__init__(
            settings.charger_id, connection, response_timeout=RESPONSE_TIMEOUT_SECONDS
        )
        self.settings = settings
        self.state = state

    async def _send(self, message: str) -> None:
        """Send a frame, recording a BootNotification for the startup panel.

        Args:
            message: The OCPP-J frame.
        """
        message_type, unique_id, action = frame_parts(message)
        if message_type == 2 and action == "BootNotification":
            self.state.boot_sent(unique_id, message)
        await super()._send(message)

    async def route_message(self, raw_msg: str) -> None:
        """Record what the page needs, then answer or route the frame.

        The answer to the BootNotification is recorded for the startup panel.
        A server request with no handler is answered here with a
        ``NotImplemented`` CALLERROR (the frame the library would send,
        without the traceback it logs). Everything else goes to the library.

        Args:
            raw_msg: One raw OCPP-J frame.
        """
        message_type, unique_id, action = frame_parts(raw_msg)
        if message_type in (3, 4) and self.state.is_boot_answer(unique_id):
            self.state.boot_answer_received(unique_id, raw_msg)
        if message_type == 2 and action not in self.route_map:
            reply = CallError(
                unique_id=unique_id,
                error_code="NotImplemented",
                error_description=f"No handler for {action} yet",
                error_details={},
            )
            await self._send(reply.to_json())
            self.state.server_request(action, "NotImplemented")
            log(f"<- {action} from the server: answered NotImplemented")
            return
        await super().route_message(raw_msg)

    async def boot_until_accepted(self) -> float:
        """Send BootNotification until the server accepts it.

        Per 1.6 section 4.2, after ``Pending`` or ``Rejected`` the charger
        sends no other message and boots again after the returned interval.

        Returns:
            The heartbeat interval to use, in seconds: the
            ``CHARGER_HEARTBEAT_INTERVAL`` override, else the server's, else
            the fallback when the server returned 0. Also recorded in the
            state with its source.

        Raises:
            OCPPError: The server answered the boot with a CALLERROR.
            TimeoutError: No answer within the response timeout.
        """
        settings = self.settings
        while True:
            log(f"-> BootNotification ({settings.vendor} {settings.model})")
            response = await self.call(
                call.BootNotification(
                    charge_point_vendor=settings.vendor,
                    charge_point_model=settings.model,
                    charge_point_serial_number=settings.serial,
                    firmware_version=settings.firmware,
                ),
                suppress=False,
            )
            status = RegistrationStatus(response.status)
            log(
                f"<- BootNotification {status.value}, interval "
                f"{response.interval} s, currentTime {response.current_time}"
            )
            if status is RegistrationStatus.accepted:
                break
            retry = response.interval or FALLBACK_BOOT_RETRY_SECONDS
            log(f"   boot not accepted: booting again in {retry} s")
            await asyncio.sleep(retry)

        if settings.heartbeat_interval is not None:
            interval, source = settings.heartbeat_interval, "setting"
        elif response.interval <= 0:
            interval, source = FALLBACK_INTERVAL_SECONDS, "fallback"
        else:
            interval, source = float(response.interval), "server"
        self.state.heartbeat_interval_set(interval, source)
        log(f"   heartbeat every {interval:g} s ({source})")
        return interval

    async def heartbeat(self) -> None:
        """Send one Heartbeat and record its answer, round trip and clock offset.

        The offset compares the server's ``currentTime`` with the charger's
        clock halfway through the round trip. A CALLERROR is recorded and the
        next heartbeat still goes out.

        Raises:
            TimeoutError: No answer within the response timeout (recorded as
                ``no answer``); the caller treats the connection as dead.
        """
        record = self.state.heartbeat_sent()
        try:
            response = await self.call(call.Heartbeat(), suppress=False)
        except OCPPError as error:
            self.state.heartbeat_failed(record, "callerror", f"{type(error).__name__}")
            log(f"<> Heartbeat #{record.number}: CALLERROR {type(error).__name__}")
            return
        except TimeoutError:
            self.state.heartbeat_failed(record, "no answer", "no answer in time")
            log(f"<> Heartbeat #{record.number}: no answer")
            raise
        server_time = parse_ocpp_time(response.current_time)
        offset = None
        if server_time is not None:
            midpoint = (record.sent_at.timestamp() + utc_now().timestamp()) / 2
            offset = server_time.timestamp() - midpoint
        self.state.heartbeat_answered(record, response.current_time, offset)
        gap = f"{record.gap_s:.1f} s after the previous" if record.gap_s else "first"
        log(f"<> Heartbeat #{record.number}: answered ({gap})")
