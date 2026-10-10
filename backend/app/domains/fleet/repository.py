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
    FleetUserAssignmentModel,
    FleetVehicleMembershipModel,
    GeofenceModel,
)
from app.libs.common.clock import utc_now
from app.libs.db.history import set_change_context

# Fixed history reasons of routine fleet actions (a typed reason overrides).
FLEET_EDITED_REASON = "Fleet details edited"
FLEET_DELETED_REASON = "Fleet deleted"


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


async def get_by_id(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    organization_id: UUID | None = None,
) -> FleetModel | None:
    """Find a fleet by ID, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        organization_id: Data scope (ACC-15): only a fleet owned by this
            organization is found; `None` means no restriction.

    Returns:
        The fleet record, or None if not found or out of scope.
    """
    conditions = [FleetModel.fleet_id == fleet_id, FleetModel.deleted_at.is_(None)]
    if organization_id is not None:
        conditions.append(FleetModel.organization_id == organization_id)
    query_result = await db_session.execute(select(FleetModel).where(and_(*conditions)))
    return query_result.scalar_one_or_none()


async def find_by_fleet_code(
    db_session: AsyncSession, organization_id: UUID, fleet_code: str
) -> FleetModel | None:
    """Find a fleet by its code inside one organization, excluding soft-deleted.

    Args:
        db_session: Current database session.
        organization_id: Organization whose fleets are searched (a code is
            unique per organization, FL-08).
        fleet_code: Code of the fleet.

    Returns:
        The fleet record, or None if not found.
    """
    query_result = await db_session.execute(
        select(FleetModel).where(
            and_(
                FleetModel.organization_id == organization_id,
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
    search_text: str | None,
    vehicle_id: UUID | None,
    organization_id: UUID | None,
    fleet_ids: frozenset[UUID] | None,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by `list_all` and `count`.

    Args:
        search_text: Case-insensitive substring of the name or fleet code,
            if any.
        vehicle_id: Only the fleet this vehicle is currently (open
            membership) a member of, if given.
        organization_id: Data scope; `None` means every organization.
        fleet_ids: Only these fleets (the caller's fleet limit, FL-10, or the
            descendants of a parent); `None` means no restriction, an empty
            set matches nothing.

    Returns:
        Conditions to AND together; always excludes soft-deleted fleets.
    """
    conditions: list[ColumnElement[bool]] = [FleetModel.deleted_at.is_(None)]
    if fleet_ids is not None:
        conditions.append(FleetModel.fleet_id.in_(fleet_ids))
    if organization_id is not None:
        conditions.append(FleetModel.organization_id == organization_id)

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
                    FleetVehicleMembershipModel.removed_at.is_(None),
                )
            )
        )
    return conditions


async def list_all(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    search_text: str | None = None,
    vehicle_id: UUID | None = None,
    organization_id: UUID | None = None,
    fleet_ids: frozenset[UUID] | None = None,
) -> list[FleetModel]:
    """Get a paginated list of fleets, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        search_text: Case-insensitive substring of the name or fleet code,
            if any.
        vehicle_id: Only the fleet this vehicle is currently a member of,
            if given.
        organization_id: Data scope; `None` means every organization.
        fleet_ids: Only these fleets, if given (see `_fleet_list_conditions`).

    Returns:
        List of fleet records, newest first.
    """
    conditions = _fleet_list_conditions(
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
        fleet_ids=fleet_ids,
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
    search_text: str | None = None,
    vehicle_id: UUID | None = None,
    organization_id: UUID | None = None,
    fleet_ids: frozenset[UUID] | None = None,
) -> int:
    """Count the fleets matching the same filters as `list_all`.

    Args:
        db_session: Current database session.
        search_text: Case-insensitive substring of the name or fleet code,
            if any.
        vehicle_id: Only the fleet this vehicle is currently a member of,
            if given.
        organization_id: Data scope; `None` means every organization.
        fleet_ids: Only these fleets, if given (see `_fleet_list_conditions`).

    Returns:
        Total number of matching fleets.
    """
    conditions = _fleet_list_conditions(
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
        fleet_ids=fleet_ids,
    )
    query_result = await db_session.execute(
        select(func.count(FleetModel.fleet_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession,
    fleet_id: UUID,
    values: dict[str, Any],
    *,
    changed_by: UUID | None = None,
    change_reason: str = FLEET_EDITED_REASON,
) -> FleetModel | None:
    """Update the specified fields of a fleet.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        values: Fields to update.
        changed_by: The acting user, recorded in the fleet history.
        change_reason: Why the fleet changes; a fixed text for a routine edit.

    Returns:
        The updated fleet record, or None if not found.
    """
    fleet_record = await get_by_id(db_session, fleet_id)
    if not fleet_record:
        return None

    # Fleets are change-tracked: record who changed them and why.
    await set_change_context(
        db_session, changed_by=changed_by, change_reason=change_reason
    )
    for field_name, value in values.items():
        if hasattr(fleet_record, field_name):
            setattr(fleet_record, field_name, value)

    fleet_record.updated_at = utc_now()
    await db_session.flush()
    await db_session.refresh(fleet_record)
    return fleet_record


async def soft_delete(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    changed_by: UUID | None = None,
    change_reason: str = FLEET_DELETED_REASON,
) -> FleetModel | None:
    """Soft-delete a fleet by stamping deleted_at.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        changed_by: The acting user, recorded in the fleet history.
        change_reason: Why the fleet is deleted.

    Returns:
        The fleet record after soft delete, or None if not found.
    """
    fleet_record = await get_by_id(db_session, fleet_id)
    if not fleet_record:
        return None

    await set_change_context(
        db_session, changed_by=changed_by, change_reason=change_reason
    )
    fleet_record.deleted_at = utc_now()
    await db_session.flush()
    await db_session.refresh(fleet_record)
    return fleet_record


async def count_child_fleets(db_session: AsyncSession, fleet_id: UUID) -> int:
    """Count the live fleets sitting directly under a fleet.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the parent fleet.

    Returns:
        Number of fleets not soft-deleted whose `parent_fleet_id` is
        `fleet_id`.
    """
    query_result = await db_session.execute(
        select(func.count(FleetModel.fleet_id)).where(
            FleetModel.parent_fleet_id == fleet_id,
            FleetModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar() or 0


async def list_child_fleet_ids(db_session: AsyncSession, fleet_id: UUID) -> list[UUID]:
    """List the live fleets sitting directly under a fleet.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the parent fleet.

    Returns:
        IDs of the fleets not soft-deleted whose `parent_fleet_id` is
        `fleet_id`, oldest first.
    """
    query_result = await db_session.execute(
        select(FleetModel.fleet_id)
        .where(
            FleetModel.parent_fleet_id == fleet_id,
            FleetModel.deleted_at.is_(None),
        )
        .order_by(FleetModel.created_at.asc())
    )
    return list(query_result.scalars().all())


async def list_by_organization(
    db_session: AsyncSession, organization_id: UUID
) -> list[FleetModel]:
    """List every live fleet of one organization, with no pagination.

    For the fleet tree (FLT-01): an organization's fleets are a handful to a
    few hundred rows, and the tree needs all of them to be assembled.

    Args:
        db_session: Current database session.
        organization_id: Organization whose fleets are read.

    Returns:
        Fleet records not soft-deleted, oldest first.
    """
    query_result = await db_session.execute(
        select(FleetModel)
        .where(
            FleetModel.organization_id == organization_id,
            FleetModel.deleted_at.is_(None),
        )
        .order_by(FleetModel.created_at.asc())
    )
    return list(query_result.scalars().all())


async def list_active_vehicle_ids_by_fleet_ids(
    db_session: AsyncSession, fleet_ids: frozenset[UUID]
) -> list[UUID]:
    """List the vehicles with an open membership in any of the given fleets.

    Args:
        db_session: Current database session.
        fleet_ids: The fleets to read.

    Returns:
        Vehicle IDs, each once (a vehicle has at most one open membership),
        oldest member first.
    """
    query_result = await db_session.execute(
        select(FleetVehicleMembershipModel.vehicle_id)
        .where(
            FleetVehicleMembershipModel.fleet_id.in_(fleet_ids),
            FleetVehicleMembershipModel.removed_at.is_(None),
        )
        .order_by(FleetVehicleMembershipModel.added_at.asc())
    )
    return list(query_result.scalars().all())


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
                FleetVehicleMembershipModel.removed_at.is_(None),
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
        Open membership records ordered by `added_at` descending.
    """
    query_result = await db_session.execute(
        select(FleetVehicleMembershipModel)
        .where(
            and_(
                FleetVehicleMembershipModel.fleet_id == fleet_id,
                FleetVehicleMembershipModel.removed_at.is_(None),
            )
        )
        .order_by(FleetVehicleMembershipModel.added_at.desc())
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
                FleetVehicleMembershipModel.removed_at.is_(None),
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
        select(
            func.count(FleetVehicleMembershipModel.fleet_vehicle_membership_id)
        ).where(
            and_(
                FleetVehicleMembershipModel.fleet_id == fleet_id,
                FleetVehicleMembershipModel.removed_at.is_(None),
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
        Membership records (open and closed) ordered by `added_at` descending.
    """
    query_result = await db_session.execute(
        select(FleetVehicleMembershipModel)
        .where(FleetVehicleMembershipModel.fleet_id == fleet_id)
        .order_by(FleetVehicleMembershipModel.added_at.desc())
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
        select(
            func.count(FleetVehicleMembershipModel.fleet_vehicle_membership_id)
        ).where(FleetVehicleMembershipModel.fleet_id == fleet_id)
    )
    return query_result.scalar() or 0


async def insert_membership(
    db_session: AsyncSession,
    *,
    fleet_id: UUID,
    vehicle_id: UUID,
    added_at: datetime,
    added_by: UUID | None = None,
) -> FleetVehicleMembershipModel:
    """Open a new membership and flush it.

    Args:
        db_session: Current database session; the repository does not
            commit the transaction.
        fleet_id: Internal ID of the fleet.
        vehicle_id: Internal ID of the vehicle.
        added_at: When the vehicle is added to the fleet.
        added_by: User who added the vehicle.

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
        added_at=added_at,
        added_by=added_by,
    )
    db_session.add(membership_record)
    await db_session.flush()
    await db_session.refresh(membership_record)
    return membership_record


async def close_membership(
    db_session: AsyncSession,
    membership_record: FleetVehicleMembershipModel,
    *,
    removed_at: datetime,
    removed_by: UUID | None = None,
) -> FleetVehicleMembershipModel:
    """Close an open membership.

    Args:
        db_session: Current database session; the repository does not
            commit the transaction.
        membership_record: The open membership to close.
        removed_at: When the vehicle is removed from the fleet.
        removed_by: User who removed it; ``None`` when the system closes the
            period.

    Returns:
        The closed membership record.
    """
    membership_record.removed_at = removed_at
    membership_record.removed_by = removed_by
    membership_record.updated_at = utc_now()
    await db_session.flush()
    await db_session.refresh(membership_record)
    return membership_record


async def get_membership_by_id(
    db_session: AsyncSession, fleet_vehicle_membership_id: UUID
) -> FleetVehicleMembershipModel | None:
    """Find a membership by ID, open or closed.

    Args:
        db_session: Current database session.
        fleet_vehicle_membership_id: Internal ID of the membership.

    Returns:
        The membership record, or None if not found.
    """
    query_result = await db_session.execute(
        select(FleetVehicleMembershipModel).where(
            FleetVehicleMembershipModel.fleet_vehicle_membership_id
            == fleet_vehicle_membership_id
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


def _assignment_conditions(
    *,
    fleet_id: UUID | None,
    membership_id: UUID | None,
    include_closed: bool,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by the assignment list and count.

    Args:
        fleet_id: Only assignments of this fleet, if given.
        membership_id: Only assignments of this membership, if given.
        include_closed: Also return assignments that were taken away.

    Returns:
        Conditions to AND together (possibly empty).
    """
    conditions: list[ColumnElement[bool]] = []
    if fleet_id is not None:
        conditions.append(FleetUserAssignmentModel.fleet_id == fleet_id)
    if membership_id is not None:
        conditions.append(FleetUserAssignmentModel.membership_id == membership_id)
    if not include_closed:
        conditions.append(FleetUserAssignmentModel.unassigned_at.is_(None))
    return conditions


async def insert_assignment(
    db_session: AsyncSession,
    *,
    fleet_id: UUID,
    membership_id: UUID,
    assigned_at: datetime,
    assigned_by: UUID | None,
) -> FleetUserAssignmentModel:
    """Open a fleet assignment for a membership and flush it.

    Args:
        db_session: Current database session; the repository does not commit.
        fleet_id: The fleet the membership is limited to.
        membership_id: The membership whose fleet-level roles are limited.
        assigned_at: When the fleet is given.
        assigned_by: User who gave it; `None` when the system did.

    Returns:
        The new open assignment.

    Side Effects:
        Flushes, which is where the partial unique index would raise
        `IntegrityError` for a fleet the membership already holds.
    """
    assignment_record = FleetUserAssignmentModel(
        fleet_id=fleet_id,
        membership_id=membership_id,
        assigned_at=assigned_at,
        assigned_by=assigned_by,
    )
    db_session.add(assignment_record)
    await db_session.flush()
    await db_session.refresh(assignment_record)
    return assignment_record


async def find_open_assignment(
    db_session: AsyncSession, fleet_id: UUID, membership_id: UUID
) -> FleetUserAssignmentModel | None:
    """Find the open assignment of a fleet to a membership, if any.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        membership_id: Internal ID of the membership.

    Returns:
        The open assignment, or None.
    """
    query_result = await db_session.execute(
        select(FleetUserAssignmentModel).where(
            FleetUserAssignmentModel.fleet_id == fleet_id,
            FleetUserAssignmentModel.membership_id == membership_id,
            FleetUserAssignmentModel.unassigned_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def list_open_assignments_by_membership(
    db_session: AsyncSession, membership_id: UUID
) -> list[FleetUserAssignmentModel]:
    """List every open assignment of a membership, with no pagination.

    Used to compute the visible fleet set, which must never be truncated.

    Args:
        db_session: Current database session.
        membership_id: Internal ID of the membership.

    Returns:
        Open assignments, oldest first.
    """
    query_result = await db_session.execute(
        select(FleetUserAssignmentModel)
        .where(
            FleetUserAssignmentModel.membership_id == membership_id,
            FleetUserAssignmentModel.unassigned_at.is_(None),
        )
        .order_by(FleetUserAssignmentModel.assigned_at.asc())
    )
    return list(query_result.scalars().all())


async def list_open_assignments_by_fleet(
    db_session: AsyncSession, fleet_id: UUID
) -> list[FleetUserAssignmentModel]:
    """List every open assignment pointing at a fleet, with no pagination.

    Used when a fleet is deleted, so no assignment is left behind.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        Open assignments of the fleet.
    """
    query_result = await db_session.execute(
        select(FleetUserAssignmentModel).where(
            FleetUserAssignmentModel.fleet_id == fleet_id,
            FleetUserAssignmentModel.unassigned_at.is_(None),
        )
    )
    return list(query_result.scalars().all())


async def list_assignments(
    db_session: AsyncSession,
    *,
    fleet_id: UUID | None = None,
    membership_id: UUID | None = None,
    include_closed: bool = False,
    offset: int,
    limit: int,
) -> list[FleetUserAssignmentModel]:
    """Get a page of fleet assignments, newest first.

    Args:
        db_session: Current database session.
        fleet_id: Only assignments of this fleet, if given.
        membership_id: Only assignments of this membership, if given.
        include_closed: Also return assignments that were taken away.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        Assignment records ordered by `assigned_at` descending.
    """
    conditions = _assignment_conditions(
        fleet_id=fleet_id, membership_id=membership_id, include_closed=include_closed
    )
    query_result = await db_session.execute(
        select(FleetUserAssignmentModel)
        .where(*conditions)
        .order_by(FleetUserAssignmentModel.assigned_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_assignments(
    db_session: AsyncSession,
    *,
    fleet_id: UUID | None = None,
    membership_id: UUID | None = None,
    include_closed: bool = False,
) -> int:
    """Count the assignments matching the same filters as `list_assignments`.

    Args:
        db_session: Current database session.
        fleet_id: Only assignments of this fleet, if given.
        membership_id: Only assignments of this membership, if given.
        include_closed: Also count assignments that were taken away.

    Returns:
        Number of matching assignments.
    """
    conditions = _assignment_conditions(
        fleet_id=fleet_id, membership_id=membership_id, include_closed=include_closed
    )
    query_result = await db_session.execute(
        select(func.count(FleetUserAssignmentModel.fleet_user_assignment_id)).where(
            *conditions
        )
    )
    return query_result.scalar() or 0


async def close_assignment(
    db_session: AsyncSession,
    assignment_record: FleetUserAssignmentModel,
    *,
    unassigned_at: datetime,
    unassigned_by: UUID | None,
) -> FleetUserAssignmentModel:
    """Close an open assignment.

    Args:
        db_session: Current database session; the repository does not commit.
        assignment_record: The open assignment to close.
        unassigned_at: When the fleet is taken away.
        unassigned_by: User who took it away; `None` when the system did
            (the fleet was deleted).

    Returns:
        The closed assignment.
    """
    assignment_record.unassigned_at = unassigned_at
    assignment_record.unassigned_by = unassigned_by
    await db_session.flush()
    await db_session.refresh(assignment_record)
    return assignment_record
