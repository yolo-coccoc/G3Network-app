"""The simulated charger's side of one OCPP 1.6J connection.

Wraps the ``ocpp`` library's ``ChargePoint``: the charger's own messages
(``BootNotification``, ``Heartbeat``) and the answers to the server's
requests. Each exchange is written to the charger's console log only; the
OCPP traffic is not shown on the charger's web page.

Supported server requests: ``GetConfiguration`` and ``ChangeConfiguration``
(``configuration.py``), ``RemoteStartTransaction`` and
``RemoteStopTransaction`` (handed to the station, ``station.py``). Any other
request is answered with a ``NotImplemented`` CALLERROR. A handler never
waits for a call of its own (the library would deadlock): the station queues
the follow-up messages and sends them after the answer.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress

from configuration import ChargerConfiguration
from ocpp.exceptions import OCPPError
from ocpp.messages import CallError
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call, call_result
from ocpp.v16.enums import (
    Action,
    ConfigurationStatus,
    RegistrationStatus,
    RemoteStartStopStatus,
)
from settings import ChargerSettings
from state import log
from station import ChargingStation, mask
from websockets.asyncio.client import ClientConnection

# Seconds to wait for the server's answer to one of our messages.
RESPONSE_TIMEOUT_SECONDS = 30

# Interval used when the server answers a boot with ``interval`` 0, which 1.6
# leaves to the charger.
FALLBACK_INTERVAL_SECONDS = 60

# Wait before booting again after ``Pending`` / ``Rejected`` with interval 0.
FALLBACK_BOOT_RETRY_SECONDS = 10


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

    A new instance is made for every connection; the configuration is the
    charger's and outlives it.

    Attributes:
        settings: The charger's settings (boot fields, interval override).
        configuration: The charger's OCPP configuration keys.
        station: The charger's guns and charging logic.
    """

    def __init__(
        self,
        settings: ChargerSettings,
        connection: ClientConnection,
        configuration: ChargerConfiguration,
        station: ChargingStation,
    ) -> None:
        """Bind the library's charge point to one connection.

        Args:
            settings: The charger's settings.
            connection: The open WebSocket connection.
            configuration: The charger's OCPP configuration keys.
            station: The charger's guns and charging logic.
        """
        super().__init__(
            settings.charger_id, connection, response_timeout=RESPONSE_TIMEOUT_SECONDS
        )
        self.settings = settings
        self.configuration = configuration
        self.station = station

    @on(Action.remote_start_transaction)  # type: ignore[untyped-decorator]
    async def on_remote_start_transaction(
        self, id_tag: str, connector_id: int | None = None, **_: object
    ) -> call_result.RemoteStartTransaction:
        """Accept or refuse a remote start (the driver scanned the QR code).

        Args:
            id_tag: The token the backend issued for the scan.
            connector_id: The gun the driver chose; ``None`` = any gun.
            **_: Fields this charger does not use (a charging profile).

        Returns:
            ``Accepted`` or ``Rejected``.
        """
        status = self.station.remote_start(id_tag, connector_id)
        gun = f"gun {connector_id}" if connector_id else "any gun"
        log(f"<- RemoteStartTransaction {mask(id_tag)} on {gun}: {status}")
        return call_result.RemoteStartTransaction(status=RemoteStartStopStatus(status))

    @on(Action.remote_stop_transaction)  # type: ignore[untyped-decorator]
    async def on_remote_stop_transaction(
        self, transaction_id: int, **_: object
    ) -> call_result.RemoteStopTransaction:
        """Accept or refuse a remote stop (the driver stopped in the app).

        Args:
            transaction_id: The transaction to stop.
            **_: Nothing else is defined.

        Returns:
            ``Accepted``, or ``Rejected`` for an unknown transaction.
        """
        status = self.station.remote_stop(transaction_id)
        log(f"<- RemoteStopTransaction {transaction_id}: {status}")
        return call_result.RemoteStopTransaction(status=RemoteStartStopStatus(status))

    @on(Action.get_configuration)  # type: ignore[untyped-decorator]
    async def on_get_configuration(
        self, key: list[str] | None = None, **_: object
    ) -> call_result.GetConfiguration:
        """Report the configuration: every key, or the ones asked for.

        Args:
            key: The keys asked for; ``None`` = all of them.
            **_: Fields this charger does not use.

        Returns:
            The known keys with their values and read-only flags, and the
            names of the keys it does not have.
        """
        entries, unknown = self.configuration.report(key)
        log(
            f"<- GetConfiguration ({', '.join(key) if key else 'all keys'}): "
            f"sent {len(entries)} key(s)"
            + (f", unknown {', '.join(unknown)}" if unknown else "")
        )
        return call_result.GetConfiguration(
            configuration_key=entries, unknown_key=unknown or None
        )

    @on(Action.change_configuration)  # type: ignore[untyped-decorator]
    async def on_change_configuration(
        self, key: str, value: str, **_: object
    ) -> call_result.ChangeConfiguration:
        """Change one configuration key, if it exists, is writable and valid.

        Args:
            key: The key to change.
            value: Its new value.
            **_: Fields this charger does not use.

        Returns:
            ``Accepted``, ``Rejected`` or ``NotSupported``.
        """
        status = self.configuration.change(key, value)
        log(f"<- ChangeConfiguration {key}={value!r}: {status}")
        return call_result.ChangeConfiguration(status=ConfigurationStatus(status))

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

    async def boot_until_accepted(self) -> None:
        """Send BootNotification until the server accepts it.

        Per 1.6 section 4.2, after ``Pending`` or ``Rejected`` the charger
        sends no other message and boots again after the returned interval.
        The accepted answer sets ``HeartbeatInterval`` in the configuration:
        the ``CHARGER_HEARTBEAT_INTERVAL`` setting if given, else the
        server's interval, else the fallback when the server returned 0.

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
            interval = round(settings.heartbeat_interval)
            source = "CHARGER_HEARTBEAT_INTERVAL"
        elif response.interval <= 0:
            interval, source = FALLBACK_INTERVAL_SECONDS, "fallback"
        else:
            interval, source = int(response.interval), "server"
        self.configuration.set_heartbeat_interval(interval)
        log(f"   heartbeat every {self.configuration.heartbeat_interval} s ({source})")

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
