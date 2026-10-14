"""The simulated charger's side of one OCPP 1.6J connection.

Wraps the ``ocpp`` library's ``ChargePoint``: the charger's own messages
(``BootNotification``, ``Heartbeat``) and the answers to the server's
requests. Each exchange is written to the charger's console log only; the
OCPP traffic is not shown on the charger's web page. No server request is
supported yet, so each is answered with a ``NotImplemented`` CALLERROR (the
server's post-boot ``GetConfiguration`` included).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress

from ocpp.exceptions import OCPPError
from ocpp.messages import CallError
from ocpp.v16 import ChargePoint, call
from ocpp.v16.enums import RegistrationStatus
from settings import ChargerSettings
from state import utc_now
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

    A new instance is made for every connection.

    Attributes:
        settings: The charger's settings (boot fields, interval override).
    """

    def __init__(self, settings: ChargerSettings, connection: ClientConnection) -> None:
        """Bind the library's charge point to one connection.

        Args:
            settings: The charger's settings.
            connection: The open WebSocket connection.
        """
        super().__init__(
            settings.charger_id, connection, response_timeout=RESPONSE_TIMEOUT_SECONDS
        )
        self.settings = settings

    async def route_message(self, raw_msg: str) -> None:
        """Answer or route one frame from the server.

        A server request with no handler is answered here with a
        ``NotImplemented`` CALLERROR (the frame the library would send,
        without the traceback it logs). Everything else, answers to our own
        calls included, goes to the library.

        Args:
            raw_msg: One raw OCPP-J frame.
        """
        message_type, unique_id, action = frame_parts(raw_msg)
        if message_type == 2 and action not in self.route_map:
            reply = CallError(
                unique_id=unique_id,
                error_code="NotImplemented",
                error_description=f"No handler for {action} yet",
                error_details={},
            )
            await self._send(reply.to_json())
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
            the fallback when the server returned 0.

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
            interval, source = settings.heartbeat_interval, "CHARGER_HEARTBEAT_INTERVAL"
        elif response.interval <= 0:
            interval, source = FALLBACK_INTERVAL_SECONDS, "fallback"
        else:
            interval, source = float(response.interval), "server"
        log(f"   heartbeat every {interval:g} s ({source})")
        return interval

    async def heartbeat(self) -> None:
        """Send one Heartbeat and log the server's answer.

        A CALLERROR is logged and the next heartbeat still goes out.

        Raises:
            TimeoutError: No answer within the response timeout; the caller
                treats the connection as dead.
        """
        log("-> Heartbeat")
        try:
            response = await self.call(call.Heartbeat(), suppress=False)
        except OCPPError as error:
            log(f"<- Heartbeat CALLERROR {type(error).__name__}")
            return
        log(f"<- Heartbeat currentTime {response.current_time}")
