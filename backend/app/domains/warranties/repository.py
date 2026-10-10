"""Repository querying the warranties table; no business rules.

Only the warranties service calls this module; other domains go through
``warranties/service.py``. Lookups exclude soft-deleted rows. Functions flush
when they need a generated value or a constraint error, and never commit or
roll back - the entry boundary owns the transaction.

Data scope (DM-24): a warranty has no ``organization_id``; it follows its
object, so "the caller's warranties" are those whose truck, battery, T-Box or
charger the caller's organization owns. The filter is one SQL predicate built
from lightweight table definitions of those owners' columns (the same
precedent as ``drivers.repository``): importing the other domains' models is
forbidden, and a join in SQL keeps filtering and pagination in one query.
"""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import and_, column, func, or_, select, table
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.warranties.models import WarrantyModel
from app.libs.db.history import set_change_context

# Only the columns the ownership predicate reads. The tables belong to the
# vehicles, batteries, telematics and charging_stations domains.
_VEHICLES = table("vehicles", column("vehicle_id"), column("organization_id"))
_BATTERIES = table("batteries", column("battery_id"), column("organization_id"))
_TELEMATICS = table("telematics", column("telematic_id"), column("organization_id"))
_STATIONS = table("charging_stations", column("station_id"), column("location_id"))
_LOCATIONS = table(
    "charging_locations", column("location_id"), column("organization_id")
)


def _owned_by(organization_id: UUID) -> ColumnElement[bool]:
    """Build the predicate "the covered object belongs to this organization".

    Args:
        organization_id: The owning organization.

    Returns:
        A condition on the warranty's four links: the truck, battery or T-Box
        is owned by the organization, or the charger's location is.
    """
    return or_(
        WarrantyModel.vehicle_id.in_(
            select(_VEHICLES.c.vehicle_id).where(
                _VEHICLES.c.organization_id == organization_id
            )
        ),
        WarrantyModel.battery_id.in_(
            select(_BATTERIES.c.battery_id).where(
                _BATTERIES.c.organization_id == organization_id
            )
        ),
        WarrantyModel.telematic_id.in_(
            select(_TELEMATICS.c.telematic_id).where(
                _TELEMATICS.c.organization_id == organization_id
            )
        ),
        WarrantyModel.station_id.in_(
            select(_STATIONS.c.station_id)
            .join(_LOCATIONS, _LOCATIONS.c.location_id == _STATIONS.c.location_id)
            .where(_LOCATIONS.c.organization_id == organization_id)
        ),
    )


async def insert(db_session: AsyncSession, values: dict[str, Any]) -> WarrantyModel:
    """Insert a warranty record.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Fields used to initialize the ORM record.

    Returns:
        The new record, reloaded after the flush.

    Raises:
        IntegrityError: When a foreign key or the one-link check fails.
    """
    warranty_record = WarrantyModel(**values)
    db_session.add(warranty_record)
    await db_session.flush()
    await db_session.refresh(warranty_record)
    return warranty_record


async def get_by_id(
    db_session: AsyncSession,
    warranty_id: UUID,
    *,
    organization_id: UUID | None = None,
) -> WarrantyModel | None:
    """Find a live warranty by ID.

    Args:
        db_session: Current database session.
        warranty_id: Internal ID of the warranty.
        organization_id: Data scope: only a warranty whose covered object this
            organization owns is found; `None` means no restriction.

    Returns:
        The record, or None if not found, entered by mistake or out of scope.
    """
    conditions = [
        WarrantyModel.warranty_id == warranty_id,
        WarrantyModel.deleted_at.is_(None),
    ]
    if organization_id is not None:
        conditions.append(_owned_by(organization_id))
    query_result = await db_session.execute(
        select(WarrantyModel).where(and_(*conditions))
    )
    return query_result.scalar_one_or_none()


async def find_overlapping(
    db_session: AsyncSession,
    object_links: dict[str, UUID],
    warranty_type: str,
    starts_on: date,
    ends_on: date,
    *,
    excluding_warranty_id: UUID | None = None,
) -> WarrantyModel | None:
    """Find a live, not voided warranty of the same object and type that overlaps.

    Args:
        db_session: Current database session.
        object_links: The covered object as ``{link column: id}`` (one entry).
        warranty_type: ``STANDARD`` / ``EXTENDED``.
        starts_on: First day of the period to test.
        ends_on: Last day of the period to test.
        excluding_warranty_id: A warranty to ignore (the one being edited).

    Returns:
        One overlapping warranty, or None.
    """
    conditions: list[ColumnElement[bool]] = [
        WarrantyModel.deleted_at.is_(None),
        WarrantyModel.status == "ACTIVE",
        WarrantyModel.warranty_type == warranty_type,
        WarrantyModel.starts_on <= ends_on,
        WarrantyModel.ends_on >= starts_on,
    ]
    for link_name, link_id in object_links.items():
        conditions.append(getattr(WarrantyModel, link_name) == link_id)
    if excluding_warranty_id is not None:
        conditions.append(WarrantyModel.warranty_id != excluding_warranty_id)
    query_result = await db_session.execute(
        select(WarrantyModel).where(and_(*conditions)).limit(1)
    )
    return query_result.scalar_one_or_none()


