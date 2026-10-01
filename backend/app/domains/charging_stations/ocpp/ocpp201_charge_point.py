"""OCPP 2.0.1 adapter for one connected charge point.

This module holds the adapter class for the ``ocpp2.0.1`` subprotocol
(``OCPP201ChargePoint``: ``TransactionEvent``, ``MeterValues``,
``StatusNotification``) and its payload helpers, mirroring
``ocpp16_charge_point.py``/``ocpp16_measurements.py`` for 1.6J. The two
adapters never share payload code: the protocols shape a ``SampledValue``
differently (2.0.1 has a nested ``unitOfMeasure`` with a multiplier, 1.6J a
flat ``unit``). Every other 2.0.1 action is answered with ``CALLERROR
NotImplemented`` by ``python-ocpp`` (the frame is still stored by the raw
message log).

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
from decimal import Decimal
from typing import Final
from uuid import UUID

from ocpp.routing import on
from ocpp.v201 import ChargePoint, call_result
from ocpp.v201.enums import Action, ConnectorStatusEnumType, TransactionEventEnumType
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
from app.domains.charging_sessions.types import (
    ENERGY_ACTIVE_IMPORT_REGISTER,
    MeterSampleInput,
    SessionEventType,
)
from app.domains.charging_stations.ocpp.parsing import (
    OcppPayload,
    parse_ocpp_timestamp,
)
from app.domains.charging_stations.ocpp.raw_log import RecordingConnection
from app.domains.charging_stations.types import ChargingConnectorStatus

logger = logging.getLogger(__name__)

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
        register, and therefore the only measurand the session aggregate
        can use ``meter_start_wh``/``meter_end_wh`` from; anything else
        (power, SoC, temperature, an *interval* energy delta) is extra
        telemetry this MVP doesn't model and is skipped, not an error. An
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
            samples.append(MeterSampleInput(sampled_at=sampled_at, value_wh=value_wh))
    return samples


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
            Initializes the ``python-ocpp`` base class state and attaches
            the gateway logger.
        """
        super().__init__(identity, connection, logger=logger)
        self.session_factory = session_factory
        self._session_by_evse: dict[int, UUID] = {}

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
        **_: object,
    ) -> call_result.TransactionEvent:
        """Persist a TransactionEvent using primitive values, then ACK the OCPP call.

        Args:
            event_type: ``Started``, ``Updated``, or ``Ended``.
            timestamp: Time of the event per OCPP.
            trigger_reason: OCPP trigger, currently only parsed to preserve
                the contract.
            seq_no: OCPP sequence number, persisted on the event history
                row (F-B2) but not yet used for dedup/ordering.
            transaction_info: The ``transactionInfo`` object, as a plain
                dict - see the module docstring.
            meter_value: Start/end meter values depending on the event, as
                plain dicts - see the module docstring.
            evse: OCPP EVSE and connector to resolve, as a plain dict.
            **_: Optional OCPP fields not part of the MVP.

        Returns:
            A valid empty response for TransactionEvent.

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
                contract.
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
        del trigger_reason
        event_map = {
            TransactionEventEnumType.started: SessionEventType.STARTED,
            TransactionEventEnumType.updated: SessionEventType.UPDATED,
            TransactionEventEnumType.ended: SessionEventType.ENDED,
        }
        session_event = event_map[event_type]
        samples = extract_meter_samples(meter_value) if meter_value else []
        meter_start_wh = samples[0].value_wh if samples else None
        meter_end_wh = samples[-1].value_wh if samples else None
        meter_end_sampled_at = samples[-1].sampled_at if samples else None
        transaction_id = parse_ocpp_transaction_id(transaction_info)
        ocpp_evse_id, ocpp_connector_id = parse_ocpp_evse_reference(evse)
        async with self.session_factory.begin() as db:
            (
                station_id,
                evse_id,
                connector_id,
            ) = await ocpp_state_service.resolve_ocpp_topology(
                db, self.id, ocpp_evse_id, ocpp_connector_id
            )
            result = await charging_sessions_service.ingest_transaction_event(
                db,
                station_id=station_id,
                evse_id=evse_id,
                connector_id=connector_id,
                transaction_id=transaction_id,
                event_type=session_event,
                event_occurred_at=parse_ocpp_timestamp(timestamp),
                seq_no=seq_no,
                meter_start_wh=(
                    meter_start_wh
                    if session_event is SessionEventType.STARTED
                    else None
                ),
                meter_end_wh=(
                    meter_end_wh
                    if session_event is not SessionEventType.STARTED
                    else None
                ),
                meter_end_sampled_at=(
                    meter_end_sampled_at
                    if session_event is not SessionEventType.STARTED
                    else None
                ),
            )
        if session_event is SessionEventType.ENDED:
            if evse is not None:
                self._session_by_evse.pop(evse["id"], None)
        else:
            if evse is not None:
                self._session_by_evse[evse["id"]] = result.session_id
        return call_result.TransactionEvent()

    @on(Action.meter_values)  # type: ignore[untyped-decorator]
    async def on_meter_values(
        self,
        evse_id: int,
        meter_value: list[OcppPayload],
        **_: object,
    ) -> call_result.MeterValues:
        """Persist each MeterValues energy sample within one transaction.

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
        # surviving a reconnect is the reliability path (future.md item 27).
        session_id = self._session_by_evse[evse_id]
        samples = extract_meter_samples(meter_value)
        async with self.session_factory.begin() as db:
            for sample in samples:
                await charging_sessions_service.ingest_meter_values(
                    db, session_id=session_id, sample=sample
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
            ``docs/01-requirements/future.md`` item 31).
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
