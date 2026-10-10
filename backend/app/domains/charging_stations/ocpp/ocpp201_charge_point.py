"""OCPP 2.0.1 adapter for one connected charge point.

This module holds the adapter class for the ``ocpp2.0.1`` subprotocol
(``OCPP201ChargePoint``: ``BootNotification``, ``Heartbeat``,
``Authorize``, ``TransactionEvent``, ``MeterValues``, ``StatusNotification``) and its payload
helpers, mirroring
``ocpp16_charge_point.py``/``ocpp16_measurements.py`` for 1.6J. The two
adapters never share payload code: the protocols shape a ``SampledValue``
differently (2.0.1 has a nested ``unitOfMeasure`` with a multiplier, 1.6J a
flat ``unit``). Besides the charger's own messages, the adapter sends the commands of the
command loop (``send_command``, CS-20) and receives the ``NotifyReport`` parts
that answer a ``GET_CONFIGURATION`` command. Every other 2.0.1 action is
answered with ``CALLERROR NotImplemented`` by ``python-ocpp`` (the frame is
still stored by the raw message log).

``python-ocpp``'s ``ChargePoint._handle_call`` only snake_cases inbound JSON
keys and splats the result as handler kwargs - it never constructs the
``ocpp.v201.datatypes`` dataclasses. So nested OCPP objects (``evse``,
``transactionInfo``, ``meterValue`` and its ``sampledValue`` entries) always
arrive as plain ``dict``s at runtime, never as those dataclasses, regardless
of a handler's type annotation. Handlers here are written against that reality
(aliased ``OcppPayload``); don't re-introduce dataclass type hints for nested
OCPP objects.
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Final
from uuid import UUID

from ocpp.routing import on
from ocpp.v201 import ChargePoint, call, call_result
from ocpp.v201.enums import (
    Action,
    AuthorizationStatusEnumType,
    ConnectorStatusEnumType,
    RegistrationStatusEnumType,
    TransactionEventEnumType,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
from app.domains.charging_sessions.exceptions import ChargingSessionTokenError
from app.domains.charging_sessions.types import (
    DEFAULT_MEASUREMENT_CONTEXT,
    DEFAULT_MEASUREMENT_LOCATION,
    ENERGY_ACTIVE_IMPORT_REGISTER,
    STOP_REASON_MAX_LENGTH,
    MeasurementInput,
    MeterSampleInput,
)
from app.domains.charging_stations.ocpp.command_types import (
    CommandResult,
    OutboundCommand,
    to_command_result,
)
from app.domains.charging_stations.ocpp.measurement_units import (
    KNOWN_MEASURAND_UNITS,
    convert_measurement,
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
    ConfigurationMutability,
    ReportEntry,
    StationCommandOutcome,
    StationCommandType,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

# Subprotocol label this adapter serves; stored with each configuration snapshot.
OCPP201_PROTOCOL_VERSION: Final[str] = "ocpp2.0.1"
# Component a ``CHANGE_CONFIGURATION`` command addresses when it names none.
_DEFAULT_CONFIGURATION_COMPONENT: Final[str] = "OCPPCommCtrlr"
# 2.0.1 mutability labels of a report -> the table's mutability values.
_MUTABILITY_BY_REPORT: Final[dict[str, ConfigurationMutability]] = {
    "ReadOnly": ConfigurationMutability.READ_ONLY,
    "WriteOnly": ConfigurationMutability.WRITE_ONLY,
    "ReadWrite": ConfigurationMutability.READ_WRITE,
}
# Our Soft/Hard reset names -> 2.0.1 ResetEnumType.
_RESET_TYPE_BY_NAME: Final[dict[str, str]] = {
    "Soft": "OnIdle",
    "Hard": "Immediate",
    "OnIdle": "OnIdle",
    "Immediate": "Immediate",
}

# Wh is OCPP's default unit; kWh is the only other energy unit in the 2.0.1
# standardized list. Keys are lowercased for a case-insensitive match,
# since unitOfMeasure.unit is a free string, not an enum, in the spec.
_ENERGY_UNIT_FACTORS_WH: Final[dict[str, Decimal]] = {
    "wh": Decimal(1),
    "kwh": Decimal(1000),
}


def normalize_sampled_value_to_wh(sampled_value: OcppPayload) -> Decimal | None:
    """Convert one OCPP sampled value into a canonical Wh energy reading (F-B2).

    Rule:
        Only ``Energy.Active.Import.Register`` is a cumulative import
        register, and therefore the only measurand the session's declared
        ``meter_start_wh``/``meter_stop_wh`` can come from; anything else
        (power, SoC, temperature, an *interval* energy delta) is returned as
        ``None`` here and handled by ``extract_measurements``. An
        absent ``measurand`` means that register, per OCPP 2.0.1's own
        default. The reading is ``value * 10 ** multiplier`` in ``unit``,
        with ``unit`` defaulting to Wh and ``multiplier`` to 0 - the OCPP
        JSON Schema declares those defaults but validation doesn't inject
        them, so both are defaulted here.

    Args:
        sampled_value: One ``sampledValue`` object, snake_cased by
            ``python-ocpp`` into a plain dict - see the module docstring.

    Returns:
        The reading in Wh, or ``None`` if the measurand is not the import
        energy register (skip this sample, not an error).

    Raises:
        ValueError: If the measurand is the import energy register but its
            unit is neither Wh nor kWh - an uninterpretable register
            reading must fail loudly rather than be dropped, which would
            leave the aggregate stale while the station believes it
            reported successfully.
    """
    measurand = sampled_value.get("measurand") or ENERGY_ACTIVE_IMPORT_REGISTER
    if measurand != ENERGY_ACTIVE_IMPORT_REGISTER:
        return None

    unit_of_measure = sampled_value.get("unit_of_measure") or {}
    unit = str(unit_of_measure.get("unit") or "Wh").lower()
    multiplier = unit_of_measure.get("multiplier") or 0
    factor = _ENERGY_UNIT_FACTORS_WH.get(unit)
    if factor is None:
        raise ValueError(f"Unrecognized energy unit '{unit}' for {measurand}")

    raw_value = Decimal(str(sampled_value["value"]))
    return raw_value.scaleb(multiplier) * factor


def extract_meter_samples(
    meter_values: list[OcppPayload],
) -> list[MeterSampleInput]:
    """Convert the energy samples in an OCPP message into persistence input.

    Args:
        meter_values: Groups of samples, snake_cased by ``python-ocpp``
            into plain dicts - see the module docstring.

    Returns:
        Canonical Wh samples in payload order. Shorter than the payload,
        or empty, when the message carries non-energy measurands
        (F-B2) - see ``normalize_sampled_value_to_wh``.

    Raises:
        ValueError: If a timestamp lacks a timezone, or an energy-register
            sample uses an uninterpretable unit.
    """
    samples: list[MeterSampleInput] = []
    for meter_value in meter_values:
        sampled_at = parse_ocpp_timestamp(meter_value["timestamp"])
        for sampled_value in meter_value["sampled_value"]:
            value_wh = normalize_sampled_value_to_wh(sampled_value)
            if value_wh is None:
                continue
            samples.append(
                MeterSampleInput(
                    sampled_at=sampled_at,
                    value_wh=value_wh,
                    context=sampled_value.get("context") or DEFAULT_MEASUREMENT_CONTEXT,
                    measurement_location=(
                        sampled_value.get("location") or DEFAULT_MEASUREMENT_LOCATION
                    ),
                )
            )
    return samples


def extract_measurements(
    meter_values: list[OcppPayload],
) -> list[MeasurementInput]:
    """Convert the non-energy samples of an OCPP message into measurements.

    Every known measurand is converted to its fixed unit (CE-14); a vendor
    measurand is stored as sent. A missing context or location gets the OCPP
    default. A sample with a non-numeric value is skipped.

    Args:
        meter_values: Groups of samples, snake_cased by ``python-ocpp``
            into plain dicts - see the module docstring.

    Returns:
        The measurements in payload order, without the energy register.

    Raises:
        ValueError: If a timestamp lacks a timezone.
    """
    measurements: list[MeasurementInput] = []
    for meter_value in meter_values:
        sampled_at = parse_ocpp_timestamp(meter_value["timestamp"])
        for sampled_value in meter_value["sampled_value"]:
            measurand = sampled_value.get("measurand") or ENERGY_ACTIVE_IMPORT_REGISTER
            if measurand == ENERGY_ACTIVE_IMPORT_REGISTER:
                continue
            try:
                raw_value = Decimal(str(sampled_value["value"]))
            except (InvalidOperation, KeyError):
                continue
            if not raw_value.is_finite():
                continue
            unit_of_measure = sampled_value.get("unit_of_measure") or {}
            value, unit = convert_measurement(
                measurand,
                raw_value.scaleb(unit_of_measure.get("multiplier") or 0),
                unit_of_measure.get("unit") or KNOWN_MEASURAND_UNITS.get(measurand),
            )
            measurements.append(
                MeasurementInput(
                    sampled_at=sampled_at,
                    measurand=measurand,
                    value=value,
                    unit=unit,
                    context=sampled_value.get("context") or DEFAULT_MEASUREMENT_CONTEXT,
                    phase=sampled_value.get("phase"),
                    measurement_location=(
                        sampled_value.get("location") or DEFAULT_MEASUREMENT_LOCATION
                    ),
                )
            )
    return measurements


def parse_ocpp_transaction_id(transaction_info: OcppPayload) -> str:
    """Read the transaction identity out of a raw TransactionEvent payload.

    Args:
        transaction_info: The ``transactionInfo`` object, snake_cased by
            ``python-ocpp`` into a plain dict - see the module docstring.

    Returns:
        The OCPP transaction identity.

    Raises:
        ValueError: If the payload has no ``transaction_id``.
    """
    transaction_id = transaction_info.get("transaction_id")
    if not transaction_id:
        raise ValueError("transactionInfo must have a transactionId")
    return str(transaction_id)


def parse_ocpp_evse_reference(evse: OcppPayload | None) -> tuple[int, int]:
    """Read the OCPP EVSE and connector IDs out of a raw ``evse`` payload.

    Args:
        evse: The ``evse`` object, snake_cased by ``python-ocpp`` into a
            plain dict - see the module docstring.

    Returns:
        A ``(ocpp_evse_id, ocpp_connector_id)`` pair.

    Raises:
        ValueError: If the payload is missing the EVSE or the connector.
    """
    if evse is None or evse.get("id") is None or evse.get("connector_id") is None:
        raise ValueError("TransactionEvent must have an EVSE and connector")
    return int(evse["id"]), int(evse["connector_id"])


class OCPP201ChargePoint(ChargePoint):  # type: ignore[misc]
    """``python-ocpp`` 2.0.1 adapter attaching an accepted WebSocket to a station.

    Attributes:
        id: Station identity used by ``ChargePoint`` when dispatching OCPP.
        connection: Recording wrapper around the WebSocket connection created
            by ``websockets`` after the handshake; ``ChargePoint`` only calls
            its ``recv()``/``send()``.
        session_factory: Shared factory used for each persistence operation.
        _session_by_evse: Mapping from OCPP EVSE ID to session UUID, for
            MeterValues.

    Note:
        The class receives OCPP actions after the handshake, converts the
        payload into primitive values, and does not create the WebSocket
        connection itself.
    """

    protocol_version = OCPP201_PROTOCOL_VERSION

    def __init__(
        self,
        identity: str,
        connection: RecordingConnection,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Initialize the OCPP 2.0.1 adapter for an already validated connection.

        Args:
            identity: OCPP identity already resolved in the database.
            connection: Recording wrapper around the WebSocket connection that
                completed the handshake.
            session_factory: Shared factory owning the transaction for the
                action handler.

        Side Effects:
            Initializes the ``python-ocpp`` base class state, attaches the
            gateway logger and sets the response timeout for requests this
            backend sends (``CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS``).
        """
        super().__init__(
            identity,
            connection,
            response_timeout=settings.CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS,
            logger=logger,
        )
        self.session_factory = session_factory
        self._session_by_evse: dict[int, UUID] = {}

    @on(Action.boot_notification)  # type: ignore[untyped-decorator]
    async def on_boot_notification(
        self,
        charging_station: OcppPayload,
        reason: str,
        **_: object,
    ) -> call_result.BootNotification:
        """Record the charging station's identity and accept the boot (F-G2).

        Mirrors the 1.6J adapter: an unprovisioned station never gets here
        (the handshake rejects it), so the boot is always ``Accepted``.
        ``reason`` (``PowerUp``, ``FirmwareUpdate``...) is not stored; the
        frame itself stays in the raw message log.

        Args:
            charging_station: The ``chargingStation`` object, snake_cased by
                ``python-ocpp`` into a plain dict - see the module
                docstring. ``model`` and ``vendor_name`` are required by the
                2.0.1 schema; ``serial_number`` and ``firmware_version`` are
                optional; ``modem`` is not stored.
            reason: OCPP ``BootReasonEnumType`` label, not stored.
            **_: Other optional OCPP fields (``customData``).

        Returns:
            ``Accepted`` with the server's current time (the station syncs its
            clock from it) and the heartbeat interval in seconds
            (``CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS``).

        Raises:
            ChargingStationNotFoundError: If the station was soft-deleted
                after the handshake; ``python-ocpp`` then answers
                ``CALLERROR`` without closing the connection.

        Side Effects:
            Updates the station's device fields and ``last_boot_at`` in an
            atomic transaction (``ocpp_state_service.record_charger_boot``).
        """
        del reason
        booted_at = utc_now()
        async with self.session_factory.begin() as db:
            await ocpp_state_service.record_charger_boot(
                db,
                ocpp_identity=self.id,
                vendor=str(charging_station["vendor_name"]),
                model=str(charging_station["model"]),
                serial_number=charging_station.get("serial_number"),
                firmware_version=charging_station.get("firmware_version"),
                booted_at=booted_at,
            )
        return call_result.BootNotification(
            current_time=format_ocpp_timestamp(utc_now()),
            interval=settings.CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS,
            status=RegistrationStatusEnumType.accepted,
        )

    @on(Action.heartbeat)  # type: ignore[untyped-decorator]
    async def on_heartbeat(self, **_: object) -> call_result.Heartbeat:
        """Answer a heartbeat with the server's current time (F-G2).

        Liveness (``last_seen_at``) is already recorded for every inbound
        frame by the raw message log, so nothing else is stored here.

        Args:
            **_: A 2.0.1 ``Heartbeat`` has no fields besides ``customData``.

        Returns:
            The server's current UTC time.
        """
        return call_result.Heartbeat(current_time=format_ocpp_timestamp(utc_now()))

    @on(Action.authorize)  # type: ignore[untyped-decorator]
    async def on_authorize(
        self, id_token: OcppPayload, **_: object
    ) -> call_result.Authorize:
        """Accept only a token a QR scan issued for this charger (CE-11).

        Same rule as the 1.6J adapter: the token must belong to a PENDING
        session of this charger scanned inside the pending window, or to its
        ACTIVE session. Any other token is answered ``Invalid``.

        Args:
            id_token: The ``idToken`` object (``id_token`` and ``type``), as a
                plain dict; never log it (IS-07).
            **_: Optional certificate fields, unused.

        Returns:
            ``idTokenInfo`` ``Accepted`` for a token we issued, else ``Invalid``.

        Raises:
            ChargingStationNotFoundError: If the station is not provisioned.
        """
        token = str(id_token.get("id_token") or "")
        async with self.session_factory.begin() as db:
            station_id = await ocpp_state_service.resolve_station_id_by_identity(
                db, self.id
            )
            is_valid = await charging_sessions_service.is_start_token_valid(
                db, station_id=station_id, id_token=token
            )
        if not is_valid:
            logger.warning(
                "Authorize refused: no scan issued this token",
                extra={"ocpp_identity": self.id},
            )
        return call_result.Authorize(
            id_token_info={
                "status": (
                    AuthorizationStatusEnumType.accepted
                    if is_valid
                    else AuthorizationStatusEnumType.invalid
                )
            }
        )

    @on(Action.transaction_event)  # type: ignore[untyped-decorator]
    async def on_transaction_event(
        self,
        event_type: TransactionEventEnumType,
        timestamp: str,
        trigger_reason: object,
        seq_no: int,
        transaction_info: OcppPayload,
        meter_value: list[OcppPayload] | None = None,
        evse: OcppPayload | None = None,
        id_token: OcppPayload | None = None,
        **_: object,
    ) -> call_result.TransactionEvent:
        """Start, update or complete a session from a TransactionEvent, then ACK.

        ``Started`` must carry the single-use token a QR scan issued for this
        charger (``idToken``): it finds the PENDING session, which turns
        ``ACTIVE`` with the first energy reading as its start reading (CE-11).
        Any other token is answered ``Invalid`` and creates no session.
        ``Updated`` stores the event's samples; ``Ended`` stores them and
        completes the session with the last energy reading as the closing
        reading (``meter_stop_wh``, CE-12). The event itself is not stored:
        start and end live on the session, the full trail in the raw log
        (CE-15).

        Args:
            event_type: ``Started``, ``Updated``, or ``Ended``.
            timestamp: Time of the event per OCPP.
            trigger_reason: OCPP trigger, currently only parsed to preserve
                the contract.
            seq_no: OCPP sequence number; not stored (CE-15).
            transaction_info: The ``transactionInfo`` object, as a plain
                dict - see the module docstring.
            meter_value: Start/end meter values depending on the event, as
                plain dicts - see the module docstring.
            evse: OCPP EVSE and connector to resolve, as a plain dict.
            id_token: The ``idToken`` object (``id_token`` and ``type``), as a
                plain dict; never log it (IS-07).
            **_: Optional OCPP fields not part of the MVP.

        Returns:
            An empty response, or one carrying ``idTokenInfo`` ``Invalid`` for
            a start whose token no scan issued.

        Raises:
            KeyError: If ``event_type`` is not one of the three 2.0.1 values.
            ValueError: If ``transactionInfo`` has no ``transactionId``, the
                ``evse`` object lacks its EVSE or connector, a timestamp has
                no timezone, or an energy-register sample uses an
                uninterpretable unit.
            ChargingStationNotFoundError: If ``self.id`` is not
                pre-provisioned.
            ChargingEvseNotFoundError: If the EVSE is not pre-provisioned
                under this station.
            ChargingConnectorNotFoundError: If the connector is not
                pre-provisioned under that EVSE.
            ChargingSessionInputError: If the event violates the session
                contract (for example a start without a meter reading).
            ChargingSessionNotFoundError: If a non-``Started`` event names an
                unknown transaction.
            ChargingSessionStateError: If the session is already completed.

            ``python-ocpp`` turns any of these into a ``CALLERROR`` without
            closing the connection; the transaction rolls back entirely.

        Side Effects:
            Calls the public ``charging_sessions`` service within an atomic
            transaction; the EVSE -> session mapping is only updated after
            the transaction commits.
        """
        del trigger_reason, seq_no
        event_timestamp = parse_ocpp_timestamp(timestamp)
        energy_samples = extract_meter_samples(meter_value) if meter_value else []
        other_measurements = extract_measurements(meter_value) if meter_value else []
        transaction_id = parse_ocpp_transaction_id(transaction_info)
        ocpp_evse_id, ocpp_connector_id = parse_ocpp_evse_reference(evse)
        session_id: UUID
        try:
            async with self.session_factory.begin() as db:
                (
                    station_id,
                    evse_uuid,
                    connector_uuid,
                ) = await ocpp_state_service.resolve_ocpp_topology(
                    db, self.id, ocpp_evse_id, ocpp_connector_id
                )
                if event_type == TransactionEventEnumType.started:
                    token = (id_token or {}).get("id_token")
                    if not token:
                        raise ChargingSessionTokenError(
                            "The start event carries no idToken"
                        )
                    result = await charging_sessions_service.activate_pending_session(
                        db,
                        station_id=station_id,
                        evse_id=evse_uuid,
                        connector_id=connector_uuid,
                        id_token=str(token),
                        transaction_id=transaction_id,
                        started_at=event_timestamp,
                        meter_start_wh=(
                            energy_samples[0].value_wh if energy_samples else None
                        ),
                    )
                    session_id = result.session_id
                    if other_measurements:
                        await charging_sessions_service.ingest_measurements(
                            db, session_id=session_id, samples=other_measurements
                        )
                else:
                    reference = (
                        await charging_sessions_service.resolve_session_by_transaction(
                            db, station_id=station_id, transaction_id=transaction_id
                        )
                    )
                    session_id = reference.session_id
                    for sample in energy_samples:
                        await charging_sessions_service.ingest_meter_values(
                            db, session_id=session_id, sample=sample
                        )
                    if other_measurements:
                        await charging_sessions_service.ingest_measurements(
                            db, session_id=session_id, samples=other_measurements
                        )
                    if event_type == TransactionEventEnumType.ended:
                        await charging_sessions_service.complete_session(
                            db,
                            station_id=station_id,
                            evse_id=evse_uuid,
                            connector_id=connector_uuid,
                            transaction_id=transaction_id,
                            ended_at=event_timestamp,
                            stop_reason=(
                                str(transaction_info["stopped_reason"])[
                                    :STOP_REASON_MAX_LENGTH
                                ]
                                if transaction_info.get("stopped_reason")
                                else None
                            ),
                            meter_stop_wh=(
                                energy_samples[-1].value_wh if energy_samples else None
                            ),
                        )
        except ChargingSessionTokenError:
            logger.warning(
                "TransactionEvent refused: no scan issued its token",
                extra={"ocpp_identity": self.id},
            )
            return call_result.TransactionEvent(
                id_token_info={"status": AuthorizationStatusEnumType.invalid}
            )
        if event_type == TransactionEventEnumType.ended:
            self._session_by_evse.pop(ocpp_evse_id, None)
        else:
            self._session_by_evse[ocpp_evse_id] = session_id
        return call_result.TransactionEvent()

    @on(Action.meter_values)  # type: ignore[untyped-decorator]
    async def on_meter_values(
        self,
        evse_id: int,
        meter_value: list[OcppPayload],
        **_: object,
    ) -> call_result.MeterValues:
        """Persist each MeterValues sample within one transaction.

        Args:
            evse_id: OCPP EVSE ID used to look up the session on this
                connection.
            meter_value: Groups of samples, as plain dicts - see the
                module docstring - to convert to Wh.
            **_: Optional OCPP fields not part of the MVP.

        Returns:
            A valid empty response for MeterValues.

        Raises:
            KeyError: If no session was started for ``evse_id`` on this
                connection (see the comment below), or a sample group lacks
                ``timestamp``/``sampled_value``.
            ValueError: If a timestamp has no timezone or an energy-register
                sample uses an uninterpretable unit.
            ChargingSessionInputError: If a sample violates the session
                contract.
            ChargingSessionNotFoundError: If the mapped session no longer
                exists.
            ChargingSessionStateError: If the session is already completed.

        Side Effects:
            Calls ``ingest_meter_values`` for each sample on the same
            AsyncSession; an exception rolls back the entire message's
            entry transaction.
        """
        # Populated by on_transaction_event on this same connection and
        # popped on Ended. A missing key means MeterValues outside a
        # transaction or after a reconnect dropped the mapping; the
        # resulting KeyError -> CALLERROR is the intended MVP failure, and
        # surviving a reconnect is the reliability path (deferred.md item 27).
        session_id = self._session_by_evse[evse_id]
        samples = extract_meter_samples(meter_value)
        other_measurements = extract_measurements(meter_value)
        async with self.session_factory.begin() as db:
            for sample in samples:
                await charging_sessions_service.ingest_meter_values(
                    db, session_id=session_id, sample=sample
                )
            if other_measurements:
                await charging_sessions_service.ingest_measurements(
                    db, session_id=session_id, samples=other_measurements
                )
        return call_result.MeterValues()

    @on(Action.status_notification)  # type: ignore[untyped-decorator]
    async def on_status_notification(
        self,
        timestamp: str,
        connector_status: ConnectorStatusEnumType,
        evse_id: int,
        connector_id: int,
        **_: object,
    ) -> call_result.StatusNotification:
        """Record a connector's live status, then ACK the OCPP call (F-C2).

        Args:
            timestamp: Time of the status change per OCPP.
            connector_status: OCPP status label. Arrives as a plain ``str``
                at runtime despite the type annotation - ``python-ocpp``
                passes the parsed JSON straight through with no coercion.
            evse_id: OCPP EVSE ID owning the connector.
            connector_id: OCPP connector ID within the EVSE.
            **_: Optional OCPP fields not part of the MVP.

        Returns:
            A valid empty response for StatusNotification.

        Raises:
            ChargingStationNotFoundError: If ``self.id`` is not
                pre-provisioned.
            ChargingEvseNotFoundError: If ``evse_id`` is not pre-provisioned
                under this station.
            ChargingConnectorNotFoundError: If ``connector_id`` is not
                pre-provisioned under that EVSE.
            ValueError: If ``connector_status`` isn't one of OCPP 2.0.1's
                five status labels.

        Side Effects:
            Calls the internal OCPP state service within an atomic
            transaction; rolls back entirely on any of the above. No
            try/except here - ``python-ocpp`` already wraps every handler
            invocation, logs the traceback, and replies with a
            ``CALLERROR(InternalError)`` without closing the connection,
            the same de-facto contract ``on_transaction_event`` and
            ``on_meter_values`` already rely on. Standardizing this instead
            of relying on the framework default is deferred (see
            ``docs/decisions/deferred.md`` item 31).
        """
        async with self.session_factory.begin() as db:
            (
                _station_id,
                _evse_id,
                connector_uuid,
            ) = await ocpp_state_service.resolve_ocpp_topology(
                db, self.id, evse_id, connector_id
            )
            await ocpp_state_service.update_connector_status(
                db,
                connector_id=connector_uuid,
                status=ChargingConnectorStatus(connector_status),
                status_updated_at=parse_ocpp_timestamp(timestamp),
            )
        return call_result.StatusNotification()

    @on(Action.notify_report)  # type: ignore[untyped-decorator]
    async def on_notify_report(
        self,
        request_id: int,
        generated_at: str,
        seq_no: int,
        report_data: list[OcppPayload] | None = None,
        tbc: bool = False,
        **_: object,
    ) -> call_result.NotifyReport:
        """Store one part of a configuration report into its snapshot (CS-19).

        A ``GetBaseReport`` is answered in several ``NotifyReport`` parts that
        share the ``requestId`` of the request; the snapshot of the matching
        ``GET_CONFIGURATION`` command receives the parts' settings and becomes
        complete with the last part (``tbc`` false). A part nobody asked for
        (no pending snapshot with that ``requestId``) is acknowledged and
        dropped; the raw log keeps it.

        Args:
            request_id: The ``requestId`` of the ``GetBaseReport``.
            generated_at: When the part was generated; not stored (the arrival
                time is the capture time).
            seq_no: Sequence number of the part; not used (parts are inserted
                as they arrive).
            report_data: The reported components and variables, as plain dicts.
            tbc: ``True`` while more parts follow.
            **_: Optional OCPP fields not stored.

        Returns:
            A valid empty response for NotifyReport.

        Side Effects:
            Inserts the part's entries and, on the last part, closes the
            snapshot and its command in one atomic transaction.
        """
        del generated_at, seq_no
        entries = to_report_entries(report_data or [])
        async with self.session_factory.begin() as db:
            await ocpp_state_service.store_configuration_report_part(
                db,
                ocpp_identity=self.id,
                ocpp_request_id=request_id,
                entries=entries,
                is_last_part=not tbc,
                received_at=utc_now(),
            )
        return call_result.NotifyReport()

    async def send_command(self, command: OutboundCommand) -> CommandResult:
        """Send one command of the command loop as an OCPP 2.0.1 call (CS-20).

        The frame carries the command's message ID so it pairs with the row in
        the raw log. ``GET_CONFIGURATION`` sends ``GetBaseReport``; the settings
        arrive later in ``NotifyReport`` parts, so its snapshot stays pending
        and the command is accepted with the charger's verdict only.

        Args:
            command: The claimed command.

        Returns:
            The charger's verdict.

        Raises:
            TimeoutError: If the charger does not answer in time.
            OCPPError: If the charger answers with a ``CALLERROR``.
            ValueError: If the command lacks something its type needs.
        """
        parameters = command.parameters
        command_type = command.command_type
        payload: object
        if command_type is StationCommandType.REMOTE_START:
            if command.id_token is None:
                raise ValueError("REMOTE_START has no token to send")
            payload = call.RequestStartTransaction(
                id_token={"id_token": command.id_token, "type": "Central"},
                remote_start_id=int(
                    parameters.get("remote_start_id", command.command_id.int % 2**31)
                ),
                evse_id=command.ocpp_evse_id,
            )
        elif command_type is StationCommandType.REMOTE_STOP:
            if command.ocpp_transaction_id is None:
                raise ValueError("REMOTE_STOP has no transaction to stop")
            payload = call.RequestStopTransaction(
                transaction_id=command.ocpp_transaction_id
            )
        elif command_type is StationCommandType.UNLOCK_CONNECTOR:
            if command.ocpp_evse_id is None or command.ocpp_connector_id is None:
                raise ValueError("UNLOCK_CONNECTOR needs an EVSE and connector")
            payload = call.UnlockConnector(
                evse_id=command.ocpp_evse_id, connector_id=command.ocpp_connector_id
            )
        elif command_type is StationCommandType.RESET:
            payload = call.Reset(
                type=_RESET_TYPE_BY_NAME.get(
                    str(parameters.get("reset_type", "Soft")), "OnIdle"
                ),
                evse_id=command.ocpp_evse_id,
            )
        elif command_type is StationCommandType.CHANGE_AVAILABILITY:
            payload = call.ChangeAvailability(
                operational_status=str(parameters.get("availability", "Inoperative")),
                evse=(
                    None
                    if command.ocpp_evse_id is None
                    else {"id": command.ocpp_evse_id}
                ),
            )
        elif command_type is StationCommandType.CHANGE_CONFIGURATION:
            payload = call.SetVariables(
                set_variable_data=[
                    {
                        "attribute_value": str(parameters["value"]),
                        "component": {
                            "name": str(
                                parameters.get(
                                    "component_name", _DEFAULT_CONFIGURATION_COMPONENT
                                )
                            )
                        },
                        "variable": {"name": str(parameters["key"])},
                    }
                ]
            )
        elif command_type is StationCommandType.TRIGGER_MESSAGE:
            payload = call.TriggerMessage(
                requested_message=str(parameters["requested_message"]),
                evse=(
                    None
                    if command.ocpp_evse_id is None
                    else {"id": command.ocpp_evse_id}
                ),
            )
        else:
            return await self._send_get_base_report(command)
        response = await self.call(
            payload, suppress=False, unique_id=command.ocpp_message_id
        )
        if isinstance(response, call_result.SetVariables):
            # One CHANGE_CONFIGURATION sets one setting, so one result (CS-20).
            results = response.set_variable_result
            return to_command_result(
                results[0]["attribute_status"] if results else None
            )
        return to_command_result(getattr(response, "status", None))

    async def _send_get_base_report(self, command: OutboundCommand) -> CommandResult:
        """Run a ``GET_CONFIGURATION`` command: open the snapshot, ask for a report.

        Args:
            command: The claimed command.

        Returns:
            The charger's verdict on the request. An accepted request leaves
            the snapshot pending until its last ``NotifyReport`` part; a
            refused one fails the snapshot at once.

        Raises:
            TimeoutError: If the charger does not answer in time.
            OCPPError: If the charger answers with a ``CALLERROR``.
        """
        request_id = int(
            command.parameters.get("ocpp_request_id", command.command_id.int % 2**31)
        )
        async with self.session_factory.begin() as db:
            await ocpp_state_service.open_configuration_capture(
                db,
                command_id=command.command_id,
                reason=ConfigurationCaptureReason(
                    str(command.parameters.get("capture_reason", "ON_DEMAND"))
                ),
                ocpp_protocol_version=self.protocol_version,
                ocpp_request_id=request_id,
            )
        response = await self.call(
            call.GetBaseReport(request_id=request_id, report_base="FullInventory"),
            suppress=False,
            unique_id=command.ocpp_message_id,
        )
        result = to_command_result(getattr(response, "status", None))
        if result.outcome is not StationCommandOutcome.ACCEPTED:
            async with self.session_factory.begin() as db:
                await ocpp_state_service.fail_command(
                    db,
                    command_id=command.command_id,
                    outcome=result.outcome,
                    response_status=result.response_status,
                    answered_at=utc_now(),
                )
            return CommandResult(
                outcome=result.outcome,
                response_status=result.response_status,
                handled=True,
            )
        return result


