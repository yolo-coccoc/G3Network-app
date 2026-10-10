"""OCPP 1.6J adapter for one connected charge point.

This module holds the adapter class for the ``ocpp1.6`` subprotocol. It is
deliberately separate from the OCPP 2.0.1 adapter: the two protocols have
incompatible payload shapes (1.6J has no EVSE level, no ``TransactionEvent``,
and reports units in a flat ``unit`` field), and mixing them in one class would
hide which contract each handler assumes.

Scope: handlers are added one message group at a time by the OCPP 1.6J
planner (``docs/planners/backend-ocpp16-charger-integration.md``). Currently
handled: ``BootNotification``, ``Heartbeat``, ``StatusNotification``,
``Authorize``, ``StartTransaction``, ``StopTransaction`` and ``MeterValues``.
After every accepted ``BootNotification`` the adapter itself asks the charger for
its configuration (``GetConfiguration``, CO-05) and stores the answer as a
snapshot of its own ``GET_CONFIGURATION`` command. The other requests this
backend sends are the commands of the command loop (``send_command``: remote
start/stop, unlock, reset, availability, configuration change, trigger
message; CS-20). Every other action a 1.6J
charger sends is answered with ``CALLERROR NotImplemented`` by ``python-ocpp``
(the frame is still stored by the raw message log).
"""

import asyncio
import logging
from typing import Final
from uuid import UUID, uuid4

from ocpp.exceptions import OCPPError
from ocpp.routing import after, on
from ocpp.v16 import ChargePoint, call, call_result
from ocpp.v16.enums import Action, AuthorizationStatus, RegistrationStatus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
from app.domains.charging_sessions.exceptions import ChargingSessionTokenError
from app.domains.charging_sessions.types import STOP_REASON_MAX_LENGTH
from app.domains.charging_stations.ocpp.command_types import (
    CommandResult,
    OutboundCommand,
    to_command_result,
)
from app.domains.charging_stations.ocpp.library_logging import OCPP_LIBRARY_LOGGER
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
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ConfigurationCaptureReason,
    ConfigurationEntry,
    StationCommandOutcome,
    StationCommandType,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

# Subprotocol label this adapter serves; stored with each configuration snapshot.
OCPP16_PROTOCOL_VERSION: Final[str] = "ocpp1.6"


