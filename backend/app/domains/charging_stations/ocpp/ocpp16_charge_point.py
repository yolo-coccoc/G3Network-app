"""OCPP 1.6J adapter for one connected charge point.

This module holds the adapter class for the ``ocpp1.6`` subprotocol. It is
deliberately separate from the OCPP 2.0.1 adapter: the two protocols have
incompatible payload shapes (1.6J has no EVSE level, no ``TransactionEvent``,
and reports units in a flat ``unit`` field), and mixing them in one class would
hide which contract each handler assumes.

Scope: handlers are added one message group at a time by the OCPP 1.6J
planner (``docs/02-planners/backend-ocpp16-charger-integration.md``). Currently
handled: ``BootNotification`` and ``Heartbeat``. Every other action a 1.6J
charger sends is answered with ``CALLERROR NotImplemented`` by ``python-ocpp``
(the frame is still stored by the raw message log).
"""

import logging
from datetime import datetime, timezone

from ocpp.routing import on
from ocpp.v16 import ChargePoint, call_result
from ocpp.v16.enums import Action, RegistrationStatus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.charging_stations import service as charging_stations_service
from app.domains.charging_stations.ocpp.parsing import format_ocpp_timestamp
from app.domains.charging_stations.ocpp.raw_log import RecordingConnection
from app.libs.common.config import settings

logger = logging.getLogger(__name__)


class OCPP16ChargePoint(ChargePoint):  # type: ignore[misc]
    """``python-ocpp`` adapter attaching an accepted 1.6J WebSocket to a station.

    Attributes:
        id: Station identity used by ``ChargePoint`` when dispatching OCPP.
        connection: Recording wrapper around the WebSocket connection created
            by ``websockets`` after the handshake; ``ChargePoint`` only calls
            its ``recv()``/``send()``.
        session_factory: Shared factory used for each persistence operation
            (the entry boundary owns every transaction).

    Note:
        The class receives OCPP actions after the handshake and does not
        create the WebSocket connection itself.
    """

    def __init__(
        self,
        identity: str,
        connection: RecordingConnection,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Initialize the OCPP 1.6J adapter for an already validated connection.

        Args:
            identity: OCPP identity already resolved in the database.
            connection: Recording wrapper around the WebSocket connection that
                completed the handshake.
            session_factory: Shared factory owning the transaction for each
                action handler.

        Side Effects:
            Initializes the ``python-ocpp`` base class state and attaches the
            module logger.
        """
        super().__init__(identity, connection, logger=logger)
        self.session_factory = session_factory

    @on(Action.boot_notification)  # type: ignore[untyped-decorator]
    async def on_boot_notification(
        self,
        charge_point_vendor: str,
        charge_point_model: str,
        firmware_version: str | None = None,
        charge_point_serial_number: str | None = None,
        charge_box_serial_number: str | None = None,
        **_: object,
    ) -> call_result.BootNotification:
        """Record the charger's identity and accept the boot.

        Only ``chargePointVendor`` and ``chargePointModel`` are required by the
        1.6 schema; everything else may be absent. An unprovisioned station
        never gets here (the handshake rejects it), so the boot is always
        ``Accepted``.

        Args:
            charge_point_vendor: Vendor name.
            charge_point_model: Model name.
            firmware_version: Firmware version, if reported.
            charge_point_serial_number: Charger serial number, if reported;
                preferred over ``charge_box_serial_number``.
            charge_box_serial_number: Legacy serial number, used only when the
                charger sends no ``charge_point_serial_number``.
            **_: Other optional OCPP fields (``iccid``, ``imsi``, meter data)
                that are not stored.

        Returns:
            ``Accepted`` with the server's current time (the charger syncs its
            clock from it) and the heartbeat interval in seconds.

        Side Effects:
            Updates the station's device fields in an atomic transaction.
        """
        booted_at = datetime.now(timezone.utc)
        async with self.session_factory.begin() as db:
            await charging_stations_service.record_charger_boot(
                db,
                ocpp_identity=self.id,
                vendor=charge_point_vendor,
                model=charge_point_model,
                serial_number=charge_point_serial_number or charge_box_serial_number,
                firmware_version=firmware_version,
                booted_at=booted_at,
            )
        return call_result.BootNotification(
            current_time=format_ocpp_timestamp(datetime.now(timezone.utc)),
            interval=settings.CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS,
            status=RegistrationStatus.accepted,
        )

    @on(Action.heartbeat)  # type: ignore[untyped-decorator]
    async def on_heartbeat(self, **_: object) -> call_result.Heartbeat:
        """Answer a heartbeat with the server's current time.

        Liveness (``last_seen_at``) is already recorded for every inbound frame
        by the raw message log, so nothing else is stored here.

        Args:
            **_: A 1.6 ``Heartbeat`` has no fields.

        Returns:
            The server's current UTC time, which the charger uses to sync its
            clock.
        """
        return call_result.Heartbeat(
            current_time=format_ocpp_timestamp(datetime.now(timezone.utc))
        )