def to_report_entries(report_data: list[OcppPayload]) -> list[ReportEntry]:
    """Convert the ``reportData`` of a ``NotifyReport`` part into storable entries.

    Args:
        report_data: Items of the part (component, variable and their
            attributes), snake_cased by ``python-ocpp`` into plain dicts.

    Returns:
        One entry per variable attribute; a variable that lists no attribute
        yields one ``Actual`` entry with no value. ``WriteOnly`` values are
        dropped (nothing is shown for them).
    """
    entries: list[ReportEntry] = []
    for item in report_data:
        component = item.get("component") or {}
        evse = component.get("evse") or {}
        variable = item.get("variable") or {}
        if not variable.get("name") or not component.get("name"):
            continue
        for attribute in item.get("variable_attribute") or [{}]:
            mutability = _MUTABILITY_BY_REPORT.get(
                str(attribute.get("mutability") or "ReadWrite"),
                ConfigurationMutability.READ_WRITE,
            )
            value = attribute.get("value")
            entries.append(
                ReportEntry(
                    variable_name=str(variable["name"]),
                    value=(
                        None
                        if value is None
                        or mutability is ConfigurationMutability.WRITE_ONLY
                        else str(value)
                    ),
                    mutability=mutability,
                    attribute_type=str(attribute.get("type") or "Actual"),
                    component_name=str(component["name"]),
                    component_instance=component.get("instance"),
                    ocpp_evse_id=evse.get("id"),
                    ocpp_connector_id=evse.get("connector_id"),
                    variable_instance=variable.get("instance"),
                )
            )
    return entries