class OCPP16ChargePoint(ChargePoint):  # type: ignore[misc]
    """``python-ocpp`` adapter attaching an accepted 1.6J WebSocket to a station.

    Attributes:
        id: Station identity used by ``ChargePoint`` when dispatching OCPP.
        connection: Recording wrapper around the WebSocket connection created
            by ``websockets`` after the handshake; ``ChargePoint`` only calls
            its ``recv()``/``send()``.
        session_factory: Shared factory used for each persistence operation
            (the entry boundary owns every transaction).
        _configuration_task: The running post-boot ``GetConfiguration`` capture,
            if any. Kept so it can be cancelled when the connection closes.

    Note:
        The class receives OCPP actions after the handshake and does not
        create the WebSocket connection itself.
    """

    protocol_version = OCPP16_PROTOCOL_VERSION

    def __init__(
        self,
        identity: str,
        connection: RecordingConnection,
        session_factory: async_sessionmaker[AsyncSession],
        station_id: UUID | None = None,
    ) -> None:
        """Initialize the OCPP 1.6J adapter for an already validated connection.

        Args:
            identity: OCPP identity already resolved in the database.
            connection: Recording wrapper around the WebSocket connection that
                completed the handshake.
            session_factory: Shared factory owning the transaction for each
                action handler.
            station_id: The charger resolved at the handshake, if known.

        Side Effects:
            Initializes the ``python-ocpp`` base class state, attaches the
            frame-redacting library logger (RV-CS1), and sets the response timeout for requests this
            backend sends (``CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS``).
        """
        super().__init__(
            identity,
            connection,
            response_timeout=settings.CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS,
            logger=OCPP_LIBRARY_LOGGER,
        )
        self.session_factory = session_factory
        # The charger the gateway resolved at the handshake. Every message of
        # this connection addresses it by ID, so renaming the identity string
        # cannot redirect an open connection (RV-CS9). ``None`` (tests, tools)
        # means: resolve it by identity on first use.
        self._station_id = station_id
        self._configuration_task: asyncio.Task[None] | None = None

    async def _resolve_station_id(self, db: AsyncSession) -> UUID:
        """Return the charger's internal ID, resolving it by identity once if unset.

        Args:
            db: The handler's open session.

        Returns:
            The station ID fixed at the handshake (or resolved on first use).

        Raises:
            ChargingStationNotFoundError: If the station is unknown or deleted.
        """
        if self._station_id is None:
            self._station_id = await ocpp_state_service.resolve_station_id_by_identity(
                db, self.id
            )
        return self._station_id

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
        booted_at = utc_now()
        async with self.session_factory.begin() as db:
            await ocpp_state_service.record_charger_boot(
                db,
                station_id=await self._resolve_station_id(db),
                vendor=charge_point_vendor,
                model=charge_point_model,
                serial_number=charge_point_serial_number or charge_box_serial_number,
                firmware_version=firmware_version,
                booted_at=booted_at,
            )
        return call_result.BootNotification(
            current_time=format_ocpp_timestamp(utc_now()),
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
        return call_result.Heartbeat(current_time=format_ocpp_timestamp(utc_now()))

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
            parse_ocpp_timestamp(timestamp) if timestamp is not None else utc_now()
        )
        async with self.session_factory.begin() as db:
            if connector_id == 0:
                await ocpp_state_service.update_charger_status(
                    db,
                    station_id=await self._resolve_station_id(db),
                    status=reported_status,
                    status_updated_at=reported_at,
                    error_code=error_code,
                    vendor_error_code=vendor_error_code,
                )
            else:
                (
                    _station_id,
                    _evse_id,
                    connector_uuid,
                ) = await ocpp_state_service.resolve_ocpp16_topology(
                    db, await self._resolve_station_id(db), connector_id
                )
                await ocpp_state_service.update_connector_status(
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
        """Accept only a tag a QR scan issued for this charger (CE-11).

        The tag must belong to a PENDING session of this charger scanned inside
        the pending window, or to its ACTIVE session (the same tag shown again
        to stop at the screen). Any other tag is answered ``Invalid`` and
        stays in the raw log only.

        Args:
            id_tag: The tag presented at the charger; never log it (IS-07).
            **_: A 1.6 ``Authorize`` has no other fields.

        Returns:
            ``Accepted`` for a token we issued, otherwise ``Invalid``.

        Raises:
            ChargingStationNotFoundError: If the station is not provisioned.
        """
        async with self.session_factory.begin() as db:
            station_id = await self._resolve_station_id(db)
            is_valid = await charging_sessions_service.is_start_token_valid(
                db, station_id=station_id, id_token=id_tag
            )
        if not is_valid:
            logger.warning(
                "Authorize refused: no scan issued this tag",
                extra={"ocpp_identity": self.id},
            )
        return call_result.Authorize(
            id_tag_info={
                "status": (
                    AuthorizationStatus.accepted
                    if is_valid
                    else AuthorizationStatus.invalid
                )
            }
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
        """Start the PENDING session holding the charger's token (CE-11).

        OCPP 1.6J requires the CSMS to assign the integer ``transactionId``;
        it comes from a database sequence (decision D6) and is stored, as text,
        in the session's ``ocpp_transaction_id``. The ``idTag`` must be the
        single-use token a QR scan issued for this charger: it finds the
        PENDING session, which turns ``ACTIVE``. Any other tag is answered
        ``Invalid``, creates no session and stays in the raw log only.

        If the connector already has an ``ACTIVE`` session (a charger that
        rebooted mid-session, for example), a structured warning is logged and
        the old session is left untouched: orphan handling waits for real
        charger logs (decision D14).

        Args:
            connector_id: The gun number (``>= 1``; gun ``n`` is EVSE ``n`` /
                connector ``1``).
            id_tag: The tag that started the session; never log it (IS-07).
            meter_start: Meter reading at the start, an integer in Wh.
            timestamp: Start time (must carry a timezone).
            reservation_id: Reservation that led to this session; not stored.
            **_: Other optional OCPP fields.

        Returns:
            ``Accepted`` with the newly allocated ``transactionId``, or
            ``Invalid`` (transaction ID ``0``) for a token no scan issued.

        Raises:
            ValueError: If ``timestamp`` has no timezone.
            ChargingOcppMessageInputError: If ``connector_id`` is ``0``.
            ChargingStationNotFoundError: If the station is not provisioned.
            ChargingEvseNotFoundError: If the gun's EVSE is not provisioned.
            ChargingConnectorNotFoundError: If the gun's connector is not
                provisioned.
            ChargingSessionInputError: If the tag or meter value is invalid.

        Side Effects:
            Allocates an ID and activates the session in one atomic
            transaction; everything rolls back on any error above (the ID
            number is then skipped, which is harmless).
        """
        started_at = parse_ocpp_timestamp(timestamp)
        station_id: UUID | None = None
        try:
            async with self.session_factory.begin() as db:
                (
                    station_id,
                    evse_id,
                    connector_uuid,
                ) = await ocpp_state_service.resolve_ocpp16_topology(
                    db, await self._resolve_station_id(db), connector_id
                )
                if await charging_sessions_service.has_active_session_on_connector(
                    db, connector_uuid
                ):
                    logger.warning(
                        "StartTransaction on a connector that already has an "
                        "active session",
                        extra={
                            "ocpp_identity": self.id,
                            "ocpp_connector_id": connector_id,
                        },
                    )
                transaction_id = (
                    await charging_sessions_service.allocate_ocpp16_transaction_id(db)
                )
                await charging_sessions_service.activate_pending_session(
                    db,
                    station_id=station_id,
                    evse_id=evse_id,
                    connector_id=connector_uuid,
                    id_token=id_tag,
                    transaction_id=str(transaction_id),
                    started_at=started_at,
                    meter_start_wh=to_decimal(meter_start, "meter_start"),
                )
        except ChargingSessionTokenError:
            retried_transaction_id = await self._find_transaction_started_by_token(
                station_id, id_tag
            )
            if retried_transaction_id is not None:
                # The charger sent the start again because it never got our
                # answer: give it the transaction identity it was given before.
                return call_result.StartTransaction(
                    transaction_id=retried_transaction_id,
                    id_tag_info={"status": AuthorizationStatus.accepted},
                )
            logger.warning(
                "StartTransaction refused: no scan issued its token",
                extra={"ocpp_identity": self.id, "ocpp_connector_id": connector_id},
            )
            return call_result.StartTransaction(
                transaction_id=0,
                id_tag_info={"status": AuthorizationStatus.invalid},
            )
        return call_result.StartTransaction(
            transaction_id=transaction_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    async def _find_transaction_started_by_token(
        self, station_id: UUID | None, id_tag: str
    ) -> int | None:
        """Look up the transaction a token already started on this charger.

        Args:
            station_id: The charger's internal ID, or ``None`` when the
                message failed before the topology was resolved.
            id_tag: The token of the start message; never log it (IS-07).

        Returns:
            The 1.6J ``transactionId`` already given for the token, or
            ``None`` when no ACTIVE session holds it.
        """
        if station_id is None:
            return None
        async with self.session_factory.begin() as db:
            started = await charging_sessions_service.find_started_session_by_token(
                db, station_id=station_id, id_token=id_tag
            )
        if started is None or not started.ocpp_transaction_id.isdigit():
            return None
        return int(started.ocpp_transaction_id)

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
        ``meterStop`` is always stored as ``meter_stop_wh``, the billing figure
        (CE-12). Meter values sampled during the session (``transactionData``)
        are stored first, while the session is still ``ACTIVE``.

        JSON-schema validation is switched off for this action on purpose: the
        1.6 schema restricts ``reason`` and every sampled-value field to fixed
        lists, so a single vendor-specific stop reason or measurand would make
        the library reject the whole message and leave the session open forever.
        The fields the backend relies on are validated here instead, and a
        ``reason`` longer than its column (``STOP_REASON_MAX_LENGTH``) is
        truncated (the raw frame in the message log keeps the original).

        Args:
            meter_stop: Meter reading at the end, in Wh.
            timestamp: Stop time (must carry a timezone).
            transaction_id: The ID this backend allocated in
                ``StartTransaction``.
            reason: Why the session stopped (``EmergencyStop``,
                ``EVDisconnected``, a vendor value…), stored as sent.
            id_tag: Tag that stopped the session; not stored.
            transaction_data: Meter values sampled during the session, stored
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
            Stores the samples and completes the session in one atomic
            transaction; rolls back entirely on any error.
        """
        stopped_at = parse_ocpp_timestamp(timestamp)
        closing_meter_wh = to_decimal(meter_stop, "meter_stop")
        transaction_key = str(int(transaction_id))
        extraction = extract_v16_measurements(transaction_data or [])
        async with self.session_factory.begin() as db:
            station_id = await self._resolve_station_id(db)
            reference = await charging_sessions_service.resolve_session_by_transaction(
                db, station_id=station_id, transaction_id=transaction_key
            )
            await self._store_extraction(db, reference.session_id, extraction)
            await charging_sessions_service.complete_session(
                db,
                station_id=reference.station_id,
                evse_id=reference.evse_id,
                connector_id=reference.connector_id,
                transaction_id=transaction_key,
                ended_at=stopped_at,
                stop_reason=reason[:STOP_REASON_MAX_LENGTH] if reason else None,
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
        handled exactly like one on the original connection. Every measurand
        (the energy register, ``SoC``, power, voltage, current, temperature,
        ``Power.Offered``, and vendor-specific names) is stored as a
        measurement, in its fixed unit (CE-14).

        JSON-schema validation is switched off for this action on purpose (the
        1.6 schema rejects any measurand, unit, context, phase or location
        outside fixed lists, which would discard a whole message over one
        vendor-specific sample); see ``ocpp16_measurements.py`` for the rules
        applied instead. Samples that cannot be stored are skipped, counted and
        logged as a warning.

        A message **without** a ``transactionId`` (clock-aligned samples or
        readings outside a transaction) belongs to no session, so nothing is
        stored beyond the raw message log; station-level metering is deferred
        (``deferred.md`` #77).

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
            Appends the samples in one atomic transaction; rolls back entirely
            on any error above.
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
            station_id = await self._resolve_station_id(db)
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
            Energy samples go through ``ingest_meter_values``, everything else
            through ``ingest_measurements``; both refuse a session that is not
            ``ACTIVE``.
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

    @after(Action.boot_notification)  # type: ignore[untyped-decorator]
    def after_boot_notification(self, **_: object) -> None:
        """Start capturing the charger's configuration once the boot is answered.

        ``python-ocpp`` runs this hook only **after** the ``BootNotification``
        response has been sent. The request is scheduled as a separate task and
        never awaited here: the library's receive loop is sequential, so a
        handler that awaited its own ``call()`` would block the loop that must
        read the charger's answer and deadlock until the timeout. A capture that
        is still running from an earlier boot is cancelled first.

        Args:
            **_: The ``BootNotification`` payload fields; not used.

        Side Effects:
            Creates one asyncio task, remembered in ``_configuration_task`` and
            cancelled by ``cancel_background_tasks`` when the connection closes.
        """
        if self._configuration_task is not None and not self._configuration_task.done():
            self._configuration_task.cancel()
        self._configuration_task = asyncio.create_task(self._capture_configuration())

    async def _capture_configuration(self) -> None:
        """Ask the charger for its full configuration and store the answer.

        The system's own ``GET_CONFIGURATION`` command (reason ``BOOT``,
        CS-21) is written first, holding the message ID of the frame, so even
        a request the charger never answers leaves a record. Sends
        ``GetConfiguration`` with **no key**, which makes the charger return
        every key it supports (including ``SupportedFeatureProfiles``, its own
        answer to which OCPP profiles it implements). The call uses
        ``suppress=False`` so a ``CALLERROR`` is raised instead of being
        mistaken for an empty answer.

        This is a task boundary: nothing awaits the task, so a failure is logged
        here (``logger.exception``) and never affects the connection. A charger
        that refuses or does not answer within
        ``CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS`` is logged as a warning, and
        the command and its snapshot are closed as failed. ``CancelledError``
        is never swallowed.

        Side Effects:
            Sends one request; writes the command and snapshot in their own
            short transactions.
        """
        try:
            ocpp_message_id = str(uuid4())
            async with self.session_factory.begin() as db:
                command_id = await ocpp_state_service.start_boot_configuration_command(
                    db,
                    station_id=await self._resolve_station_id(db),
                    ocpp_protocol_version=self.protocol_version,
                    ocpp_message_id=ocpp_message_id,
                )
            try:
                response = await self.call(
                    call.GetConfiguration(), suppress=False, unique_id=ocpp_message_id
                )
            except (OCPPError, TimeoutError) as error:
                logger.warning(
                    "Charger did not answer GetConfiguration",
                    extra={
                        "ocpp_identity": self.id,
                        "error_type": type(error).__name__,
                    },
                )
                async with self.session_factory.begin() as db:
                    await ocpp_state_service.fail_command(
                        db,
                        command_id=command_id,
                        outcome=(
                            StationCommandOutcome.TIMEOUT
                            if isinstance(error, TimeoutError)
                            else StationCommandOutcome.ERROR
                        ),
                        response_status=(
                            None
                            if isinstance(error, TimeoutError)
                            else type(error).__name__
                        ),
                        answered_at=utc_now(),
                    )
                return
            await self._store_configuration_answer(command_id, response)
        except Exception:
            logger.exception(
                "Configuration capture failed", extra={"ocpp_identity": self.id}
            )

    async def _store_configuration_answer(
        self, command_id: UUID, response: call_result.GetConfiguration
    ) -> None:
        """Store a ``GetConfiguration`` answer into the command's snapshot.

        Args:
            command_id: The ``GET_CONFIGURATION`` command that was answered.
            response: The parsed answer.

        Side Effects:
            Inserts the entries and closes command and snapshot in one atomic
            transaction.
        """
        entries = _to_configuration_entries(response.configuration_key)
        if response.unknown_key:
            logger.info(
                "Charger reported unknown configuration keys",
                extra={
                    "ocpp_identity": self.id,
                    "unknown_keys": response.unknown_key,
                },
            )
        async with self.session_factory.begin() as db:
            await ocpp_state_service.complete_configuration_capture(
                db,
                command_id=command_id,
                entries=entries,
                captured_at=utc_now(),
            )

    async def send_command(self, command: OutboundCommand) -> CommandResult:
        """Send one command of the command loop as an OCPP 1.6J call (CS-20).

        The frame carries the command's message ID so it pairs with the row in
        the raw log. Gun ``n`` is EVSE ``n`` (CS-03), so the EVSE number is the
        1.6J ``connectorId``; a command with no EVSE addresses the whole
        charger (connector ``0``).

        Args:
            command: The claimed command.

        Returns:
            The charger's verdict; a ``GET_CONFIGURATION`` command is written
            back with its snapshot (``handled=True``).

        Raises:
            TimeoutError: If the charger does not answer in time.
            OCPPError: If the charger answers with a ``CALLERROR``.
            ValueError: If the command lacks something its type needs (the
                service validated the parameters, so this is a data error).
        """
        gun = command.ocpp_evse_id if command.ocpp_evse_id is not None else 0
        parameters = command.parameters
        command_type = command.command_type
        payload: object
        if command_type is StationCommandType.REMOTE_START:
            if command.id_token is None:
                raise ValueError("REMOTE_START has no token to send")
            payload = call.RemoteStartTransaction(
                id_tag=command.id_token,
                connector_id=command.ocpp_evse_id,
            )
        elif command_type is StationCommandType.REMOTE_STOP:
            if command.ocpp_transaction_id is None:
                raise ValueError("REMOTE_STOP has no transaction to stop")
            payload = call.RemoteStopTransaction(
                transaction_id=int(command.ocpp_transaction_id)
            )
        elif command_type is StationCommandType.UNLOCK_CONNECTOR:
            payload = call.UnlockConnector(connector_id=gun)
        elif command_type is StationCommandType.RESET:
            payload = call.Reset(type=str(parameters.get("reset_type", "Soft")))
        elif command_type is StationCommandType.CHANGE_AVAILABILITY:
            payload = call.ChangeAvailability(
                connector_id=gun,
                type=str(parameters.get("availability", "Inoperative")),
            )
        elif command_type is StationCommandType.CHANGE_CONFIGURATION:
            payload = call.ChangeConfiguration(
                key=str(parameters["key"]), value=str(parameters["value"])
            )
        elif command_type is StationCommandType.TRIGGER_MESSAGE:
            payload = call.TriggerMessage(
                requested_message=str(parameters["requested_message"]),
                connector_id=command.ocpp_evse_id,
            )
        else:
            return await self._send_get_configuration(command)
        response = await self.call(
            payload, suppress=False, unique_id=command.ocpp_message_id
        )
        return to_command_result(getattr(response, "status", None))

    async def _send_get_configuration(self, command: OutboundCommand) -> CommandResult:
        """Run a ``GET_CONFIGURATION`` command: ask, then store the snapshot.

        Args:
            command: The claimed command.

        Returns:
            An accepted result already written back (``handled=True``).

        Raises:
            TimeoutError: If the charger does not answer in time.
            OCPPError: If the charger answers with a ``CALLERROR``.
        """
        async with self.session_factory.begin() as db:
            await ocpp_state_service.open_configuration_capture(
                db,
                command_id=command.command_id,
                reason=ConfigurationCaptureReason(
                    str(command.parameters.get("capture_reason", "ON_DEMAND"))
                ),
                ocpp_protocol_version=self.protocol_version,
            )
        key = command.parameters.get("keys")
        response = await self.call(
            call.GetConfiguration(key=list(key) if isinstance(key, list) else None),
            suppress=False,
            unique_id=command.ocpp_message_id,
        )
        await self._store_configuration_answer(command.command_id, response)
        return CommandResult(
            outcome=StationCommandOutcome.ACCEPTED,
            response_status="Accepted",
            handled=True,
        )

    async def cancel_background_tasks(self) -> None:
        """Cancel the post-boot capture if it is still running.

        Called when the connection closes so no task outlives its connection.

        Side Effects:
            Cancels the task and waits for it to finish.
        """
        task = self._configuration_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _to_configuration_entries(
    configuration_key: list[OcppPayload] | None,
) -> list[ConfigurationEntry]:
    """Convert a ``GetConfiguration`` answer into storable entries.

    Args:
        configuration_key: The ``configurationKey`` list (plain dicts with
            ``key``, ``readonly`` and an optional ``value``), or ``None``.

    Returns:
        One entry per item that names a key; an item without a key is skipped.
        A missing ``value`` stays ``None`` and a missing ``readonly`` is
        ``False``.
    """
    entries: list[ConfigurationEntry] = []
    for item in configuration_key or []:
        key = item.get("key")
        if not key:
            continue
        value = item.get("value")
        entries.append(
            ConfigurationEntry(
                key=str(key),
                value=None if value is None else str(value),
                is_readonly=bool(item.get("readonly", False)),
            )
        )
    return entries
