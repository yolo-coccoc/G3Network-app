"""Asynchronous repository for the state OCPP chargers report about themselves.

Counterpart of ``repository.py`` (locations, topology and the directory/geo
queries): this module holds the queries behind the OCPP gateway's writes - the
verbatim frame log, the two state tables (liveness, boot identity, whole-charger
and connector status), the command table that is also the API-to-gateway channel
(CS-20, PR-16) and the configuration snapshots (CS-19, CS-21) - plus the reads of
those commands and snapshots used by the API. It only queries and flushes; it
never commits or rolls back and holds no business rule.

State tables are written by plain ``UPDATE``s of their own row: a device report
no longer touches the profile row, so ``updated_at`` of a charger keeps meaning
"last edit by a person" without any trick (DM-16, CS-06).
"""

from collections.abc import Collection
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.charging_stations.models import (
    ChargingConnectorStateModel,
    ChargingOcppMessageModel,
    ChargingStationCommandModel,
    ChargingStationConfigurationCaptureModel,
    ChargingStationConfigurationEntryModel,
    ChargingStationStateModel,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ConfigurationCaptureOutcome,
    ConfigurationCaptureReason,
    OcppMessageDirection,
    StationCommandOutcome,
    StationCommandType,
)
from app.libs.common.clock import utc_now


async def update_connector_status(
    db: AsyncSession,
    connector_id: UUID,
    *,
    status: ChargingConnectorStatus,
    status_updated_at: datetime,
    error_code: str | None = None,
    vendor_error_code: str | None = None,
    status_info: str | None = None,
) -> bool:
    """Set a connector's live status from an OCPP ``StatusNotification`` (F-C2).

    Args:
        db: Current async session.
        connector_id: UUID of the connector whose state to update.
        status: New live status reported by the station.
        status_updated_at: Timestamp the station reported, already parsed
            and normalized to UTC by the caller.
        error_code: ``errorCode`` of this report, if the protocol carries one
            (OCPP 1.6J does, 2.0.1 does not).
        vendor_error_code: ``vendorErrorCode`` of this report, if any.
        status_info: Free-text ``info`` of this report, if any.

    Returns:
        ``True`` if the connector's state row was updated.

    Side Effects:
        Issues one ``UPDATE`` of the state row (a report without the detail
        fields **clears** the old values, because the latest report is the
        truth about the connector) and flushes; does not commit. No
        out-of-order guard - in-order arrival is this MVP's assumption
        (``docs/decisions/deferred.md`` item 27).
    """
    result = await db.execute(
        update(ChargingConnectorStateModel)
        .where(ChargingConnectorStateModel.connector_id == connector_id)
        .values(
            status=status.value,
            status_updated_at=status_updated_at,
            error_code=error_code,
            vendor_error_code=vendor_error_code,
            status_info=status_info,
        )
    )
    await db.flush()
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def insert_ocpp_message(
    db: AsyncSession,
    *,
    station_id: UUID,
    occurred_at: datetime,
    ocpp_subprotocol: str,
    direction: OcppMessageDirection,
    raw_frame: str,
    action: str | None = None,
    ocpp_message_id: str | None = None,
) -> ChargingOcppMessageModel:
    """Append one raw OCPP frame to the message log.

    Args:
        db: Current async session.
        station_id: UUID of the station the frame was exchanged with.
        occurred_at: Receive/send time, already timezone-aware UTC.
        ocpp_subprotocol: Negotiated WebSocket subprotocol.
        direction: Whether the frame was inbound or outbound.
        raw_frame: The exact frame text.
        action: Message type copied from a request's envelope, if any.
        ocpp_message_id: The frame's own message ID from the envelope, if
            readable.

    Returns:
        The persisted log row.

    Side Effects:
        Adds the row and flushes; does not commit. The log is append-only, so
        there is no update or delete counterpart.
    """
    message = ChargingOcppMessageModel(
        station_id=station_id,
        occurred_at=occurred_at,
        ocpp_subprotocol=ocpp_subprotocol,
        direction=direction,
        raw_frame=raw_frame,
        action=action,
        ocpp_message_id=ocpp_message_id,
    )
    db.add(message)
    await db.flush()
    return message


