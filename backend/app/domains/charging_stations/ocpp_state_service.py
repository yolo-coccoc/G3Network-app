"""Internal service for the state OCPP chargers report: used only by ``ocpp/``.

``service.py`` is the domain's public interface (topology CRUD, the
directory/nearby searches, the configuration read). This module holds the
business rules behind the OCPP gateway's writes, which no other domain may
call: resolving an OCPP identity/EVSE/connector into internal IDs (both
protocols), appending raw frames to the message log, recording boot identity,
charger (connector ``0``) and connector status in the state tables, and the
configuration snapshots (each from one ``GET_CONFIGURATION`` command, CS-21).

Every function runs inside a transaction owned by the gateway's entry boundary
(the adapter's per-message transaction, or the raw log's own one) and never
commits or rolls back. OCPP never creates topology: an identity, EVSE or
connector that was not pre-provisioned is an error. Every timestamp must carry
a timezone (decision D11 of the OCPP 1.6J planner) and is stored in UTC.
"""

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingEvseNotFoundError,
    ChargingOcppMessageInputError,
    ChargingStationNotFoundError,
)
from app.domains.charging_stations.models import (
    OCPP_SUBPROTOCOL_MAX_LENGTH,
    ChargingStationModel,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ConfigurationCaptureOutcome,
    ConfigurationCaptureReason,
    ConfigurationEntry,
    ConfigurationMutability,
    OcppMessageDirection,
    ReportEntry,
    StationCommandOutcome,
    StationCommandType,
)

# Width of ``charging_ocpp_messages.ocpp_message_id`` and of the commands'.
_MESSAGE_ID_MAX_LENGTH = 36
# OCPP-J message type of a request (CALL); answers and errors have no action.
_OCPP_CALL_TYPE = 2

logger = logging.getLogger(__name__)


def _read_envelope(raw_frame: str) -> tuple[str | None, str | None]:
    """Read the message ID and the action out of an OCPP-J frame (CS-18).

    Args:
        raw_frame: The exact frame text.

    Returns:
        ``(action, ocpp_message_id)``. The message ID is on every readable
        frame (``[type, id, ...]``); the action only on a request
        (``[2, id, action, payload]``). Both are ``None`` for a frame that
        cannot be read; the raw frame stays the record either way.
    """
    try:
        frame = json.loads(raw_frame)
    except ValueError:
        return None, None
    if not isinstance(frame, list) or len(frame) < 2:
        return None, None
    message_id = str(frame[1])[:_MESSAGE_ID_MAX_LENGTH]
    action = None
    if frame[0] == _OCPP_CALL_TYPE and len(frame) >= 3 and isinstance(frame[2], str):
        action = frame[2][:50]
    return action, message_id


