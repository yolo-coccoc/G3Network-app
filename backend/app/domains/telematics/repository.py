"""Repository for querying the Telematic device table.

This repository must only be called from the ``telematics`` domain's service.
Other domains must use the public service so they do not depend directly on
this domain's model or internal SQL.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telematics.models import TelematicModel
from app.domains.telematics.types import TelematicStatus, TelematicVehicleMapping


async def get_by_id(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> TelematicModel | None:
    """Get a device that has not been soft-deleted, by ID."""
    query_result = await db_session.execute(
        select(TelematicModel).where(
            TelematicModel.telematic_id == telematic_id,
            TelematicModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def get_by_serial(
    db_session: AsyncSession,
    serial: str,
    include_deleted: bool = False,
) -> TelematicModel | None:
    """Get a device by serial."""
    stmt = select(TelematicModel).where(TelematicModel.telematic_serial == serial)
    if not include_deleted:
        stmt = stmt.where(TelematicModel.deleted_at.is_(None))
    query_result = await db_session.execute(stmt)
    return query_result.scalar_one_or_none()


async def find_mapping_by_serial(
    db_session: AsyncSession,
    serial: str,
) -> TelematicVehicleMapping | None:
    """Find a device-vehicle mapping by serial for ingestion.

    Args:
        db: Database session owned by the entry boundary.
        serial: Physical serial of the device to look up.

    Returns:
        Tuple ``(telematic_id, vehicle_id)`` when the device is still active
        in the system and has been assigned a vehicle; ``None`` if there is
        no valid mapping.
    """
    query_result = await db_session.execute(
        select(TelematicModel.telematic_id, TelematicModel.vehicle_id).where(
            TelematicModel.telematic_serial == serial,
            TelematicModel.deleted_at.is_(None),
            TelematicModel.vehicle_id.is_not(None),
        )
    )
    mapping_row = query_result.one_or_none()
    return (
        TelematicVehicleMapping(
            telematic_id=mapping_row.telematic_id,
            vehicle_id=mapping_row.vehicle_id,
        )
        if mapping_row
        else None
    )


async def list_active_with_vehicle(
    db_session: AsyncSession,
) -> list[TelematicModel]:
    """Get active devices assigned to a vehicle, for the F-J1/F-J3 health monitor.

    Args:
        db_session: Current database session.

    Returns:
        Non-soft-deleted, ``ACTIVE`` devices with a non-``None``
        ``vehicle_id``. A device that's soft-deleted, explicitly
        ``INACTIVE``/``MAINTENANCE``, or not yet assigned to a vehicle is
        excluded - a device deliberately taken offline being silent is
        expected, not something to alert on. Unbounded result set - the
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
    skip: int,
    limit: int,
    status: object | None,
) -> list[TelematicModel]:
    """Get the list of devices that have not been deleted."""
    stmt = (
        select(TelematicModel)
        .where(TelematicModel.deleted_at.is_(None))
        .order_by(TelematicModel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(TelematicModel.status == status)
    return list((await db_session.execute(stmt)).scalars().all())


async def count(db_session: AsyncSession, status: object | None) -> int:
    """Count devices that have not been deleted."""
    stmt = (
        select(func.count())
        .select_from(TelematicModel)
        .where(TelematicModel.deleted_at.is_(None))
    )
    if status is not None:
        stmt = stmt.where(TelematicModel.status == status)
    return int((await db_session.execute(stmt)).scalar_one())


async def insert(
    db_session: AsyncSession,
    values: dict[str, object],
) -> TelematicModel:
    """Create a device and flush to obtain its ID."""
    telematic_record = TelematicModel(**values)
    db_session.add(telematic_record)
    await db_session.flush()
    return telematic_record


async def update_fields(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
    values: dict[str, object],
) -> TelematicModel:
    """Update the fields the service has allowed."""
    for field_name, value in values.items():
        setattr(telematic_record, field_name, value)
    await db_session.flush()
    return telematic_record


async def soft_delete(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
) -> None:
    """Mark a device as soft-deleted."""
    telematic_record.deleted_at = datetime.now(timezone.utc)
    telematic_record.updated_at = telematic_record.deleted_at
    await db_session.flush()