def _ocpp_message_conditions(
    station_id: UUID,
    *,
    action: str | None,
    direction: OcppMessageDirection | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
) -> list[ColumnElement[bool]]:
    """Build the filters of a station's message-log read (STN-15 minimal read).

    Args:
        station_id: The station.
        action: Only requests of this OCPP action (answers carry none).
        direction: Only frames in this direction.
        occurred_from: Inclusive lower bound on the receive/send time.
        occurred_to: Exclusive upper bound on the receive/send time.

    Returns:
        Conditions on ``ChargingOcppMessageModel``; every one filters the
        hypertable by its time or station key where it can.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingOcppMessageModel.station_id == station_id
    ]
    if action is not None:
        conditions.append(ChargingOcppMessageModel.action == action)
    if direction is not None:
        conditions.append(ChargingOcppMessageModel.direction == direction)
    if occurred_from is not None:
        conditions.append(ChargingOcppMessageModel.occurred_at >= occurred_from)
    if occurred_to is not None:
        conditions.append(ChargingOcppMessageModel.occurred_at < occurred_to)
    return conditions


async def list_ocpp_messages(
    db: AsyncSession,
    *,
    station_id: UUID,
    offset: int,
    limit: int,
    action: str | None = None,
    direction: OcppMessageDirection | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> list[ChargingOcppMessageModel]:
    """List a station's logged frames, newest first.

    Args:
        db: Current async session.
        station_id: The station.
        offset: Number of rows to skip.
        limit: Maximum number of rows.
        action: Only requests of this OCPP action.
        direction: Only frames in this direction.
        occurred_from: Inclusive lower bound on the receive/send time.
        occurred_to: Exclusive upper bound on the receive/send time.

    Returns:
        The frames, newest first.
    """
    result = await db.execute(
        select(ChargingOcppMessageModel)
        .where(
            *_ocpp_message_conditions(
                station_id,
                action=action,
                direction=direction,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
            )
        )
        .order_by(
            ChargingOcppMessageModel.occurred_at.desc(),
            ChargingOcppMessageModel.message_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_ocpp_messages(
    db: AsyncSession,
    *,
    station_id: UUID,
    action: str | None = None,
    direction: OcppMessageDirection | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> int:
    """Count a station's logged frames with the filters of `list_ocpp_messages`.

    Args:
        db: Current async session.
        station_id: The station.
        action: Only requests of this OCPP action.
        direction: Only frames in this direction.
        occurred_from: Inclusive lower bound on the receive/send time.
        occurred_to: Exclusive upper bound on the receive/send time.

    Returns:
        The number of matching frames.
    """
    result = await db.execute(
        select(func.count(ChargingOcppMessageModel.message_id)).where(
            *_ocpp_message_conditions(
                station_id,
                action=action,
                direction=direction,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
            )
        )
    )
    return int(result.scalar() or 0)


async def get_station_state_for_update(
    db: AsyncSession, station_id: UUID
) -> ChargingStationStateModel | None:
    """Read a station's state row (the gateway's view of the previous values).

    Args:
        db: Current async session.
        station_id: UUID of the station.

    Returns:
        The state row, or ``None`` when there is none.
    """
    return await db.get(ChargingStationStateModel, station_id)


async def update_station_boot_info(
    db: AsyncSession,
    station_id: UUID,
    *,
    vendor: str,
    model: str,
    serial_number: str | None,
    firmware_version: str | None,
    booted_at: datetime,
) -> bool:
    """Store the device identity reported by an OCPP ``BootNotification``.

    Args:
        db: Current async session.
        station_id: UUID of the station that booted.
        vendor: Reported vendor name.
        model: Reported model name.
        serial_number: Reported serial number, or ``None`` if the charger sent
            none (a previously stored value is then cleared, because the
            latest boot is the truth about the device).
        firmware_version: Reported firmware version, or ``None``.
        booted_at: Time of the boot, timezone-aware UTC.

    Returns:
        ``True`` if the station's state row was updated.

    Side Effects:
        Issues one ``UPDATE`` of the state row and flushes; does not commit.
    """
    result = await db.execute(
        update(ChargingStationStateModel)
        .where(ChargingStationStateModel.station_id == station_id)
        .values(
            vendor=vendor,
            model=model,
            serial_number=serial_number,
            firmware_version=firmware_version,
            last_boot_at=booted_at,
        )
    )
    await db.flush()
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def touch_station_seen(
    db: AsyncSession,
    station_id: UUID,
    *,
    seen_at: datetime,
    ocpp_protocol_version: str,
) -> None:
    """Record that a frame just arrived from a station (liveness).

    Args:
        db: Current async session.
        station_id: UUID of the station the frame came from.
        seen_at: Receive time, timezone-aware UTC.
        ocpp_protocol_version: Subprotocol of the connection.

    Side Effects:
        Issues one ``UPDATE`` of the state row without loading it and flushes;
        does not commit.
    """
    await db.execute(
        update(ChargingStationStateModel)
        .where(ChargingStationStateModel.station_id == station_id)
        .values(last_seen_at=seen_at, ocpp_protocol_version=ocpp_protocol_version)
    )
    await db.flush()


async def update_station_charger_status(
    db: AsyncSession,
    station_id: UUID,
    *,
    status: ChargingConnectorStatus,
    status_updated_at: datetime,
    error_code: str | None,
    vendor_error_code: str | None,
) -> bool:
    """Store the status of the whole charger (OCPP 1.6J connector ``0``).

    Args:
        db: Current async session.
        station_id: UUID of the station that reported.
        status: Reported status of the whole charger.
        status_updated_at: Timestamp of the report, timezone-aware UTC.
        error_code: Reported ``errorCode``, or ``None``.
        vendor_error_code: Reported ``vendorErrorCode``, or ``None``.

    Returns:
        ``True`` if the station's state row was updated.

    Side Effects:
        Issues one ``UPDATE`` of the state row and flushes; does not commit.
    """
    result = await db.execute(
        update(ChargingStationStateModel)
        .where(ChargingStationStateModel.station_id == station_id)
        .values(
            charger_status=status.value,
            charger_status_updated_at=status_updated_at,
            charger_error_code=error_code,
            charger_vendor_error_code=vendor_error_code,
        )
    )
    await db.flush()
    return bool(result.rowcount)  # type: ignore[attr-defined]


# --- Commands (CS-20) --------------------------------------------------------


async def insert_station_command(
    db: AsyncSession,
    *,
    station_id: UUID,
    command_type: StationCommandType,
    evse_id: UUID | None = None,
    session_id: UUID | None = None,
    parameters: dict[str, Any] | None = None,
    requested_by: UUID | None = None,
    reason: str | None = None,
    ocpp_message_id: str | None = None,
) -> ChargingStationCommandModel:
    """Insert a command as ``PENDING`` and flush.

    Args:
        db: Current async session.
        station_id: The charger the command goes to.
        evse_id: The gun it targets, ``None`` for the whole charger.
        session_id: The session a remote start begins or a remote stop ends.
        command_type: What to ask.
        parameters: What is sent besides the links, ``None`` if nothing.
        requested_by: User who asked, ``None`` when the system sends it.
        reason: Why, typed by the operator for a manual command.
        ocpp_message_id: Set when the sender is the gateway itself and has
            already chosen the frame's message ID (the command is then not
            queued for the loop).

    Returns:
        The persisted command; queued (``ocpp_message_id`` is ``None``) unless
        the caller gave a message ID.
    """
    command = ChargingStationCommandModel(
        station_id=station_id,
        evse_id=evse_id,
        session_id=session_id,
        command_type=command_type.value,
        parameters=parameters,
        requested_by=requested_by,
        reason=reason,
        ocpp_message_id=ocpp_message_id,
        outcome=StationCommandOutcome.PENDING.value,
    )
    db.add(command)
    await db.flush()
    await db.refresh(command)
    return command


async def get_station_command_by_id(
    db: AsyncSession, command_id: UUID
) -> ChargingStationCommandModel | None:
    """Find a command by ID.

    Args:
        db: Current async session.
        command_id: Internal UUID.

    Returns:
        The command, or ``None``.
    """
    return await db.get(ChargingStationCommandModel, command_id)


def _station_command_conditions(
    station_id: UUID,
    *,
    outcome: StationCommandOutcome | None,
    command_type: StationCommandType | None,
) -> list[ColumnElement[bool]]:
    """Build the filters of a charger's command log.

    Args:
        station_id: The charger.
        outcome: Only commands with this observed outcome.
        command_type: Only commands of this type.

    Returns:
        Conditions on ``ChargingStationCommandModel``.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingStationCommandModel.station_id == station_id
    ]
    if outcome is not None:
        conditions.append(ChargingStationCommandModel.outcome == outcome.value)
    if command_type is not None:
        conditions.append(
            ChargingStationCommandModel.command_type == command_type.value
        )
    return conditions