def _to_utc(value: datetime, field_name: str) -> datetime:
    """Require a timezone-aware timestamp and convert it to UTC.

    Args:
        value: The timestamp to check.
        field_name: Name used in the error message.

    Returns:
        ``value`` converted to UTC.

    Raises:
        ChargingOcppMessageInputError: If ``value`` has no timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ChargingOcppMessageInputError(f"{field_name} must have a timezone")
    return value.astimezone(timezone.utc)


async def _get_active_station_by_identity(
    db: AsyncSession, ocpp_identity: str
) -> ChargingStationModel:
    """Load the active (not soft-deleted) station behind an OCPP identity.

    Args:
        db: Async session owned by the OCPP entry boundary.
        ocpp_identity: Station identity from the WebSocket path.

    Returns:
        The station record.

    Raises:
        ChargingStationNotFoundError: If the station has not been
            pre-provisioned or was soft-deleted.
    """
    station = await charging_stations_repository.get_station_by_identity(
        db, ocpp_identity, include_deleted=False
    )
    if station is None:
        raise ChargingStationNotFoundError(f"OCPP station '{ocpp_identity}' not found")
    return station


async def resolve_station_id_by_identity(db: AsyncSession, ocpp_identity: str) -> UUID:
    """Resolve an OCPP identity into the station's internal ID.

    Args:
        db: Async session owned by the OCPP entry boundary.
        ocpp_identity: Station identity from the WebSocket path.

    Returns:
        The internal UUID of the active station.

    Raises:
        ChargingStationNotFoundError: If the station is not pre-provisioned or
            was soft-deleted.
    """
    station = await _get_active_station_by_identity(db, ocpp_identity)
    return station.station_id


async def resolve_ocpp_topology(
    db: AsyncSession,
    ocpp_identity: str,
    ocpp_evse_id: int,
    ocpp_connector_id: int | None,
) -> tuple[UUID, UUID, UUID]:
    """Resolve an OCPP station/EVSE/connector identity into internal IDs.

    Args:
        db: Async session owned by the OCPP entry boundary.
        ocpp_identity: Station identity from the WebSocket path.
        ocpp_evse_id: EVSE ID in the OCPP message.
        ocpp_connector_id: Connector ID in the OCPP message; ``None`` when the
            message omitted it (``EVSEType.connectorId`` is optional in
            2.0.1), which means the EVSE's single connector (RV-CS2).

    Returns:
        Tuple ``(station_id, evse_id, connector_id)`` to pass to the
        ``charging_sessions`` domain without exposing the ORM model.

    Raises:
        ChargingStationNotFoundError: If the station has not been
            pre-provisioned or was soft-deleted.
        ChargingEvseNotFoundError: If the EVSE does not belong to an active
            station.
        ChargingConnectorNotFoundError: If the connector does not belong to
            an active EVSE.
    """
    station = await _get_active_station_by_identity(db, ocpp_identity)
    evse = await charging_stations_repository.get_evse_by_identity(
        db, station.station_id, ocpp_evse_id, include_deleted=False
    )
    if evse is None:
        raise ChargingEvseNotFoundError(
            f"OCPP EVSE '{ocpp_evse_id}' not found in station"
        )
    if ocpp_connector_id is None:
        connectors = await charging_stations_repository.list_charging_connectors(
            db, evse_id=evse.evse_id, offset=0, limit=2
        )
        if len(connectors) != 1:
            raise ChargingConnectorNotFoundError(
                "The message names no connector and the EVSE does not have exactly one"
            )
        return station.station_id, evse.evse_id, connectors[0].connector_id
    connector = await charging_stations_repository.get_connector_by_identity(
        db, evse.evse_id, ocpp_connector_id, include_deleted=False
    )
    if connector is None:
        raise ChargingConnectorNotFoundError(
            f"OCPP connector '{ocpp_connector_id}' not found in EVSE"
        )
    return station.station_id, evse.evse_id, connector.connector_id


async def resolve_ocpp16_topology(
    db: AsyncSession, ocpp_identity: str, ocpp_connector_id: int
) -> tuple[UUID, UUID, UUID]:
    """Resolve an OCPP 1.6J connector number into internal topology IDs.

    Rule:
        OCPP 1.6J has no EVSE level, so gun ``n`` (``n >= 1``) is provisioned
        as EVSE ``n`` holding connector ``1`` (decision D3 of the OCPP 1.6J
        planner). Connector ``0`` means the whole charger and has no topology
        row: callers must use ``update_charger_status`` for it instead.

    Args:
        db: Async session owned by the OCPP entry boundary.
        ocpp_identity: Station identity from the WebSocket path.
        ocpp_connector_id: Connector number in the 1.6J message; must be
            positive.

    Returns:
        Tuple ``(station_id, evse_id, connector_id)``.

    Raises:
        ChargingOcppMessageInputError: If ``ocpp_connector_id`` is not positive.
        ChargingStationNotFoundError: If the station is not pre-provisioned.
        ChargingEvseNotFoundError: If EVSE ``n`` is not provisioned.
        ChargingConnectorNotFoundError: If EVSE ``n`` has no connector ``1``.
    """
    if ocpp_connector_id < 1:
        raise ChargingOcppMessageInputError(
            "OCPP 1.6J connector 0 is the whole charger and has no topology row"
        )
    return await resolve_ocpp_topology(db, ocpp_identity, ocpp_connector_id, 1)


async def record_ocpp_message(
    db: AsyncSession,
    *,
    station_id: UUID,
    occurred_at: datetime,
    ocpp_subprotocol: str,
    direction: OcppMessageDirection,
    raw_frame: str,
) -> None:
    """Append one raw OCPP frame to the verbatim message log.

    Rule:
        The frame is stored exactly as given; it is never parsed,
        normalized, or truncated here. Only the metadata around it is
        validated, plus the two envelope fields copied for the viewer
        (``action`` of a request, the frame's message ID; CS-18). Called by the
        OCPP gateway's connection wrapper in its own transaction, so a
        rolled-back handler never erases the record of what arrived.

    Args:
        db: Async session owned by the gateway's logging boundary.
        station_id: UUID of the station the frame was exchanged with.
        occurred_at: When the frame was received or sent; must carry a
            timezone and is normalized to UTC.
        ocpp_subprotocol: Negotiated WebSocket subprotocol (at most
            ``OCPP_SUBPROTOCOL_MAX_LENGTH`` characters, e.g. ``ocpp1.6``).
        direction: Whether the frame was inbound or outbound.
        raw_frame: The exact frame text.

    Raises:
        ChargingOcppMessageInputError: If ``occurred_at`` lacks a timezone or
            ``ocpp_subprotocol`` is empty or longer than its column.

    Side Effects:
        Appends one row and flushes within the caller's transaction; does not
        commit or roll back. For an inbound frame it also records the station's
        liveness (``last_seen_at`` and ``ocpp_protocol_version`` of its state
        row) in the same
        transaction, since any frame proves the charger is alive; this works
        for both protocols without touching their handlers.
    """
    occurred_at_utc = _to_utc(occurred_at, "occurred_at")
    if not ocpp_subprotocol or len(ocpp_subprotocol) > OCPP_SUBPROTOCOL_MAX_LENGTH:
        raise ChargingOcppMessageInputError(
            f"ocpp_subprotocol must be 1-{OCPP_SUBPROTOCOL_MAX_LENGTH} characters"
        )
    action, ocpp_message_id = _read_envelope(raw_frame)
    await ocpp_state_repository.insert_ocpp_message(
        db,
        station_id=station_id,
        occurred_at=occurred_at_utc,
        ocpp_subprotocol=ocpp_subprotocol,
        direction=direction,
        raw_frame=raw_frame,
        action=action,
        ocpp_message_id=ocpp_message_id,
    )
    if direction is OcppMessageDirection.CP_TO_CSMS:
        await ocpp_state_repository.touch_station_seen(
            db,
            station_id,
            seen_at=occurred_at_utc,
            ocpp_protocol_version=ocpp_subprotocol,
        )


async def record_charger_boot(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    vendor: str,
    model: str,
    serial_number: str | None,
    firmware_version: str | None,
    booted_at: datetime,
) -> None:
    """Store the device identity from an OCPP ``BootNotification``.

    Rule:
        The latest boot is the truth about the physical charger, so all four
        device fields of its state row are overwritten (a field the charger no
        longer reports becomes ``NULL``). A **firmware change** relative to a
        previously stored non-null value is logged as a structured ``WARNING``
        - the stored value is the baseline for noticing a firmware swap;
        alerting on it is deferred (``deferred.md`` #75).

    Args:
        db: Async session owned by the OCPP gateway's action transaction.
        ocpp_identity: Identity of the station that booted.
        vendor: ``chargePointVendor`` from the message.
        model: ``chargePointModel`` from the message.
        serial_number: Charger serial number, or ``None``.
        firmware_version: Reported firmware version, or ``None``.
        booted_at: Time of the boot, timezone-aware.

    Raises:
        ChargingOcppMessageInputError: If ``booted_at`` lacks a timezone.
        ChargingStationNotFoundError: If the station is not pre-provisioned or
            was soft-deleted.

    Side Effects:
        Updates the station's state row within the caller's transaction; does
        not commit or roll back. If the update matches no row a ``WARNING`` is
        logged and the boot is still accepted: the OCPP answer never depends on
        it. A reported serial that differs from the registered one is logged
        as a ``WARNING`` too (CS-14).
    """
    booted_at_utc = _to_utc(booted_at, "booted_at")
    station = await _get_active_station_by_identity(db, ocpp_identity)
    state = await ocpp_state_repository.get_station_state_for_update(
        db, station.station_id
    )
    previous_firmware = None if state is None else state.firmware_version
    if (
        previous_firmware is not None
        and firmware_version is not None
        and previous_firmware != firmware_version
    ):
        logger.warning(
            "Charger firmware version changed",
            extra={
                "ocpp_identity": ocpp_identity,
                "previous_firmware_version": previous_firmware,
                "firmware_version": firmware_version,
            },
        )
    if serial_number is not None and serial_number != station.registered_serial_number:
        # CS-14: a swapped controller board or a misconfigured charger.
        logger.warning(
            "Charger serial number differs from the registered one",
            extra={
                "ocpp_identity": ocpp_identity,
                "registered_serial_number": station.registered_serial_number,
                "reported_serial_number": serial_number,
            },
        )
    is_updated = await ocpp_state_repository.update_station_boot_info(
        db,
        station.station_id,
        vendor=vendor,
        model=model,
        serial_number=serial_number,
        firmware_version=firmware_version,
        booted_at=booted_at_utc,
    )
    if not is_updated:
        logger.warning(
            "Charger boot info matched no active station",
            extra={"ocpp_identity": ocpp_identity},
        )


async def update_charger_status(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    status: ChargingConnectorStatus,
    status_updated_at: datetime,
    error_code: str | None,
    vendor_error_code: str | None,
) -> None:
    """Record the status of the whole charger (OCPP 1.6J connector ``0``).

    Args:
        db: Async session owned by the OCPP entry boundary.
        ocpp_identity: Identity of the station that reported.
        status: Reported status of the whole charger.
        status_updated_at: Timestamp of the report, timezone-aware.
        error_code: Reported ``errorCode``, stored as sent.
        vendor_error_code: Reported ``vendorErrorCode``, or ``None``.

    Raises:
        ChargingOcppMessageInputError: If ``status_updated_at`` lacks a
            timezone.
        ChargingStationNotFoundError: If the station is not pre-provisioned or
            was soft-deleted.

    Side Effects:
        Updates the station's state row within the caller's transaction; does
        not commit or roll back. An update that matches no row is logged as a
        ``WARNING`` and otherwise ignored, like ``record_charger_boot``.
    """
    status_updated_at_utc = _to_utc(status_updated_at, "status_updated_at")
    station_id = await resolve_station_id_by_identity(db, ocpp_identity)
    is_updated = await ocpp_state_repository.update_station_charger_status(
        db,
        station_id,
        status=status,
        status_updated_at=status_updated_at_utc,
        error_code=error_code,
        vendor_error_code=vendor_error_code,
    )
    if not is_updated:
        logger.warning(
            "Charger status matched no active station",
            extra={"ocpp_identity": ocpp_identity},
        )


async def update_connector_status(
    db: AsyncSession,
    *,
    connector_id: UUID,
    status: ChargingConnectorStatus,
    status_updated_at: datetime,
    error_code: str | None = None,
    vendor_error_code: str | None = None,
    status_info: str | None = None,
) -> None:
    """Record a connector's live status from an OCPP ``StatusNotification`` (F-C2).

    Args:
        db: Async session owned by the caller's entry boundary (the OCPP
            gateway's own transaction).
        connector_id: UUID of the connector the station reported on.
        status: New live status.
        status_updated_at: Timestamp the station reported, already parsed
            and normalized to UTC.
        error_code: ``errorCode`` of the report (OCPP 1.6J), stored as sent.
        vendor_error_code: ``vendorErrorCode`` of the report, if any.
        status_info: Free-text ``info`` of the report, if any.

    Raises:
        ChargingConnectorNotFoundError: If the connector has no state row.

    Side Effects:
        Updates the connector's state row within the caller's transaction.
    """
    is_updated = await ocpp_state_repository.update_connector_status(
        db,
        connector_id,
        status=status,
        status_updated_at=status_updated_at,
        error_code=error_code,
        vendor_error_code=vendor_error_code,
        status_info=status_info,
    )
    if not is_updated:
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")


async def open_configuration_capture(
    db: AsyncSession,
    *,
    command_id: UUID,
    reason: ConfigurationCaptureReason,
    ocpp_protocol_version: str,
    ocpp_request_id: int | None = None,
) -> UUID:
    """Open the pending snapshot a ``GET_CONFIGURATION`` command asks for (CS-21).

    Args:
        db: Async session owned by the gateway's transaction.
        command_id: The command (created by the API, or by the gateway itself
            for the automatic one after boot).
        reason: Why the snapshot is taken.
        ocpp_protocol_version: Protocol of the connection.
        ocpp_request_id: OCPP 2.0.1 ``GetBaseReport`` request ID, else ``None``.

    Returns:
        The new snapshot's ID.
    """
    capture = await ocpp_state_repository.insert_configuration_capture(
        db,
        command_id=command_id,
        reason=reason,
        ocpp_protocol_version=ocpp_protocol_version,
        ocpp_request_id=ocpp_request_id,
    )
    return capture.capture_id


async def start_boot_configuration_command(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    ocpp_protocol_version: str,
    ocpp_message_id: str,
    ocpp_request_id: int | None = None,
) -> UUID:
    """Create the system's own ``GET_CONFIGURATION`` command after a boot (CO-05).

    The gateway sends this request itself, so the command is created already
    holding the frame's message ID (it is not queued for the command loop) and
    has no requesting user.

    Args:
        db: Async session owned by the gateway's transaction.
        ocpp_identity: Identity of the station that booted.
        ocpp_protocol_version: Protocol of the connection.
        ocpp_message_id: Message ID of the request the gateway will send.
        ocpp_request_id: OCPP 2.0.1 ``GetBaseReport`` request ID, else ``None``.

    Returns:
        The new command's ID; its snapshot is open (``PENDING``).

    Raises:
        ChargingStationNotFoundError: If the station is not provisioned.
    """
    station_id = await resolve_station_id_by_identity(db, ocpp_identity)
    command = await ocpp_state_repository.insert_station_command(
        db,
        station_id=station_id,
        command_type=StationCommandType.GET_CONFIGURATION,
        ocpp_message_id=ocpp_message_id,
        parameters=(
            None if ocpp_request_id is None else {"ocpp_request_id": ocpp_request_id}
        ),
    )
    await open_configuration_capture(
        db,
        command_id=command.command_id,
        reason=ConfigurationCaptureReason.BOOT,
        ocpp_protocol_version=ocpp_protocol_version,
        ocpp_request_id=ocpp_request_id,
    )
    return command.command_id


async def complete_configuration_capture(
    db: AsyncSession,
    *,
    command_id: UUID,
    entries: Sequence[ConfigurationEntry],
    captured_at: datetime,
    response_status: str | None = "Accepted",
) -> UUID | None:
    """Store a 1.6J ``GetConfiguration`` answer into the command's snapshot.

    Rule:
        Append-only: the snapshot's entries are inserted once and never
        touched, so comparing two snapshots shows whether a charger's settings
        changed. A 1.6J key is a variable with no component, attribute
        ``Actual``, and ``readonly`` mapped to ``mutability``. The command is
        ``ACCEPTED`` and the snapshot ``COMPLETE`` (an empty answer is complete
        too: the charger really has no setting to report).

    Args:
        db: Async session owned by the gateway's transaction.
        command_id: The ``GET_CONFIGURATION`` command that was answered.
        entries: The configuration keys the charger reported.
        captured_at: When the answer was received; must carry a timezone.
        response_status: The charger's answer as sent.

    Returns:
        The snapshot's ID, or ``None`` if the command has no snapshot.

    Raises:
        ChargingOcppMessageInputError: If ``captured_at`` lacks a timezone.
    """
    captured_at_utc = _to_utc(captured_at, "captured_at")
    capture = await ocpp_state_repository.get_configuration_capture_by_command_id(
        db, command_id
    )
    if capture is None:
        return None
    for entry in entries:
        await ocpp_state_repository.insert_configuration_entry(
            db,
            capture_id=capture.capture_id,
            variable_name=entry.key,
            value=entry.value,
            mutability=(
                ConfigurationMutability.READ_ONLY
                if entry.is_readonly
                else ConfigurationMutability.READ_WRITE
            ).value,
        )
    await ocpp_state_repository.set_configuration_capture_outcome(
        db,
        capture.capture_id,
        outcome=ConfigurationCaptureOutcome.COMPLETE,
        captured_at=captured_at_utc,
    )
    await ocpp_state_repository.set_command_answer(
        db,
        command_id,
        outcome=StationCommandOutcome.ACCEPTED,
        response_status=response_status,
        answered_at=captured_at_utc,
    )
    return capture.capture_id


async def fail_command(
    db: AsyncSession,
    *,
    command_id: UUID,
    outcome: StationCommandOutcome,
    response_status: str | None,
    answered_at: datetime,
) -> None:
    """Record a command that got no usable answer, and fail its snapshot.

    Args:
        db: Async session owned by the gateway's transaction.
        command_id: The command.
        outcome: ``ERROR``, ``TIMEOUT`` or ``REJECTED``.
        response_status: The charger's answer as sent, ``None`` without one.
        answered_at: When the failure was recorded; must carry a timezone.

    Side Effects:
        Writes the answer to the command; a ``GET_CONFIGURATION`` command's
        pending snapshot becomes ``FAILED``.
    """
    answered_at_utc = _to_utc(answered_at, "answered_at")
    await ocpp_state_repository.set_command_answer(
        db,
        command_id,
        outcome=outcome,
        response_status=response_status,
        answered_at=answered_at_utc,
    )
    capture = await ocpp_state_repository.get_configuration_capture_by_command_id(
        db, command_id
    )
    if (
        capture is not None
        and capture.outcome == ConfigurationCaptureOutcome.PENDING.value
    ):
        await ocpp_state_repository.set_configuration_capture_outcome(
            db,
            capture.capture_id,
            outcome=ConfigurationCaptureOutcome.FAILED,
            captured_at=None,
        )


async def store_configuration_report_part(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    ocpp_request_id: int,
    entries: Sequence[ReportEntry],
    is_last_part: bool,
    received_at: datetime,
) -> UUID | None:
    """Store one OCPP 2.0.1 ``NotifyReport`` part into its pending snapshot.

    Rule:
        The part belongs to the pending snapshot of this charger with the same
        ``requestId`` (CS-21). The last part (``tbc`` false) closes the snapshot
        as ``COMPLETE`` at the arrival time and the command as ``ACCEPTED``.
        A part with no pending snapshot (nobody asked for it) stores nothing.

    Args:
        db: Async session owned by the gateway's transaction.
        ocpp_identity: Identity of the station that sent the part.
        ocpp_request_id: The ``requestId`` of the part.
        entries: The settings of this part.
        is_last_part: Whether no more parts follow.
        received_at: When the part arrived; must carry a timezone.

    Returns:
        The snapshot's ID, or ``None`` if no snapshot was waiting.

    Raises:
        ChargingOcppMessageInputError: If ``received_at`` lacks a timezone.
        ChargingStationNotFoundError: If the station is not provisioned.
    """
    received_at_utc = _to_utc(received_at, "received_at")
    station_id = await resolve_station_id_by_identity(db, ocpp_identity)
    capture = await ocpp_state_repository.find_pending_capture_by_request_id(
        db, station_id, ocpp_request_id
    )
    if capture is None:
        return None
    for entry in entries:
        await ocpp_state_repository.insert_configuration_entry(
            db,
            capture_id=capture.capture_id,
            variable_name=entry.variable_name,
            value=entry.value,
            mutability=entry.mutability.value,
            attribute_type=entry.attribute_type,
            component_name=entry.component_name,
            component_instance=entry.component_instance,
            ocpp_evse_id=entry.ocpp_evse_id,
            ocpp_connector_id=entry.ocpp_connector_id,
            variable_instance=entry.variable_instance,
        )
    if is_last_part:
        await ocpp_state_repository.set_configuration_capture_outcome(
            db,
            capture.capture_id,
            outcome=ConfigurationCaptureOutcome.COMPLETE,
            captured_at=received_at_utc,
        )
    return capture.capture_id
