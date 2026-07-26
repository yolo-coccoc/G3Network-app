"""Repository layer for Vehicle domain - handles database queries."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.vehicles.models import Vehicle
from app.domains.vehicles.types import VehicleStatus


async def create_vehicle(db: AsyncSession, vehicle_data: dict[str, Any]) -> Vehicle:
    """Create a new vehicle in database.

    Args:
        db: Async database session
        vehicle_data: Dictionary containing vehicle fields

    Returns:
        Created Vehicle instance
    """
    vehicle = Vehicle(**vehicle_data)
    db.add(vehicle)
    await db.flush()
    await db.refresh(vehicle)
    return vehicle


async def get_vehicle_by_id(db: AsyncSession, vehicle_id: UUID) -> Vehicle | None:
    """Get vehicle by ID (excluding soft-deleted).

    Args:
        db: Async database session
        vehicle_id: Vehicle internal ID (UUID)

    Returns:
        Vehicle instance or None if not found
    """
    result = await db.execute(
        select(Vehicle).where(
            and_(Vehicle.vehicle_id == vehicle_id, Vehicle.deleted_at.is_(None))
        )
    )
    return result.scalar_one_or_none()


async def get_vehicle_by_plate(db: AsyncSession, license_plate: str) -> Vehicle | None:
    """Get vehicle by license plate (excluding soft-deleted).

    Args:
        db: Async database session
        license_plate: Vehicle license plate

    Returns:
        Vehicle instance or None if not found
    """
    result = await db.execute(
        select(Vehicle).where(
            and_(Vehicle.license_plate == license_plate, Vehicle.deleted_at.is_(None))
        )
    )
    return result.scalar_one_or_none()


async def get_vehicle_by_vin(db: AsyncSession, vin: str) -> Vehicle | None:
    """Get vehicle by VIN (excluding soft-deleted).

    Args:
        db: Async database session
        vin: Vehicle Identification Number

    Returns:
        Vehicle instance or None if not found
    """
    result = await db.execute(
        select(Vehicle).where(and_(Vehicle.vin == vin, Vehicle.deleted_at.is_(None)))
    )
    return result.scalar_one_or_none()


async def get_vehicles(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 10,
    status_filter: VehicleStatus | None = None,
) -> list[Vehicle]:
    """Get list of vehicles with pagination (excluding soft-deleted).

    Args:
        db: Async database session
        skip: Number of records to skip
        limit: Maximum number of records to return
        status_filter: Optional status filter

    Returns:
        List of Vehicle instances
    """
    conditions: list[ColumnElement[bool]] = [Vehicle.deleted_at.is_(None)]

    if status_filter:
        conditions.append(Vehicle.status == status_filter)

    result = await db.execute(
        select(Vehicle)
        .where(and_(*conditions))
        .order_by(Vehicle.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_vehicles(
    db: AsyncSession, status_filter: VehicleStatus | None = None
) -> int:
    """Count total vehicles (excluding soft-deleted).

    Args:
        db: Async database session
        status_filter: Optional status filter

    Returns:
        Total count of vehicles
    """
    conditions: list[ColumnElement[bool]] = [Vehicle.deleted_at.is_(None)]

    if status_filter:
        conditions.append(Vehicle.status == status_filter)

    result = await db.execute(
        select(func.count(Vehicle.vehicle_id)).where(and_(*conditions))
    )
    return result.scalar() or 0


async def update_vehicle(
    db: AsyncSession, vehicle_id: UUID, update_data: dict[str, Any]
) -> Vehicle | None:
    """Update vehicle fields.

    Args:
        db: Async database session
        vehicle_id: Vehicle internal ID (UUID)
        update_data: Dictionary of fields to update

    Returns:
        Updated Vehicle instance or None if not found
    """
    vehicle = await get_vehicle_by_id(db, vehicle_id)
    if not vehicle:
        return None

    for key, value in update_data.items():
        if hasattr(vehicle, key):
            setattr(vehicle, key, value)

    vehicle.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(vehicle)
    return vehicle


async def soft_delete_vehicle(db: AsyncSession, vehicle_id: UUID) -> Vehicle | None:
    """Soft delete vehicle by setting deleted_at timestamp.

    Args:
        db: Async database session
        vehicle_id: Vehicle ID (UUID)

    Returns:
        Soft-deleted Vehicle instance or None if not found
    """
    vehicle = await get_vehicle_by_id(db, vehicle_id)
    if not vehicle:
        return None

    vehicle.deleted_at = datetime.now(timezone.utc)
    vehicle.status = VehicleStatus.DECOMMISSIONED
    await db.flush()
    await db.refresh(vehicle)
    return vehicle
