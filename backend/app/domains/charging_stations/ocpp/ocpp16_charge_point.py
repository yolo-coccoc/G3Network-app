"""OCPP 1.6J adapter for one connected charge point.

This module holds the adapter class for the ``ocpp1.6`` subprotocol. It is
deliberately separate from the OCPP 2.0.1 adapter: the two protocols have
incompatible payload shapes (1.6J has no EVSE level, no ``TransactionEvent``,
and reports units in a flat ``unit`` field), and mixing them in one class would
hide which contract each handler assumes.

Scope: handlers are added one message group at a time by the OCPP 1.6J
planner (``docs/02-planners/backend-ocpp16-charger-integration.md``). Currently
handled: ``BootNotification``, ``Heartbeat``, ``StatusNotification``,
``Authorize``, ``StartTransaction``, ``StopTransaction`` and ``MeterValues``. Every other action a 1.6J
charger sends is answered with ``CALLERROR NotImplemented`` by ``python-ocpp``
(the frame is still stored by the raw message log).
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from ocpp.routing import on
from ocpp.v16 import ChargePoint, call_result
from ocpp.v16.enums import Action, AuthorizationStatus, RegistrationStatus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.charging_sessions import service as charging_sessions_service
from app.domains.charging_sessions.types import SessionEventType
from app.domains.charging_stations import service as charging_stations_service
from app.domains.charging_stations.ocpp.ocpp16_measurements import (
    V16Extraction,
    extract_v16_measurements,
    to_decimal,
)
from app.domains.charging_stations.ocpp.parsing import (
    OcppPayload,
    format_ocpp_timestamp,
    parse_ocpp_timestamp,
)
from app.domains.charging_stations.ocpp.raw_log import RecordingConnection
from app.domains.charging_stations.types import ChargingConnectorStatus
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

    @on(Action.status_notification)  # type: ignore[untyped-decorator]
    async def on_status_notification(
        self,
        connector_id: int,
        error_code: str,
        status: str,
        timestamp: str | None = None,
        info: str | None = None,
        vendor_error_code: str | None = None,
        **_: object,
    ) -> call_result.StatusNotification:
        """Record a gun's (or the whole charger's) status and error details.

        Connector ``0`` is the whole charger and is stored on the station;
        connector ``n >= 1`` is gun ``n`` and is stored on the connector
        provisioned as EVSE ``n`` / connector ``1`` (decision D3). The status
        keeps the exact 1.6J label (decision D4), and ``errorCode``,
        ``vendorErrorCode`` and ``info`` are stored as sent.

        Args:
            connector_id: ``0`` for the whole charger, otherwise the gun number.
            error_code: OCPP 1.6 ``ChargePointErrorCode`` (``NoError`` included).
            status: OCPP 1.6 ``ChargePointStatus`` label. Arrives as a plain
                ``str`` at runtime, like the 2.0.1 handler.
            timestamp: Time of the status change; **optional** in 1.6, so the
                server's receive time is used when it is absent.
            info: Free-text additional information.
            vendor_error_code: Vendor-specific error code.
            **_: Other optional OCPP fields (``vendor_id``) that are not stored.

        Returns:
            A valid empty response for StatusNotification.

        Raises:
            ValueError: If ``status`` is not one of the nine 1.6 statuses, or
                ``timestamp`` has no timezone.
            ChargingStationNotFoundError: If the station is not provisioned.
            ChargingEvseNotFoundError: If the gun's EVSE is not provisioned.
            ChargingConnectorNotFoundError: If the gun's connector is not
                provisioned.

        Side Effects:
            Updates the station or connector row in an atomic transaction that
            rolls back entirely on any error above; ``python-ocpp`` then
            answers ``CALLERROR(InternalError)`` without closing the
            connection (the same contract as the other handlers).
        """
        reported_status = ChargingConnectorStatus(status)
        reported_at = (
            parse_ocpp_timestamp(timestamp)
            if timestamp is not None
            else datetime.now(timezone.utc)
        )
        async with self.session_factory.begin() as db:
            if connector_id == 0:
                await charging_stations_service.update_charger_status(
                    db,
                    ocpp_identity=self.id,
                    status=reported_status,
                    status_updated_at=reported_at,
                    error_code=error_code,
                    vendor_error_code=vendor_error_code,
                )
            else:
                _station_id, _evse_id, connector_uuid = (
                    await charging_stations_service.resolve_ocpp16_topology(
                        db,
                        ocpp_identity=self.id,
                        ocpp_connector_id=connector_id,
                    )
                )
                await charging_stations_service.update_connector_status(
                    db,
                    connector_id=connector_uuid,
                    status=reported_status,
                    status_updated_at=reported_at,
                    error_code=error_code,
                    vendor_error_code=vendor_error_code,
                    status_info=info,
                )
        return call_result.StatusNotification()

    @on(Action.authorize)  # type: ignore[untyped-decorator]
    async def on_authorize(self, id_tag: str, **_: object) -> call_result.Authorize:
        """Accept every idTag (decision D7 of the OCPP 1.6J planner).

        There is no tag registry to check against and the vendor's Autocharge
        behaviour is unknown, so rejecting unknown tags would block all
        charging. Real validation is deferred (``future.md`` #26, #62).

        Args:
            id_tag: The tag presented at the charger; not stored here (it is
                stored on the session by ``StartTransaction``).
            **_: A 1.6 ``Authorize`` has no other fields.

        Returns:
            ``Accepted`` for any tag.
        """
        return call_result.Authorize(
            id_tag_info={"status": AuthorizationStatus.accepted}
        )

    @on(Action.start_transaction)  # type: ignore[untyped-decorator]
    async def on_start_transaction(
        self,
        connector_id: int,
        id_tag: str,
        meter_start: int,
        timestamp: str,
        reservation_id: int | None = None,
        **_: object,
    ) -> call_result.StartTransaction:
        """Open a charging session and hand the charger its transaction ID.

        OCPP 1.6J requires the CSMS to assign the integer ``transactionId``;
        it comes from a database sequence (decision D6) and is stored, as text,
        in the session's ``ocpp_transaction_id``. The ``idTag`` is stored on the
        session and always accepted (decision D7).

        If the connector already has an ``active`` session (a charger that
        rebooted mid-session, for example), a structured warning is logged and
        the old session is left untouched: orphan handling waits for real
        charger logs (decision D14).

        Args:
            connector_id: The gun number (``>= 1``; gun ``n`` is EVSE ``n`` /
                connector ``1``).
            id_tag: The tag that started the session.
            meter_start: Meter reading at the start, an integer in Wh.
            timestamp: Start time (must carry a timezone).
            reservation_id: Reservation that led to this session; not stored.
            **_: Other optional OCPP fields.

        Returns:
            ``Accepted`` with the newly allocated ``transactionId``.

        Raises:
            ValueError: If ``timestamp`` has no timezone.
            ChargingOcppMessageInputError: If ``connector_id`` is ``0``.
            ChargingStationNotFoundError: If the station is not provisioned.
            ChargingEvseNotFoundError: If the gun's EVSE is not provisioned.
            ChargingConnectorNotFoundError: If the gun's connector is not
                provisioned.
            ChargingSessionInputError: If the tag or meter value is invalid.

        Side Effects:
            Allocates an ID, creates the session and its ``Started`` event in
            one atomic transaction; everything rolls back on any error above
            (the ID number is then skipped, which is harmless).
        """
        started_at = parse_ocpp_timestamp(timestamp)
        async with self.session_factory.begin() as db:
            station_id, evse_id, connector_uuid = (
                await charging_stations_service.resolve_ocpp16_topology(
                    db,
                    ocpp_identity=self.id,
                    ocpp_connector_id=connector_id,
                )
            )
            if await charging_sessions_service.has_active_session_on_connector(
                db, connector_uuid
            ):
                logger.warning(
                    "StartTransaction on a connector that already has an active session",
                    extra={"ocpp_identity": self.id, "ocpp_connector_id": connector_id},
                )
            transaction_id = (
                await charging_sessions_service.allocate_ocpp16_transaction_id(db)
            )
            await charging_sessions_service.ingest_transaction_event(
                db,
                station_id=station_id,
                evse_id=evse_id,
                connector_id=connector_uuid,
                transaction_id=str(transaction_id),
                event_type=SessionEventType.STARTED,
                event_occurred_at=started_at,
                seq_no=None,
                meter_start_wh=to_decimal(meter_start, "meter_start"),
                id_tag=id_tag,
            )
        return call_result.StartTransaction(
            transaction_id=transaction_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    @on(Action.stop_transaction, skip_schema_validation=True)  # type: ignore[untyped-decorator]
    async def on_stop_transaction(
        self,
        meter_stop: int,
        timestamp: str,
        transaction_id: int,
        reason: str | None = None,
        id_tag: str | None = None,
        transaction_data: list[OcppPayload] | None = None,
        **_: object,
    ) -> call_result.StopTransaction:
        """Close a charging session with the charger's own closing reading.

        ``StopTransaction`` carries no connector, so the session (and with it
        the topology) is looked up in the database by ``(station,
        transactionId)`` — nothing is remembered per connection. The closing
        ``meterStop`` is always stored as ``meter_stop_wh``; the session's
        ``meter_end_wh`` follows the existing forward-in-time rule, so a stale
        timestamp never overwrites a newer reading. Meter values sampled during
        the session (``transactionData``) are stored first, while the session is
        still ``active``.

        JSON-schema validation is switched off for this action on purpose: the
        1.6 schema restricts ``reason`` and every sampled-value field to fixed
        lists, so a single vendor-specific stop reason or measurand would make
        the library reject the whole message and leave the session open forever.
        The fields the backend relies on are validated here instead, and a
        ``reason`` longer than the 30-character column is truncated (the raw
        frame in the message log keeps the original).

        Args:
            meter_stop: Meter reading at the end, in Wh.
            timestamp: Stop time (must carry a timezone).
            transaction_id: The ID this backend allocated in
                ``StartTransaction``.
            reason: Why the session stopped (``EmergencyStop``,
                ``EVDisconnected``, a vendor value…), stored as sent.
            id_tag: Tag that stopped the session; not stored.
            transaction_data: Meter values sampled during the session; energy
                samples update the session total, other measurands are stored
                as measurements.
            **_: Other optional OCPP fields.

        Returns:
            An empty ``StopTransaction`` confirmation.

        Raises:
            ValueError: If a required field is not a number, ``timestamp`` has
                no timezone, or an energy-register sample cannot be read.
            ChargingStationNotFoundError: If the station is not provisioned.
            ChargingSessionNotFoundError: If this station has no such
                transaction.
            ChargingSessionStateError: If the session is already completed.

        Side Effects:
            Stores the samples, updates the session and appends its ``Ended``
            event in one atomic transaction; rolls back entirely on any error.
        """
        stopped_at = parse_ocpp_timestamp(timestamp)
        closing_meter_wh = to_decimal(meter_stop, "meter_stop")
        transaction_key = str(int(transaction_id))
        extraction = extract_v16_measurements(transaction_data or [])
        async with self.session_factory.begin() as db:
            station_id = await charging_stations_service.resolve_station_id_by_identity(
                db, ocpp_identity=self.id
            )
            reference = await charging_sessions_service.resolve_session_by_transaction(
                db, station_id=station_id, transaction_id=transaction_key
            )
            await self._store_extraction(db, reference.session_id, extraction)
            await charging_sessions_service.ingest_transaction_event(
                db,
                station_id=reference.station_id,
                evse_id=reference.evse_id,
                connector_id=reference.connector_id,
                transaction_id=transaction_key,
                event_type=SessionEventType.ENDED,
                event_occurred_at=stopped_at,
                seq_no=None,
                meter_end_wh=closing_meter_wh,
                meter_end_sampled_at=stopped_at,
                stop_reason=reason[:30] if reason else None,
                meter_stop_wh=closing_meter_wh,
            )
        self._log_skipped_samples("StopTransaction", extraction)
        return call_result.StopTransaction()

    @on(Action.meter_values, skip_schema_validation=True)  # type: ignore[untyped-decorator]
    async def on_meter_values(
        self,
        connector_id: int,
        meter_value: list[OcppPayload],
        transaction_id: int | None = None,
        **_: object,
    ) -> call_result.MeterValues:
        """Store the readings of a ``MeterValues`` message against its session.

        The session is found in the database by ``(station, transactionId)``,
        so a message that arrives on a new connection (after a reconnect) is
        handled exactly like one on the original connection. Energy-register
        samples update the session's energy total; every other measurand
        (``SoC``, power, voltage, current, temperature, ``Power.Offered``, and
        vendor-specific names) is stored as a measurement.

        JSON-schema validation is switched off for this action on purpose (the
        1.6 schema rejects any measurand, unit, context, phase or location
        outside fixed lists, which would discard a whole message over one
        vendor-specific sample); see ``ocpp16_measurements.py`` for the rules
        applied instead. Samples that cannot be stored are skipped, counted and
        logged as a warning.

        A message **without** a ``transactionId`` (clock-aligned samples or
        readings outside a transaction) belongs to no session, so nothing is
        stored beyond the raw message log; station-level metering is deferred
        (``future.md`` #77).

        Args:
            connector_id: The gun the readings belong to (not used to find the
                session; the ``transactionId`` is).
            meter_value: The sample groups (plain dicts).
            transaction_id: The transaction the readings belong to, if any.
            **_: Other optional OCPP fields.

        Returns:
            A valid empty response for MeterValues.

        Raises:
            KeyError: If a group lacks ``timestamp`` or ``sampled_value``.
            ValueError: If a timestamp lacks a timezone or an energy-register
                sample cannot be read.
            ChargingStationNotFoundError: If the station is not provisioned.
            ChargingSessionNotFoundError: If this station has no such
                transaction.
            ChargingSessionStateError: If the session is already completed.

        Side Effects:
            Appends the samples and updates the session in one atomic
            transaction; rolls back entirely on any error above.
        """
        extraction = extract_v16_measurements(meter_value)
        self._log_skipped_samples("MeterValues", extraction)
        if transaction_id is None:
            logger.debug(
                "MeterValues without a transactionId is not attributed to a session",
                extra={
                    "ocpp_identity": self.id,
                    "ocpp_connector_id": connector_id,
                    "sample_groups": len(meter_value),
                },
            )
            return call_result.MeterValues()
        async with self.session_factory.begin() as db:
            station_id = await charging_stations_service.resolve_station_id_by_identity(
                db, ocpp_identity=self.id
            )
            reference = await charging_sessions_service.resolve_session_by_transaction(
                db, station_id=station_id, transaction_id=str(int(transaction_id))
            )
            await self._store_extraction(db, reference.session_id, extraction)
        return call_result.MeterValues()

    async def _store_extraction(
        self, db: AsyncSession, session_id: UUID, extraction: V16Extraction
    ) -> None:
        """Store the samples of one message against a session.

        Args:
            db: The action's async session.
            session_id: UUID of the session that owns the samples.
            extraction: Energy samples and other measurements to store.

        Side Effects:
            Energy samples go through ``ingest_meter_values`` (aggregate update
            plus a measurement row), everything else through
            ``ingest_measurements``; both refuse a ``COMPLETED`` session.
        """
        for sample in extraction.energy:
            await charging_sessions_service.ingest_meter_values(
                db, session_id=session_id, sample=sample
            )
        if extraction.measurements:
            await charging_sessions_service.ingest_measurements(
                db, session_id=session_id, samples=extraction.measurements
            )

    def _log_skipped_samples(self, action: str, extraction: V16Extraction) -> None:
        """Log a warning when some samples of a message could not be stored.

        Args:
            action: The OCPP action name, for the log.
            extraction: The extraction whose skipped counts to report.
        """
        if extraction.skipped_count:
            logger.warning(
                "Skipped meter samples that cannot be stored",
                extra={
                    "ocpp_identity": self.id,
                    "action": action,
                    "skipped_samples": extraction.skipped_count,
                    "skipped_by_reason": extraction.skipped,
                },
            )
