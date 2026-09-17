"""Repository querying the drivers tables; contains no business rules."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.drivers.models import DriverModel, DriverVehicleAssignmentModel
from app.domains.drivers.types import DriverStatus
from app.libs.common.config import settings


async def insert(db_session: AsyncSession, values: dict[str, Any]) -> DriverModel:
    """Insert a driver record into the database.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Fields used to initialize the ORM record.

    Returns:
        The newly created driver record.
    """
    driver_record = DriverModel(**values)
    db_session.add(driver_record)
    await db_session.flush()
    await db_session.refresh(driver_record)
    return driver_record


async def get_by_id(db_session: AsyncSession, driver_id: UUID) -> DriverModel | None:
    """Find a driver by ID, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.

    Returns:
        The driver record, or None if not found.
    """
    query_result = await db_session.execute(
        select(DriverModel).where(
            and_(
                DriverModel.driver_id == driver_id,
                DriverModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_phone_number(
    db_session: AsyncSession, phone_number: str
) -> DriverModel | None:
    """Find a driver by phone number, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        phone_number: Contact phone number of the driver.

    Returns:
        The driver record, or None if not found.
    """
    query_result = await db_session.execute(
        select(DriverModel).where(
            and_(
                DriverModel.phone_number == phone_number,
                DriverModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_license_number(
    db_session: AsyncSession, license_number: str
) -> DriverModel | None:
    """Find a driver by license number, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        license_number: Driving license number of the driver.

    Returns:
        The driver record, or None if not found.
    """
    query_result = await db_session.execute(
        select(DriverModel).where(
            and_(
                DriverModel.license_number == license_number,
                DriverModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def list_all(
    db_session: AsyncSession,
    skip: int = 0,
    limit: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: DriverStatus | None = None,
) -> list[DriverModel]:
    """Get a paginated list of drivers, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        skip: Number of records to skip.
        limit: Maximum number of records to return.
        status_filter: Status filter, if any.

    Returns:
        List of driver records.
    """
    conditions: list[ColumnElement[bool]] = [DriverModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(DriverModel.status == status_filter)

    query_result = await db_session.execute(
        select(DriverModel)
        .where(and_(*conditions))
        .order_by(DriverModel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession, status_filter: DriverStatus | None = None
) -> int:
    """Count the total number of drivers, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        status_filter: Status filter, if any.

    Returns:
        Total number of drivers.
    """
    conditions: list[ColumnElement[bool]] = [DriverModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(DriverModel.status == status_filter)

    query_result = await db_session.execute(
        select(func.count(DriverModel.driver_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession, driver_id: UUID, values: dict[str, Any]
) -> DriverModel | None:
    """Update the specified fields of a driver.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        values: Fields to update.

    Returns:
        The updated driver record, or None if not found.
    """
    driver_record = await get_by_id(db_session, driver_id)
    if not driver_record:
        return None

    for field_name, value in values.items():
        if hasattr(driver_record, field_name):
            setattr(driver_record, field_name, value)

    driver_record.updated_at = datetime.now(timezone.utc)
    await db_session.flush()
    await db_session.refresh(driver_record)
    return driver_record


async def soft_delete(db_session: AsyncSession, driver_id: UUID) -> DriverModel | None:
    """Soft-delete a driver by updating deleted_at and status.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.

    Returns:
        The driver record after soft delete, or None if not found.
    """
    driver_record = await get_by_id(db_session, driver_id)
    if not driver_record:
        return None

    driver_record.deleted_at = datetime.now(timezone.utc)
    driver_record.status = DriverStatus.INACTIVE
    await db_session.flush()
    await db_session.refresh(driver_record)
    return driver_record


async def get_active_assignment_by_vehicle(
    db_session: AsyncSession, vehicle_id: UUID
) -> DriverVehicleAssignmentModel | None:
    """Find the open assignment for a vehicle, if any.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The open assignment record, or None if the vehicle is unassigned.
    """
    query_result = await db_session.execute(
        select(DriverVehicleAssignmentModel).where(
            and_(
                DriverVehicleAssignmentModel.vehicle_id == vehicle_id,
                DriverVehicleAssignmentModel.unassigned_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def get_active_assignment_by_driver(
    db_session: AsyncSession, driver_id: UUID
) -> DriverVehicleAssignmentModel | None:
    """Find the open assignment for a driver, if any.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.

    Returns:
        The open assignment record, or None if the driver is unassigned.
    """
    query_result = await db_session.execute(
        select(DriverVehicleAssignmentModel).where(
            and_(
                DriverVehicleAssignmentModel.driver_id == driver_id,
                DriverVehicleAssignmentModel.unassigned_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def list_assignments_by_driver(
    db_session: AsyncSession,
    driver_id: UUID,
    *,
    offset: int,
    limit: int,
) -> list[DriverVehicleAssignmentModel]:
    """Get a driver's assignment history, newest first.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        Assignment records ordered by `assigned_at` descending.
    """
    query_result = await db_session.execute(
        select(DriverVehicleAssignmentModel)
        .where(DriverVehicleAssignmentModel.driver_id == driver_id)
        .order_by(DriverVehicleAssignmentModel.assigned_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_assignments_by_driver(db_session: AsyncSession, driver_id: UUID) -> int:
    """Count a driver's total assignment history.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.

    Returns:
        Total number of assignment records, open and closed.
    """
    query_result = await db_session.execute(
        select(func.count(DriverVehicleAssignmentModel.assignment_id)).where(
            DriverVehicleAssignmentModel.driver_id == driver_id
        )
    )
    return query_result.scalar() or 0


async def insert_assignment(
    db_session: AsyncSession,
    *,
    driver_id: UUID,
    vehicle_id: UUID,
    assigned_at: datetime,
) -> DriverVehicleAssignmentModel:
    """Open a new assignment and flush it.

    Args:
        db_session: Current database session; the repository does not
            commit the transaction.
        driver_id: Internal ID of the driver.
        vehicle_id: Internal ID of the vehicle.
        assigned_at: When the assignment begins.

    Returns:
        The newly created assignment record.

    Side Effects:
        Adds a record and calls `flush()`, which is where the two partial
        unique indexes would raise `IntegrityError` if either the driver
        or the vehicle already has a different open assignment.
    """
    assignment_record = DriverVehicleAssignmentModel(
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        assigned_at=assigned_at,
    )
    db_session.add(assignment_record)
    await db_session.flush()
    await db_session.refresh(assignment_record)
    return assignment_record


async def close_assignment(
    db_session: AsyncSession,
    assignment_record: DriverVehicleAssignmentModel,
    *,
    unassigned_at: datetime,
) -> DriverVehicleAssignmentModel:
    """Close an open assignment.

    Args:
        db_session: Current database session; the repository does not
            commit the transaction.
        assignment_record: The open assignment to close.
        unassigned_at: When the assignment ends.

    Returns:
        The closed assignment record.
    """
    assignment_record.unassigned_at = unassigned_at
    assignment_record.updated_at = datetime.now(timezone.utc)
    await db_session.flush()
    await db_session.refresh(assignment_record)
    return assignment_record