async def list_station_commands(
    db: AsyncSession,
    *,
    station_id: UUID,
    offset: int,
    limit: int,
    outcome: StationCommandOutcome | None = None,
    command_type: StationCommandType | None = None,
) -> list[ChargingStationCommandModel]:
    """List a charger's commands, newest first (STN-10).

    Args:
        db: Current async session.
        station_id: The charger.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        outcome: Only commands with this observed outcome.
        command_type: Only commands of this type.

    Returns:
        The commands ordered by request time, newest first.
    """
    result = await db.execute(
        select(ChargingStationCommandModel)
        .where(
            *_station_command_conditions(
                station_id, outcome=outcome, command_type=command_type
            )
        )
        .order_by(
            ChargingStationCommandModel.requested_at.desc(),
            ChargingStationCommandModel.command_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_station_commands(
    db: AsyncSession,
    station_id: UUID,
    *,
    outcome: StationCommandOutcome | None = None,
    command_type: StationCommandType | None = None,
) -> int:
    """Count a charger's commands with the filters of `list_station_commands`.

    Args:
        db: Current async session.
        station_id: The charger.
        outcome: Only commands with this observed outcome.
        command_type: Only commands of this type.

    Returns:
        The number of matching commands.
    """
    result = await db.execute(
        select(func.count(ChargingStationCommandModel.command_id)).where(
            *_station_command_conditions(
                station_id, outcome=outcome, command_type=command_type
            )
        )
    )
    return int(result.scalar() or 0)


async def cancel_queued_command(
    db: AsyncSession, command_id: UUID, *, response_status: str
) -> bool:
    """Close a command that no gateway has claimed yet as ``NOT_SENT``.

    One conditional ``UPDATE``: it only matches a row that is still queued
    (``PENDING`` with no message ID), the same condition ``claim_queued_commands``
    uses, so a command that a gateway claimed in the meantime is never
    cancelled and a cancelled one is never sent.

    Args:
        db: Current async session.
        command_id: The command.
        response_status: Text kept in ``response_status`` to tell a cancel from
            the age-based ``NOT_SENT`` (the charger answered nothing).

    Returns:
        ``True`` when the command was still queued and is closed now.
    """
    result = await db.execute(
        update(ChargingStationCommandModel)
        .where(
            ChargingStationCommandModel.command_id == command_id,
            ChargingStationCommandModel.outcome == StationCommandOutcome.PENDING.value,
            ChargingStationCommandModel.ocpp_message_id.is_(None),
        )
        .values(
            outcome=StationCommandOutcome.NOT_SENT.value,
            response_status=response_status,
        )
    )
    await db.flush()
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def claim_queued_commands(
    db: AsyncSession,
    station_ids: Collection[UUID],
    *,
    limit: int = 20,
) -> list[ChargingStationCommandModel]:
    """Pick up queued commands of the given chargers and mark them as sent.

    A queued command is ``PENDING`` with no ``ocpp_message_id``. Rows are
    locked with ``FOR UPDATE SKIP LOCKED`` so two gateway processes never take
    the same command, and each claimed row gets its message ID at once (the ID
    of the frame the caller will send); the caller commits before it sends, so
    a command is never sent twice.

    Args:
        db: Current async session.
        station_ids: Chargers connected to this gateway.
        limit: Maximum number of commands to claim at once.

    Returns:
        The claimed commands, oldest first; empty when there are none.
    """
    if not station_ids:
        return []
    result = await db.execute(
        select(ChargingStationCommandModel)
        .where(
            ChargingStationCommandModel.station_id.in_(list(station_ids)),
            ChargingStationCommandModel.outcome == StationCommandOutcome.PENDING.value,
            ChargingStationCommandModel.ocpp_message_id.is_(None),
        )
        .order_by(ChargingStationCommandModel.requested_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    commands = list(result.scalars().all())
    for command in commands:
        command.ocpp_message_id = str(uuid4())
    await db.flush()
    return commands


async def mark_unsent_commands_not_sent(
    db: AsyncSession, *, requested_before: datetime
) -> int:
    """Close queued commands that no gateway picked up in time.

    Args:
        db: Current async session.
        requested_before: Queued commands requested before this time are closed.

    Returns:
        The number of commands set to ``NOT_SENT``.

    Side Effects:
        ``NOT_SENT`` keeps ``ocpp_message_id`` and ``answered_at`` empty (the
        charger was not connected); flushes, does not commit.
    """
    result = await db.execute(
        update(ChargingStationCommandModel)
        .where(
            ChargingStationCommandModel.outcome == StationCommandOutcome.PENDING.value,
            ChargingStationCommandModel.ocpp_message_id.is_(None),
            ChargingStationCommandModel.requested_at < requested_before,
        )
        .values(outcome=StationCommandOutcome.NOT_SENT.value)
    )
    await db.flush()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def mark_stale_commands_timed_out(
    db: AsyncSession, *, requested_before: datetime
) -> int:
    """Close sent commands that never got an answer (gateway restarted mid-call).

    Args:
        db: Current async session.
        requested_before: Sent-but-unanswered commands requested before this
            time are closed.

    Returns:
        The number of commands set to ``TIMEOUT``.
    """
    result = await db.execute(
        update(ChargingStationCommandModel)
        .where(
            ChargingStationCommandModel.outcome == StationCommandOutcome.PENDING.value,
            ChargingStationCommandModel.ocpp_message_id.is_not(None),
            ChargingStationCommandModel.requested_at < requested_before,
        )
        .values(outcome=StationCommandOutcome.TIMEOUT.value, answered_at=utc_now())
    )
    await db.flush()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def set_command_message_id(
    db: AsyncSession, command_id: UUID, ocpp_message_id: str
) -> None:
    """Record the message ID of the frame about to be sent for a command.

    Args:
        db: Current async session.
        command_id: The command.
        ocpp_message_id: The frame's message ID.
    """
    await db.execute(
        update(ChargingStationCommandModel)
        .where(ChargingStationCommandModel.command_id == command_id)
        .values(ocpp_message_id=ocpp_message_id)
    )
    await db.flush()


async def set_command_answer(
    db: AsyncSession,
    command_id: UUID,
    *,
    outcome: StationCommandOutcome,
    response_status: str | None,
    answered_at: datetime,
) -> None:
    """Write the charger's answer (or the timeout) back to a command (CS-20).

    Args:
        db: Current async session.
        command_id: The command.
        outcome: The observed result.
        response_status: The charger's answer as sent, ``None`` without one.
        answered_at: When the answer or the timeout was recorded.
    """
    await db.execute(
        update(ChargingStationCommandModel)
        .where(ChargingStationCommandModel.command_id == command_id)
        .values(
            outcome=outcome.value,
            response_status=response_status,
            answered_at=answered_at,
        )
    )
    await db.flush()


# --- Configuration snapshots (CS-19, CS-21) ---------------------------------


async def insert_configuration_capture(
    db: AsyncSession,
    *,
    command_id: UUID,
    reason: ConfigurationCaptureReason,
    ocpp_protocol_version: str,
    ocpp_request_id: int | None = None,
) -> ChargingStationConfigurationCaptureModel:
    """Insert a pending snapshot for a ``GET_CONFIGURATION`` command.

    Args:
        db: Current async session.
        command_id: The command that asked for the snapshot.
        reason: Why the snapshot is taken.
        ocpp_protocol_version: Protocol of the connection.
        ocpp_request_id: OCPP 2.0.1 ``GetBaseReport`` request ID, else ``None``.

    Returns:
        The persisted snapshot (outcome ``PENDING``).
    """
    capture = ChargingStationConfigurationCaptureModel(
        command_id=command_id,
        reason=reason.value,
        ocpp_protocol_version=ocpp_protocol_version,
        ocpp_request_id=ocpp_request_id,
        outcome=ConfigurationCaptureOutcome.PENDING.value,
    )
    db.add(capture)
    await db.flush()
    return capture


async def get_configuration_capture_by_command_id(
    db: AsyncSession, command_id: UUID
) -> ChargingStationConfigurationCaptureModel | None:
    """Find the snapshot a command asked for.

    Args:
        db: Current async session.
        command_id: The command.

    Returns:
        The snapshot, or ``None``.
    """
    result = await db.execute(
        select(ChargingStationConfigurationCaptureModel).where(
            ChargingStationConfigurationCaptureModel.command_id == command_id
        )
    )
    return result.scalar_one_or_none()


async def find_pending_capture_by_request_id(
    db: AsyncSession, station_id: UUID, ocpp_request_id: int
) -> ChargingStationConfigurationCaptureModel | None:
    """Find the pending 2.0.1 snapshot a ``NotifyReport`` part belongs to.

    Args:
        db: Current async session.
        station_id: The charger that sent the part.
        ocpp_request_id: The ``requestId`` of the part.

    Returns:
        The newest matching pending snapshot, or ``None``.
    """
    result = await db.execute(
        select(ChargingStationConfigurationCaptureModel)
        .join(
            ChargingStationCommandModel,
            ChargingStationCommandModel.command_id
            == ChargingStationConfigurationCaptureModel.command_id,
        )
        .where(
            ChargingStationCommandModel.station_id == station_id,
            ChargingStationConfigurationCaptureModel.ocpp_request_id == ocpp_request_id,
            ChargingStationConfigurationCaptureModel.outcome
            == ConfigurationCaptureOutcome.PENDING.value,
        )
        .order_by(ChargingStationCommandModel.requested_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def set_configuration_capture_outcome(
    db: AsyncSession,
    capture_id: UUID,
    *,
    outcome: ConfigurationCaptureOutcome,
    captured_at: datetime | None,
) -> None:
    """Close a snapshot as complete or failed.

    Args:
        db: Current async session.
        capture_id: The snapshot.
        outcome: ``COMPLETE`` or ``FAILED``.
        captured_at: When the answer (last part) arrived, ``None`` on failure.
    """
    await db.execute(
        update(ChargingStationConfigurationCaptureModel)
        .where(ChargingStationConfigurationCaptureModel.capture_id == capture_id)
        .values(outcome=outcome.value, captured_at=captured_at)
    )
    await db.flush()


async def insert_configuration_entry(
    db: AsyncSession,
    *,
    capture_id: UUID,
    variable_name: str,
    value: str | None,
    mutability: str,
    attribute_type: str = "Actual",
    component_name: str | None = None,
    component_instance: str | None = None,
    ocpp_evse_id: int | None = None,
    ocpp_connector_id: int | None = None,
    variable_instance: str | None = None,
) -> ChargingStationConfigurationEntryModel:
    """Append one setting value of a snapshot and flush it.

    Args:
        db: Current async session.
        capture_id: The snapshot the value belongs to.
        variable_name: The 1.6J key or the 2.0.1 variable.
        value: The value as text, or ``None``.
        mutability: ``READ_ONLY`` / ``READ_WRITE`` / ``WRITE_ONLY``.
        attribute_type: ``Actual`` (always for 1.6J), ``Target``, ``MinSet``
            or ``MaxSet``.
        component_name: OCPP 2.0.1 only: the component.
        component_instance: OCPP 2.0.1 only: instance of the component.
        ocpp_evse_id: OCPP 2.0.1 only: EVSE the component sits on.
        ocpp_connector_id: OCPP 2.0.1 only: connector the component sits on.
        variable_instance: OCPP 2.0.1 only: instance of the variable.

    Returns:
        The persisted row.

    Side Effects:
        Adds the row and flushes; does not commit. The table is append-only.
    """
    entry = ChargingStationConfigurationEntryModel(
        capture_id=capture_id,
        variable_name=variable_name,
        value=value,
        mutability=mutability,
        attribute_type=attribute_type,
        component_name=component_name,
        component_instance=component_instance,
        ocpp_evse_id=ocpp_evse_id,
        ocpp_connector_id=ocpp_connector_id,
        variable_instance=variable_instance,
    )
    db.add(entry)
    await db.flush()
    return entry


async def get_latest_configuration_capture(
    db: AsyncSession, station_id: UUID
) -> tuple[UUID, datetime] | None:
    """Find a station's current settings: its newest ``COMPLETE`` snapshot.

    The charger is read through the snapshot's command (CS-21).

    Args:
        db: Current async session.
        station_id: UUID of the station.

    Returns:
        ``(capture_id, captured_at)`` of the newest complete snapshot, or
        ``None`` if the station never reported its configuration.
    """
    result = await db.execute(
        select(
            ChargingStationConfigurationCaptureModel.capture_id,
            ChargingStationConfigurationCaptureModel.captured_at,
        )
        .join(
            ChargingStationCommandModel,
            ChargingStationCommandModel.command_id
            == ChargingStationConfigurationCaptureModel.command_id,
        )
        .where(
            ChargingStationCommandModel.station_id == station_id,
            ChargingStationConfigurationCaptureModel.outcome
            == ConfigurationCaptureOutcome.COMPLETE.value,
            ChargingStationConfigurationCaptureModel.captured_at.is_not(None),
        )
        .order_by(ChargingStationConfigurationCaptureModel.captured_at.desc())
        .limit(1)
    )
    row = result.first()
    if row is None or row.captured_at is None:
        return None
    return row.capture_id, row.captured_at


async def list_configuration_entries_by_capture_id(
    db: AsyncSession, capture_id: UUID
) -> list[ChargingStationConfigurationEntryModel]:
    """Get the rows of one configuration snapshot, sorted by setting name.

    Args:
        db: Current async session.
        capture_id: The snapshot whose rows to read.

    Returns:
        The snapshot's rows ordered by component, then variable name.
    """
    result = await db.execute(
        select(ChargingStationConfigurationEntryModel)
        .where(ChargingStationConfigurationEntryModel.capture_id == capture_id)
        .order_by(
            ChargingStationConfigurationEntryModel.component_name.asc().nulls_first(),
            ChargingStationConfigurationEntryModel.variable_name.asc(),
        )
    )
    return list(result.scalars().all())
