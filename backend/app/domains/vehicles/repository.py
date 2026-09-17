"""Repository querying the vehicles table; contains no business rules."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.types import VehicleActivationStatus, VehicleStatus
from app.libs.common.config import settings


async def insert(db_session: AsyncSession, values: dict[str, Any]) -> VehicleModel:
    """Insert a vehicle record into the database.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Fields used to initialize the ORM record.

    Returns:
        The newly created vehicle record.
    """
    vehicle_record = VehicleModel(**values)
    db_session.add(vehicle_record)
    await db_session.flush()
    await db_session.refresh(vehicle_record)
    return vehicle_record


async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel | None:
    """Find a vehicle by ID, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The vehicle record, or None if not found.
    """
    query_result = await db_session.execute(
        select(VehicleModel).where(
            and_(
                VehicleModel.vehicle_id == vehicle_id,
                VehicleModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_license_plate(
    db_session: AsyncSession, license_plate: str
) -> VehicleModel | None:
    """Find a vehicle by license plate, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        license_plate: License plate of the vehicle.

    Returns:
        The vehicle record, or None if not found.
    """
    query_result = await db_session.execute(
        select(VehicleModel).where(
            and_(
                VehicleModel.license_plate == license_plate,
                VehicleModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_vin(db_session: AsyncSession, vin: str) -> VehicleModel | None:
    """Find a vehicle by VIN, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        vin: VIN (chassis number) of the vehicle.

    Returns:
        The vehicle record, or None if not found.
    """
    query_result = await db_session.execute(
        select(VehicleModel).where(
            and_(VehicleModel.vin == vin, VehicleModel.deleted_at.is_(None))
        )
    )
    return query_result.scalar_one_or_none()


async def list_all(
    db_session: AsyncSession,
    skip: int = 0,
    limit: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: VehicleStatus | None = None,
) -> list[VehicleModel]:
    """Get a paginated list of vehicles, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        skip: Number of records to skip.
        limit: Maximum number of records to return.
        status_filter: Status filter, if any.

    Returns:
        List of vehicle records.
    """
    conditions: list[ColumnElement[bool]] = [VehicleModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(VehicleModel.status == status_filter)

    query_result = await db_session.execute(
        select(VehicleModel)
        .where(and_(*conditions))
        .order_by(VehicleModel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession, status_filter: VehicleStatus | None = None
) -> int:
    """Count the total number of vehicles, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        status_filter: Status filter, if any.

    Returns:
        Total number of vehicles.
    """
    conditions: list[ColumnElement[bool]] = [VehicleModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(VehicleModel.status == status_filter)

    query_result = await db_session.execute(
        select(func.count(VehicleModel.vehicle_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def count_by_activation_status(
    db_session: AsyncSession, activation_status: VehicleActivationStatus
) -> int:
    """Count active vehicles at a given activation status (F-F2).

    Args:
        db_session: Current database session.
        activation_status: Activation status to count.

    Returns:
        Number of non-soft-deleted vehicles at that activation status.
    """
    query_result = await db_session.execute(
        select(func.count(VehicleModel.vehicle_id)).where(
            and_(
                VehicleModel.activation_status == activation_status,
                VehicleModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession, vehicle_id: UUID, values: dict[str, Any]
) -> VehicleModel | None:
    """Update the specified fields of a vehicle.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        values: Fields to update.

    Returns:
        The updated vehicle record, or None if not found.
    """
    vehicle_record = await get_by_id(db_session, vehicle_id)
    if not vehicle_record:
        return None

    for field_name, value in values.items():
        if hasattr(vehicle_record, field_name):
            setattr(vehicle_record, field_name, value)

    vehicle_record.updated_at = datetime.now(timezone.utc)
    await db_session.flush()
    await db_session.refresh(vehicle_record)
    return vehicle_record


async def soft_delete(
    db_session: AsyncSession, vehicle_id: UUID
) -> VehicleModel | None:
    """Soft-delete a vehicle by updating deleted_at and status.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The vehicle record after soft delete, or None if not found.
    """
    vehicle_record = await get_by_id(db_session, vehicle_id)
    if not vehicle_record:
        return None

    vehicle_record.deleted_at = datetime.now(timezone.utc)
    vehicle_record.status = VehicleStatus.DECOMMISSIONED
    await db_session.flush()
    await db_session.refresh(vehicle_record)
    return vehicle_record