def _build_list_conditions(
    organization_id: UUID | None,
    *,
    vehicle_id: UUID | None,
    battery_id: UUID | None,
    telematic_id: UUID | None,
    station_id: UUID | None,
    status: str | None,
    warranty_type: str | None,
    ending_between: tuple[date, date] | None,
    owner_organization_id: UUID | None,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by ``list_all`` and ``count``.

    Args:
        organization_id: Data scope; `None` means every organization.
        vehicle_id: Only warranties of this truck.
        battery_id: Only warranties of this battery.
        telematic_id: Only warranties of this T-Box.
        station_id: Only warranties of this charger.
        status: Exact status value.
        warranty_type: Exact warranty type.
        ending_between: ``(from, to)`` inclusive range of ``ends_on``.
        owner_organization_id: Only warranties whose object this organization
            owns (a filter the caller chose, on top of the data scope).

    Returns:
        Conditions to AND together: always "not entered by mistake".
    """
    conditions: list[ColumnElement[bool]] = [WarrantyModel.deleted_at.is_(None)]
    if organization_id is not None:
        conditions.append(_owned_by(organization_id))
    if owner_organization_id is not None:
        conditions.append(_owned_by(owner_organization_id))
    if vehicle_id is not None:
        conditions.append(WarrantyModel.vehicle_id == vehicle_id)
    if battery_id is not None:
        conditions.append(WarrantyModel.battery_id == battery_id)
    if telematic_id is not None:
        conditions.append(WarrantyModel.telematic_id == telematic_id)
    if station_id is not None:
        conditions.append(WarrantyModel.station_id == station_id)
    if status:
        conditions.append(WarrantyModel.status == status)
    if warranty_type:
        conditions.append(WarrantyModel.warranty_type == warranty_type)
    if ending_between is not None:
        conditions.append(WarrantyModel.ends_on >= ending_between[0])
        conditions.append(WarrantyModel.ends_on <= ending_between[1])
    return conditions


async def list_all(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    organization_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    battery_id: UUID | None = None,
    telematic_id: UUID | None = None,
    station_id: UUID | None = None,
    status: str | None = None,
    warranty_type: str | None = None,
    ending_between: tuple[date, date] | None = None,
    owner_organization_id: UUID | None = None,
) -> list[WarrantyModel]:
    """Get a page of live warranties, the ones ending soonest first.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        organization_id: Data scope; `None` means every organization.
        vehicle_id: Only warranties of this truck, if given.
        battery_id: Only warranties of this battery, if given.
        telematic_id: Only warranties of this T-Box, if given.
        station_id: Only warranties of this charger, if given.
        status: Exact status value, if any.
        warranty_type: Exact warranty type, if any.
        ending_between: Inclusive range of ``ends_on``, if any.
        owner_organization_id: Only warranties whose object this organization
            owns, if given.

    Returns:
        List of warranty records.
    """
    conditions = _build_list_conditions(
        organization_id,
        vehicle_id=vehicle_id,
        battery_id=battery_id,
        telematic_id=telematic_id,
        station_id=station_id,
        status=status,
        warranty_type=warranty_type,
        ending_between=ending_between,
        owner_organization_id=owner_organization_id,
    )
    query_result = await db_session.execute(
        select(WarrantyModel)
        .where(and_(*conditions))
        .order_by(WarrantyModel.ends_on, WarrantyModel.created_at)
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    battery_id: UUID | None = None,
    telematic_id: UUID | None = None,
    station_id: UUID | None = None,
    status: str | None = None,
    warranty_type: str | None = None,
    ending_between: tuple[date, date] | None = None,
    owner_organization_id: UUID | None = None,
) -> int:
    """Count the live warranties matching the filters.

    Args:
        db_session: Current database session.
        organization_id: Data scope; `None` means every organization.
        vehicle_id: Only warranties of this truck, if given.
        battery_id: Only warranties of this battery, if given.
        telematic_id: Only warranties of this T-Box, if given.
        station_id: Only warranties of this charger, if given.
        status: Exact status value, if any.
        warranty_type: Exact warranty type, if any.
        ending_between: Inclusive range of ``ends_on``, if any.
        owner_organization_id: Only warranties whose object this organization
            owns, if given.

    Returns:
        Number of matching warranties.
    """
    conditions = _build_list_conditions(
        organization_id,
        vehicle_id=vehicle_id,
        battery_id=battery_id,
        telematic_id=telematic_id,
        station_id=station_id,
        status=status,
        warranty_type=warranty_type,
        ending_between=ending_between,
        owner_organization_id=owner_organization_id,
    )
    query_result = await db_session.execute(
        select(func.count(WarrantyModel.warranty_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession,
    warranty_id: UUID,
    values: dict[str, Any],
    *,
    change_reason: str,
    changed_by: UUID | None = None,
    organization_id: UUID | None = None,
) -> WarrantyModel | None:
    """Update fields of a live warranty (also used to void and to delete it).

    Args:
        db_session: Current database session.
        warranty_id: Internal ID of the warranty.
        values: Fields to update, keyed by ORM attribute name.
        change_reason: Why the row changes (``warranty_history``).
        changed_by: The acting user.
        organization_id: Data scope; `None` means no restriction.

    Returns:
        The updated record, or None if not found or out of scope.
    """
    warranty_record = await get_by_id(
        db_session, warranty_id, organization_id=organization_id
    )
    if warranty_record is None:
        return None
    await set_change_context(
        db_session, changed_by=changed_by, change_reason=change_reason
    )
    for field_name, value in values.items():
        if hasattr(warranty_record, field_name):
            setattr(warranty_record, field_name, value)
    await db_session.flush()
    await db_session.refresh(warranty_record)
    return warranty_record
