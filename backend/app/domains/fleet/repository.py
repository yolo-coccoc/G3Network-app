"""Repository querying the fleet tables; contains no business rules."""

from datetime import datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from geoalchemy2.elements import WKBElement
from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.fleet.models import (
    FleetModel,
    FleetVehicleMembershipModel,
    GeofenceModel,
)
from app.domains.fleet.types import FleetStatus
from app.libs.common.clock import utc_now


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


def _contains_pattern(search_text: str) -> str:
    """Build an ``ILIKE`` pattern matching a literal substring.

    ``%``, ``_`` and the escape character itself are escaped so a search
    for ``"50%"`` matches that text instead of acting as a wildcard.

    Args:
        search_text: Raw substring typed by the caller.

    Returns:
        ``%<escaped text>%``, to use with ``escape="\\"``.
    """
    escaped_text = (
        search_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped_text}%"


def _fleet_list_conditions(
    *,
    status_filter: FleetStatus | None,
    search_text: str | None,
    vehicle_id: UUID | None,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by `list_all` and `count`.

    Args:
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the name or fleet code,
            if any.
        vehicle_id: Only the fleet this vehicle is currently (open
            membership) a member of, if given.

    Returns:
        Conditions to AND together; always excludes soft-deleted fleets.
    """
    conditions: list[ColumnElement[bool]] = [FleetModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(FleetModel.status == status_filter)
    if search_text:
        pattern = _contains_pattern(search_text)
        conditions.append(
            or_(
                FleetModel.name.ilike(pattern, escape="\\"),
                FleetModel.fleet_code.ilike(pattern, escape="\\"),
            )
        )
    if vehicle_id is not None:
        conditions.append(
            FleetModel.fleet_id.in_(
                select(FleetVehicleMembershipModel.fleet_id).where(
                    FleetVehicleMembershipModel.vehicle_id == vehicle_id,
                    FleetVehicleMembershipModel.left_at.is_(None),
                )
            )
        )
    return conditions


async def list_all(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    status_filter: FleetStatus | None = None,
    search_text: str | None = None,
    vehicle_id: UUID | None = None,
) -> list[FleetModel]:
    """Get a paginated list of fleets, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the name or fleet code,
            if any.
        vehicle_id: Only the fleet this vehicle is currently a member of,
            if given.

    Returns:
        List of fleet records, newest first.
    """
    conditions = _fleet_list_conditions(
        status_filter=status_filter, search_text=search_text, vehicle_id=vehicle_id
    )
    query_result = await db_session.execute(
        select(FleetModel)
        .where(and_(*conditions))
        .order_by(FleetModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession,
    *,
    status_filter: FleetStatus | None = None,
    search_text: str | None = None,
    vehicle_id: UUID | None = None,
) -> int:
    """Count the fleets matching the same filters as `list_all`.

    Args:
        db_session: Current database session.
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the name or fleet code,
            if any.
        vehicle_id: Only the fleet this vehicle is currently a member of,
            if given.

    Returns:
        Total number of matching fleets.
    """
    conditions = _fleet_list_conditions(
        status_filter=status_filter, search_text=search_text, vehicle_id=vehicle_id
    )
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

    fleet_record.updated_at = utc_now()
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

    fleet_record.deleted_at = utc_now()
    fleet_record.status = FleetStatus.INACTIVE
    await db_session.flush()
    await db_session.refresh(fleet_record)
    return fleet_record


async def find_active_membership_by_vehicle(
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
    membership_record.updated_at = utc_now()
    await db_session.flush()
    await db_session.refresh(membership_record)
    return membership_record


async def get_membership_by_id(
    db_session: AsyncSession, membership_id: UUID
) -> FleetVehicleMembershipModel | None:
    """Find a membership by ID, open or closed.

    Args:
        db_session: Current database session.
        membership_id: Internal ID of the membership.

    Returns:
        The membership record, or None if not found.
    """
    query_result = await db_session.execute(
        select(FleetVehicleMembershipModel).where(
            FleetVehicleMembershipModel.membership_id == membership_id
        )
    )
    return query_result.scalar_one_or_none()


async def insert_geofence(
    db_session: AsyncSession,
    *,
    fleet_id: UUID,
    name: str,
    boundary: WKBElement,
) -> GeofenceModel:
    """Insert a geofence and flush it.

    Args:
        db_session: Current database session; the repository does not
            commit the transaction.
        fleet_id: Internal ID of the owning fleet.
        name: Display name of the geofence.
        boundary: The area as a WKB polygon with SRID 4326.

    Returns:
        The newly created geofence record.
    """
    geofence_record = GeofenceModel(fleet_id=fleet_id, name=name, boundary=boundary)
    db_session.add(geofence_record)
    await db_session.flush()
    await db_session.refresh(geofence_record)
    return geofence_record


async def get_geofence_by_id(
    db_session: AsyncSession, geofence_id: UUID
) -> GeofenceModel | None:
    """Find a live geofence by ID, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        geofence_id: Internal ID of the geofence.

    Returns:
        The geofence record, or None if not found.
    """
    query_result = await db_session.execute(
        select(GeofenceModel).where(
            GeofenceModel.geofence_id == geofence_id,
            GeofenceModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def list_geofences_by_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    offset: int,
    limit: int,
) -> list[GeofenceModel]:
    """Get a fleet's live geofences, newest first.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        Live geofence records ordered by `created_at` descending.
    """
    query_result = await db_session.execute(
        select(GeofenceModel)
        .where(
            GeofenceModel.fleet_id == fleet_id,
            GeofenceModel.deleted_at.is_(None),
        )
        .order_by(GeofenceModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_geofences(db_session: AsyncSession, fleet_id: UUID) -> int:
    """Count a fleet's live geofences.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        Number of geofences not soft-deleted.
    """
    query_result = await db_session.execute(
        select(func.count(GeofenceModel.geofence_id)).where(
            GeofenceModel.fleet_id == fleet_id,
            GeofenceModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar() or 0


async def update_geofence_fields(
    db_session: AsyncSession,
    geofence_record: GeofenceModel,
    values: dict[str, Any],
) -> GeofenceModel:
    """Update the given fields of a loaded geofence and flush.

    Args:
        db_session: Current database session; the repository does not
            commit the transaction.
        geofence_record: The live geofence to update, loaded in this session.
        values: Column names and their new values (`name`, `boundary`).

    Returns:
        The refreshed geofence record.
    """
    for field_name, value in values.items():
        setattr(geofence_record, field_name, value)
    geofence_record.updated_at = utc_now()
    await db_session.flush()
    await db_session.refresh(geofence_record)
    return geofence_record


async def soft_delete_geofence(
    db_session: AsyncSession, geofence_record: GeofenceModel
) -> GeofenceModel:
    """Soft-delete a loaded geofence by stamping `deleted_at`.

    Args:
        db_session: Current database session; the repository does not
            commit the transaction.
        geofence_record: The live geofence to delete, loaded in this session.

    Returns:
        The geofence record after the soft delete.
    """
    geofence_record.deleted_at = utc_now()
    await db_session.flush()
    return geofence_record


async def list_geofences_covering_point(
    db_session: AsyncSession, fleet_id: UUID, location: WKBElement
) -> list[GeofenceModel]:
    """Get a fleet's live geofences whose boundary covers a point.

    Uses `ST_Covers` on geography, so a point exactly on the boundary counts
    as inside. No spatial index is involved (see `GeofenceModel`): the
    `fleet_id` filter leaves only a handful of rows to test.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        location: The point as a WKB point with SRID 4326.

    Returns:
        Matching geofence records, oldest first (a stable order for a caller
        that compares successive results).
    """
    # The bound WKB value arrives as `geometry`; cast it so PostgreSQL picks
    # the geography overload of ST_Covers (geodesic, not planar degrees).
    point = cast(location, Geography(geometry_type="POINT", srid=4326))
    query_result = await db_session.execute(
        select(GeofenceModel)
        .where(
            GeofenceModel.fleet_id == fleet_id,
            GeofenceModel.deleted_at.is_(None),
            func.ST_Covers(GeofenceModel.boundary, point),
        )
        .order_by(GeofenceModel.created_at.asc())
    )
    return list(query_result.scalars().all())
