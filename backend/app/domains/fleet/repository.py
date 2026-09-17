"""Repository querying the fleet tables; contains no business rules."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.fleet.models import FleetModel, FleetVehicleMembershipModel
from app.domains.fleet.types import FleetStatus
from app.libs.common.config import settings


async def insert(db_session: AsyncSession, values: dict[str, Any]) -> FleetModel:
    """Insert a fleet record into the database.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Fields used to initialize the ORM record.

    Returns:
        The newly created fleet record.
    """
    fleet_record = FleetModel(**values)
    db_session.add(fleet_record)
    await db_session.flush()
    await db_session.refresh(fleet_record)
    return fleet_record


async def get_by_id(db_session: AsyncSession, fleet_id: UUID) -> FleetModel | None:
    """Find a fleet by ID, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        The fleet record, or None if not found.
    """
    query_result = await db_session.execute(
        select(FleetModel).where(
            and_(
                FleetModel.fleet_id == fleet_id,
                FleetModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_fleet_code(
    db_session: AsyncSession, fleet_code: str
) -> FleetModel | None:
    """Find a fleet by its natural code, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        fleet_code: Natural business key of the fleet.

    Returns:
        The fleet record, or None if not found.
    """
    query_result = await db_session.execute(
        select(FleetModel).where(
            and_(
                FleetModel.fleet_code == fleet_code,
                FleetModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def list_all(
    db_session: AsyncSession,
    skip: int = 0,
    limit: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: FleetStatus | None = None,
) -> list[FleetModel]:
    """Get a paginated list of fleets, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        skip: Number of records to skip.
        limit: Maximum number of records to return.
        status_filter: Status filter, if any.

    Returns:
        List of fleet records.
    """
    conditions: list[ColumnElement[bool]] = [FleetModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(FleetModel.status == status_filter)

    query_result = await db_session.execute(
        select(FleetModel)
        .where(and_(*conditions))
        .order_by(FleetModel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession, status_filter: FleetStatus | None = None
) -> int:
    """Count the total number of fleets, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        status_filter: Status filter, if any.

    Returns:
        Total number of fleets.
    """
    conditions: list[ColumnElement[bool]] = [FleetModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(FleetModel.status == status_filter)

    query_result = await db_session.execute(
        select(func.count(FleetModel.fleet_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession, fleet_id: UUID, values: dict[str, Any]
) -> FleetModel | None:
    """Update the specified fields of a fleet.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        values: Fields to update.

    Returns:
        The updated fleet record, or None if not found.
    """
    fleet_record = await get_by_id(db_session, fleet_id)
    if not fleet_record:
        return None

    for field_name, value in values.items():
        if hasattr(fleet_record, field_name):
            setattr(fleet_record, field_name, value)

    fleet_record.updated_at = datetime.now(timezone.utc)
    await db_session.flush()
    await db_session.refresh(fleet_record)
    return fleet_record


async def soft_delete(db_session: AsyncSession, fleet_id: UUID) -> FleetModel | None:
    """Soft-delete a fleet by updating deleted_at and status.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        The fleet record after soft delete, or None if not found.
    """
    fleet_record = await get_by_id(db_session, fleet_id)
    if not fleet_record:
        return None

    fleet_record.deleted_at = datetime.now(timezone.utc)
    fleet_record.status = FleetStatus.INACTIVE
    await db_session.flush()
    await db_session.refresh(fleet_record)
    return fleet_record


async def get_active_membership_by_vehicle(
    db_session: AsyncSession, vehicle_id: UUID
) -> FleetVehicleMembershipModel | None:
    """Find the open membership for a vehicle, if any.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The open membership record, or None if the vehicle is in no fleet.
    """
    query_result = await db_session.execute(
        select(FleetVehicleMembershipModel).where(
            and_(
                FleetVehicleMembershipModel.vehicle_id == vehicle_id,
                FleetVehicleMembershipModel.left_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def list_active_memberships_by_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    offset: int,
    limit: int,
) -> list[FleetVehicleMembershipModel]:
    """Get a fleet's current (open) memberships, newest first.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        Open membership records ordered by `joined_at` descending.
    """
    query_result = await db_session.execute(
        select(FleetVehicleMembershipModel)
        .where(
            and_(
                FleetVehicleMembershipModel.fleet_id == fleet_id,
                FleetVehicleMembershipModel.left_at.is_(None),
            )
        )
        .order_by(FleetVehicleMembershipModel.joined_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def list_all_active_memberships_by_fleet(
    db_session: AsyncSession, fleet_id: UUID
) -> list[FleetVehicleMembershipModel]:
    """Get every open membership of a fleet, with no pagination.

    For internal use only (closing every membership before a fleet is
    soft-deleted) - unlike `list_active_memberships_by_fleet`, this must
    never silently truncate at a page-size cap, since a fleet with more
    members than one page would otherwise be left with open memberships
    after being soft-deleted.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        Every open membership record for the fleet.
    """
    query_result = await db_session.execute(
        select(FleetVehicleMembershipModel).where(
            and_(
                FleetVehicleMembershipModel.fleet_id == fleet_id,
                FleetVehicleMembershipModel.left_at.is_(None),
            )
        )
    )
    return list(query_result.scalars().all())


async def count_active_memberships_by_fleet(
    db_session: AsyncSession, fleet_id: UUID
) -> int:
    """Count a fleet's current (open) memberships.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        Number of vehicles currently in the fleet.
    """
    query_result = await db_session.execute(
        select(func.count(FleetVehicleMembershipModel.membership_id)).where(
            and_(
                FleetVehicleMembershipModel.fleet_id == fleet_id,
                FleetVehicleMembershipModel.left_at.is_(None),
            )
        )
    )
    return query_result.scalar() or 0


async def list_memberships_by_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    offset: int,
    limit: int,
) -> list[FleetVehicleMembershipModel]:
    """Get a fleet's full membership history, newest first.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        Membership records (open and closed) ordered by `joined_at` descending.
    """
    query_result = await db_session.execute(
        select(FleetVehicleMembershipModel)
        .where(FleetVehicleMembershipModel.fleet_id == fleet_id)
        .order_by(FleetVehicleMembershipModel.joined_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_memberships_by_fleet(db_session: AsyncSession, fleet_id: UUID) -> int:
    """Count a fleet's total membership history.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        Total number of membership records, open and closed.
    """
    query_result = await db_session.execute(
        select(func.count(FleetVehicleMembershipModel.membership_id)).where(
            FleetVehicleMembershipModel.fleet_id == fleet_id
        )
    )
    return query_result.scalar() or 0


async def insert_membership(
    db_session: AsyncSession,
    *,
    fleet_id: UUID,
    vehicle_id: UUID,
    joined_at: datetime,
) -> FleetVehicleMembershipModel:
    """Open a new membership and flush it.

    Args:
        db_session: Current database session; the repository does not
            commit the transaction.
        fleet_id: Internal ID of the fleet.
        vehicle_id: Internal ID of the vehicle.
        joined_at: When the membership begins.

    Returns:
        The newly created membership record.

    Side Effects:
        Adds a record and calls `flush()`, which is where the partial
        unique index would raise `IntegrityError` if the vehicle already
        has a different open membership.
    """
    membership_record = FleetVehicleMembershipModel(
        fleet_id=fleet_id,
        vehicle_id=vehicle_id,
        joined_at=joined_at,
    )
    db_session.add(membership_record)
    await db_session.flush()
    await db_session.refresh(membership_record)
    return membership_record


async def close_membership(
    db_session: AsyncSession,
    membership_record: FleetVehicleMembershipModel,
    *,
    left_at: datetime,
) -> FleetVehicleMembershipModel:
    """Close an open membership.

    Args:
        db_session: Current database session; the repository does not
            commit the transaction.
        membership_record: The open membership to close.
        left_at: When the membership ends.

    Returns:
        The closed membership record.
    """
    membership_record.left_at = left_at
    membership_record.updated_at = datetime.now(timezone.utc)
    await db_session.flush()
    await db_session.refresh(membership_record)
    return membership_record
