"""Asynchronous repository for the state OCPP chargers report about themselves.

Counterpart of ``repository.py`` (station/EVSE/connector topology and the
directory/geo queries): this module holds the queries behind the OCPP
gateway's writes — the verbatim frame log, liveness, boot identity, the
charger (connector ``0``) and connector status, and the append-only
``GetConfiguration`` captures — plus the two reads of those captures used by
the configuration API. It only queries and flushes; it never commits or rolls
back and holds no business rule.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingOcppMessageModel,
    ChargingStationConfigurationEntryModel,
    ChargingStationModel,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    OcppMessageDirection,
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
) -> ChargingConnectorModel | None:
    """Set a connector's live status from an OCPP ``StatusNotification`` (F-C2).

    Args:
        db: Current async session.
        connector_id: UUID of the connector to update.
        status: New live status reported by the station.
        status_updated_at: Timestamp the station reported, already parsed
            and normalized to UTC by the caller.
        error_code: ``errorCode`` of this report, if the protocol carries one
            (OCPP 1.6J does, 2.0.1 does not).
        vendor_error_code: ``vendorErrorCode`` of this report, if any.
        status_info: Free-text ``info`` of this report, if any.

    Returns:
        The updated connector, or ``None`` if it is no longer active.

    Side Effects:
        Assigns ``status``/``status_updated_at`` and the three detail fields
        (a report without them **clears** the old values, because the latest
        report is the truth about the connector), updates ``updated_at``,
        flushes, and refreshes; does not commit. No out-of-order guard —
        in-order message arrival is this MVP's existing assumption (see
        ``docs/01-requirements/future.md`` item 27); the incoming timestamp
        is not compared against the stored one.
    """
    connector = await charging_stations_repository.get_connector_by_id(db, connector_id)
    if connector is None:
        return None
    connector.status = status
    connector.status_updated_at = status_updated_at
    connector.error_code = error_code
    connector.vendor_error_code = vendor_error_code
    connector.status_info = status_info
    connector.updated_at = utc_now()
    await db.flush()
    await db.refresh(connector)
    return connector


async def insert_ocpp_message(
    db: AsyncSession,
    *,
    station_id: UUID,
    occurred_at: datetime,
    ocpp_subprotocol: str,
    direction: OcppMessageDirection,
    raw_frame: str,
) -> ChargingOcppMessageModel:
    """Append one raw OCPP frame to the message log.

    Args:
        db: Current async session.
        station_id: UUID of the station the frame was exchanged with.
        occurred_at: Receive/send time, already timezone-aware UTC.
        ocpp_subprotocol: Negotiated WebSocket subprotocol.
        direction: Whether the frame was inbound or outbound.
        raw_frame: The exact frame text.

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
    )
    db.add(message)
    await db.flush()
    return message


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
        ``True`` if an active station was updated.

    Side Effects:
        Issues one ``UPDATE`` and flushes; does not commit. ``updated_at`` is
        deliberately left unchanged: these values are reported by the device,
        not an administrator's edit.
    """
    result = await db.execute(
        update(ChargingStationModel)
        .where(
            ChargingStationModel.station_id == station_id,
            ChargingStationModel.deleted_at.is_(None),
        )
        .values(
            vendor=vendor,
            model=model,
            serial_number=serial_number,
            firmware_version=firmware_version,
            last_boot_at=booted_at,
            updated_at=ChargingStationModel.updated_at,
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
        Issues one ``UPDATE`` without loading the row and flushes; does not
        commit. ``updated_at`` is deliberately left unchanged so liveness
        never looks like an administrator's edit.
    """
    await db.execute(
        update(ChargingStationModel)
        .where(ChargingStationModel.station_id == station_id)
        .values(
            last_seen_at=seen_at,
            ocpp_protocol_version=ocpp_protocol_version,
            updated_at=ChargingStationModel.updated_at,
        )
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
        ``True`` if an active station was updated.

    Side Effects:
        Issues one ``UPDATE`` and flushes; does not commit. ``updated_at`` is
        left unchanged: device-reported state is not an administrator's edit.
    """
    result = await db.execute(
        update(ChargingStationModel)
        .where(
            ChargingStationModel.station_id == station_id,
            ChargingStationModel.deleted_at.is_(None),
        )
        .values(
            charger_status=status,
            charger_status_updated_at=status_updated_at,
            charger_error_code=error_code,
            charger_vendor_error_code=vendor_error_code,
            updated_at=ChargingStationModel.updated_at,
        )
    )
    await db.flush()
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def insert_configuration_entry(
    db: AsyncSession,
    *,
    station_id: UUID,
    capture_id: UUID,
    captured_at: datetime,
    config_key: str,
    value: str | None,
    is_readonly: bool,
) -> ChargingStationConfigurationEntryModel:
    """Append one configuration key of a capture and flush it.

    Args:
        db: Current async session.
        station_id: UUID of the station the configuration belongs to.
        capture_id: Groups the rows of one capture.
        captured_at: When the answer was received, timezone-aware UTC.
        config_key: The configuration key name.
        value: The key's value as text, or ``None``.
        is_readonly: Whether the charger reported the key as read-only.

    Returns:
        The persisted row.

    Side Effects:
        Adds the row and flushes; does not commit. The table is append-only.
    """
    entry = ChargingStationConfigurationEntryModel(
        station_id=station_id,
        capture_id=capture_id,
        captured_at=captured_at,
        config_key=config_key,
        value=value,
        is_readonly=is_readonly,
    )
    db.add(entry)
    await db.flush()
    return entry


async def get_latest_configuration_capture(
    db: AsyncSession, station_id: UUID
) -> tuple[UUID, datetime] | None:
    """Find the most recent configuration capture of a station.

    Args:
        db: Current async session.
        station_id: UUID of the station.

    Returns:
        ``(capture_id, captured_at)`` of the newest capture, or ``None`` if the
        station never reported its configuration.
    """
    result = await db.execute(
        select(
            ChargingStationConfigurationEntryModel.capture_id,
            ChargingStationConfigurationEntryModel.captured_at,
        )
        .where(ChargingStationConfigurationEntryModel.station_id == station_id)
        .order_by(ChargingStationConfigurationEntryModel.captured_at.desc())
        .limit(1)
    )
    row = result.first()
    return None if row is None else (row.capture_id, row.captured_at)


async def list_configuration_entries_by_capture_id(
    db: AsyncSession, station_id: UUID, capture_id: UUID
) -> list[ChargingStationConfigurationEntryModel]:
    """Get the rows of one configuration capture, sorted by key name.

    Args:
        db: Current async session.
        station_id: UUID of the station.
        capture_id: The capture whose rows to read.

    Returns:
        The capture's rows ordered by ``config_key``.
    """
    result = await db.execute(
        select(ChargingStationConfigurationEntryModel)
        .where(
            ChargingStationConfigurationEntryModel.station_id == station_id,
            ChargingStationConfigurationEntryModel.capture_id == capture_id,
        )
        .order_by(ChargingStationConfigurationEntryModel.config_key.asc())
    )
    return list(result.scalars().all())
