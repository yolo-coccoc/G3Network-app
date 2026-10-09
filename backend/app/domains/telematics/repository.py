"""Repository for querying the Telematic device table.

This repository is only called from inside the ``telematics`` domain (its
service and its device-health monitor). Other domains must use the public
service so they do not depend directly on this domain's model or internal
SQL. Every lookup excludes soft-deleted devices; functions flush when they
need a generated value or a constraint error, and never commit or roll back.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telematics.models import TelematicModel, TelematicStatusReportModel
from app.domains.telematics.types import TelematicStatus
from app.libs.common.clock import utc_now
from app.libs.db.history import set_change_context


async def get_by_id(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> TelematicModel | None:
    """Get a device that has not been soft-deleted, by internal ID.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.

    Returns:
        The device record, or ``None`` if not found or soft-deleted.
    """
    query_result = await db_session.execute(
        select(TelematicModel).where(
            TelematicModel.telematic_id == telematic_id,
            TelematicModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_serial(
    db_session: AsyncSession,
    telematic_serial: str,
) -> TelematicModel | None:
    """Find a device that has not been soft-deleted, by its physical serial.

    Args:
        db_session: Current database session.
        telematic_serial: Serial printed on the device.

    Returns:
        The device record, or ``None`` if not found or soft-deleted. A
        soft-deleted device still holds its serial in the table's unique
        constraint, so reusing it surfaces as an ``IntegrityError`` on
        insert/flush rather than here.
    """
    query_result = await db_session.execute(
        select(TelematicModel).where(
            TelematicModel.telematic_serial == telematic_serial,
            TelematicModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_vehicle_id(
    db_session: AsyncSession,
    vehicle_id: UUID,
) -> TelematicModel | None:
    """Find the live device currently assigned to a vehicle.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The device record assigned to that vehicle, or ``None`` if no
        non-soft-deleted device is assigned to it. Mirrors the partial
        unique index ``uq_telematics_active_vehicle``, so at most one row
        can match.
    """
    query_result = await db_session.execute(
        select(TelematicModel).where(
            TelematicModel.vehicle_id == vehicle_id,
            TelematicModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def find_mounted_vehicle_id_by_serial(
    db_session: AsyncSession,
    telematic_serial: str,
) -> tuple[UUID, UUID] | None:
    """Find a device and the vehicle it is mounted on, by serial, for ingestion.

    Args:
        db_session: Database session owned by the entry boundary.
        telematic_serial: Physical serial of the device to look up.

    Returns:
        ``(telematic_id, vehicle_id)`` when the device is not soft-deleted
        and is mounted on a vehicle; ``None`` if there is no valid mapping.
    """
    query_result = await db_session.execute(
        select(TelematicModel.telematic_id, TelematicModel.vehicle_id).where(
            TelematicModel.telematic_serial == telematic_serial,
            TelematicModel.deleted_at.is_(None),
            TelematicModel.vehicle_id.is_not(None),
        )
    )
    mapping_row = query_result.one_or_none()
    if mapping_row is None:
        return None
    return mapping_row.telematic_id, mapping_row.vehicle_id


async def find_serial_by_id(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> str | None:
    """Find a device's serial by internal ID, also for a soft-deleted device.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.

    Returns:
        The serial, or ``None`` if no such device exists. A deleted device is
        included because its past telemetry still names it.
    """
    query_result = await db_session.execute(
        select(TelematicModel.telematic_serial).where(
            TelematicModel.telematic_id == telematic_id
        )
    )
    return query_result.scalar_one_or_none()


async def list_active_with_vehicle(
    db_session: AsyncSession,
) -> list[TelematicModel]:
    """Get active devices assigned to a vehicle, for the F-J1/F-J3 health monitor.

    Args:
        db_session: Current database session.

    Returns:
        Non-soft-deleted, ``ACTIVE`` devices with a non-``None``
        ``vehicle_id``. A device that's soft-deleted, explicitly
        ``INACTIVE``, or not yet assigned to a vehicle is
        excluded - a device deliberately taken offline being silent is
        expected, not something to alert on. Whether the assigned vehicle
        is itself soft-deleted is not checked here (that table belongs to
        the vehicles domain); the monitor filters those out through the
        vehicles service (D11). Unbounded result set - the
        MVP's device count doesn't need pagination here; add a cap if that
        changes.
    """
    query_result = await db_session.execute(
        select(TelematicModel).where(
            TelematicModel.deleted_at.is_(None),
            TelematicModel.status == TelematicStatus.ACTIVE,
            TelematicModel.vehicle_id.is_not(None),
        )
    )
    return list(query_result.scalars().all())


async def list_all(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    status_filter: TelematicStatus | None = None,
) -> list[TelematicModel]:
    """Get a page of devices, newest first, excluding soft-deleted ones.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        status_filter: Only return devices in this status, if given.

    Returns:
        List of device records.
    """
    statement = (
        select(TelematicModel)
        .where(TelematicModel.deleted_at.is_(None))
        .order_by(TelematicModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if status_filter is not None:
        statement = statement.where(TelematicModel.status == status_filter)
    query_result = await db_session.execute(statement)
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession,
    status_filter: TelematicStatus | None = None,
) -> int:
    """Count devices that have not been soft-deleted.

    Args:
        db_session: Current database session.
        status_filter: Only count devices in this status, if given.

    Returns:
        Number of matching devices.
    """
    statement = (
        select(func.count())
        .select_from(TelematicModel)
        .where(TelematicModel.deleted_at.is_(None))
    )
    if status_filter is not None:
        statement = statement.where(TelematicModel.status == status_filter)
    query_result = await db_session.execute(statement)
    return int(query_result.scalar_one())


async def insert(
    db_session: AsyncSession,
    values: dict[str, object],
) -> TelematicModel:
    """Create a device and flush to obtain its ID and column defaults.

    Args:
        db_session: Current database session.
        values: Fields used to initialize the ORM record.

    Returns:
        The newly created device record.

    Raises:
        IntegrityError: When the serial or the vehicle assignment already
            exists; raised by the flush for the service to convert.
    """
    telematic_record = TelematicModel(**values)
    db_session.add(telematic_record)
    await db_session.flush()
    return telematic_record


async def update_fields(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
    values: dict[str, object],
    *,
    change_reason: str,
) -> TelematicModel:
    """Write the fields the service has already validated onto a device.

    Args:
        db_session: Current database session.
        telematic_record: Device record loaded in this session.
        values: Fields to set, keyed by ORM attribute name.
        change_reason: Why the row changes; recorded in ``telematic_history``
            (devices are change-tracked). The actor is unknown until
            authentication exists, so ``changed_by`` is ``None`` (DM-29).

    Returns:
        The same record, after the flush.

    Raises:
        IntegrityError: When the new values break a unique constraint
            (serial, IMEI, vehicle assignment); raised by the flush for the
            service to convert.

    Side Effects:
        Sets the change context and flushes the UPDATE; ``updated_at`` is set
        by the column's ``onupdate`` hook.
    """
    await set_change_context(db_session, changed_by=None, change_reason=change_reason)
    for field_name, value in values.items():
        setattr(telematic_record, field_name, value)
    await db_session.flush()
    return telematic_record


async def soft_delete(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
    *,
    change_reason: str,
) -> None:
    """Soft-delete a device: it leaves the system (DM-25).

    Args:
        db_session: Current database session.
        telematic_record: Device record loaded in this session.
        change_reason: Why the device left; also stored as ``status_reason``.

    Side Effects:
        Sets ``deleted_at``, status ``INACTIVE`` and no vehicle (the table's
        check constraint), records the history row and flushes;
        ``updated_at`` is set by the column's ``onupdate`` hook. The serial
        and IMEI stay on the row but are unique only among devices not
        deleted, so a replacement can reuse them or the truck (deferred.md
        item 82).
    """
    await set_change_context(db_session, changed_by=None, change_reason=change_reason)
    telematic_record.deleted_at = utc_now()
    telematic_record.status = TelematicStatus.INACTIVE
    telematic_record.status_reason = change_reason
    telematic_record.vehicle_id = None
    telematic_record.installed_at = None
    await db_session.flush()


async def find_latest_status_report(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> TelematicStatusReportModel | None:
    """Find the newest status report of a device (its current health, TX-11).

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.

    Returns:
        The report with the latest ``reported_at``, or ``None`` if the device
        never reported.
    """
    query_result = await db_session.execute(
        select(TelematicStatusReportModel)
        .where(TelematicStatusReportModel.telematic_id == telematic_id)
        .order_by(TelematicStatusReportModel.reported_at.desc())
        .limit(1)
    )
    return query_result.scalar_one_or_none()
