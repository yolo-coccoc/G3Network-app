"""Repository querying the drivers tables; contains no business rules."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from geoalchemy2.elements import WKBElement
from sqlalchemy import and_, column, func, or_, select, table
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.drivers.models import DriverModel, DrivingSessionModel, TripModel
from app.domains.drivers.types import (
    CheckInMethod,
    DriverStatus,
    DrivingSessionEndCause,
)
from app.libs.common.clock import utc_now
from app.libs.db.history import set_change_context

# A driver profile has no organization column (DM-24): its organization is its
# membership's. The scope filter reads that one column through a lightweight
# table definition instead of importing the identity models (another domain's
# internals); the foreign key `drivers.membership_id` guarantees the row.
_MEMBERSHIPS = table(
    "memberships",
    column("membership_id", PG_UUID(as_uuid=True)),
    column("organization_id", PG_UUID(as_uuid=True)),
    column("user_id", PG_UUID(as_uuid=True)),
)


def _in_organization(organization_id: UUID) -> ColumnElement[bool]:
    """Build the condition "the profile's membership is in this organization".

    Args:
        organization_id: The organization the caller may reach.

    Returns:
        A condition on `drivers.membership_id`.
    """
    return DriverModel.membership_id.in_(
        select(_MEMBERSHIPS.c.membership_id).where(
            _MEMBERSHIPS.c.organization_id == organization_id
        )
    )


# Reasons recorded in `driver_history` for a routine change (the person's
# typed `status_reason` replaces them when the change carries one).
DRIVER_EDITED_REASON = "Driver details edited"
DRIVER_DELETED_REASON = "Driver deleted"


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


async def get_by_id(
    db_session: AsyncSession,
    driver_id: UUID,
    *,
    organization_id: UUID | None = None,
    include_deleted: bool = False,
) -> DriverModel | None:
    """Find a driver by ID, excluding soft-deleted records unless asked.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        organization_id: Data scope (ACC-15): only a profile whose membership
            is in this organization is found; `None` means no restriction.
        include_deleted: Also find a soft-deleted profile (a trip keeps
            pointing to the driver who left).

    Returns:
        The driver record, or None if not found or out of scope.
    """
    conditions = [DriverModel.driver_id == driver_id]
    if not include_deleted:
        conditions.append(DriverModel.deleted_at.is_(None))
    if organization_id is not None:
        conditions.append(_in_organization(organization_id))
    query_result = await db_session.execute(
        select(DriverModel).where(and_(*conditions))
    )
    return query_result.scalar_one_or_none()


async def find_by_membership_id(
    db_session: AsyncSession, membership_id: UUID
) -> DriverModel | None:
    """Find the driver profile of a membership, soft-deleted ones included.

    The profile is unique per membership for good (a new membership gets a
    new profile), so a deleted profile still blocks a second one.

    Args:
        db_session: Current database session.
        membership_id: Internal ID of the membership.

    Returns:
        The driver record, or None if the membership has no profile.
    """
    query_result = await db_session.execute(
        select(DriverModel).where(DriverModel.membership_id == membership_id)
    )
    return query_result.scalar_one_or_none()


def _contains_pattern(search_text: str) -> str:
    """Build an ``ILIKE`` pattern matching a literal substring.

    ``%``, ``_`` and the escape character itself are escaped so the caller's
    text is never treated as a wildcard.

    Args:
        search_text: Raw substring typed by the caller.

    Returns:
        ``%<escaped text>%``, to use with ``escape="\\"``.
    """
    escaped_text = (
        search_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped_text}%"


def _driver_list_conditions(
    *,
    status_filter: DriverStatus | None,
    search_text: str | None,
    vehicle_id: UUID | None,
    organization_id: UUID | None,
    person_membership_ids: list[UUID] | None = None,
    license_expires_by: date | None = None,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by `list_all` and `count`.

    Args:
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the licence number, if any.
            The name and phone number live on the user (another domain): the
            service passes the memberships whose person matches the same text
            in ``person_membership_ids``, and a profile matches on either.
        vehicle_id: Only the driver at the wheel of this vehicle now (open
            driving session), if given.
        organization_id: Data scope; `None` means every organization.
        person_membership_ids: Memberships whose person matches the search
            text, if the service looked them up.
        license_expires_by: Only profiles whose licence expires on or before
            this date (already expired ones included), if given.

    Returns:
        Conditions to AND together; always excludes soft-deleted drivers.
    """
    conditions: list[ColumnElement[bool]] = [DriverModel.deleted_at.is_(None)]
    if organization_id is not None:
        conditions.append(_in_organization(organization_id))

    if status_filter:
        conditions.append(DriverModel.status == status_filter)
    if search_text:
        text_conditions = [
            DriverModel.license_number.ilike(
                _contains_pattern(search_text), escape="\\"
            )
        ]
        if person_membership_ids:
            text_conditions.append(DriverModel.membership_id.in_(person_membership_ids))
        conditions.append(or_(*text_conditions))
    if license_expires_by is not None:
        conditions.append(DriverModel.license_expires_on <= license_expires_by)
    if vehicle_id is not None:
        conditions.append(
            DriverModel.driver_id.in_(
                select(DrivingSessionModel.driver_id).where(
                    DrivingSessionModel.vehicle_id == vehicle_id,
                    DrivingSessionModel.ended_at.is_(None),
                )
            )
        )
    return conditions


async def list_all(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    status_filter: DriverStatus | None = None,
    search_text: str | None = None,
    vehicle_id: UUID | None = None,
    organization_id: UUID | None = None,
    person_membership_ids: list[UUID] | None = None,
    license_expires_by: date | None = None,
) -> list[DriverModel]:
    """Get a paginated list of drivers, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the licence number, if any.
        vehicle_id: Only the driver at the wheel of this vehicle now, if given.
        organization_id: Data scope; `None` means every organization.
        person_membership_ids: Memberships whose person matches the search
            text (looked up by the service), if any.
        license_expires_by: Licence expires on or before this date, if given.

    Returns:
        List of driver records, newest first.
    """
    conditions = _driver_list_conditions(
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
        person_membership_ids=person_membership_ids,
        license_expires_by=license_expires_by,
    )

    query_result = await db_session.execute(
        select(DriverModel)
        .where(and_(*conditions))
        .order_by(DriverModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession,
    *,
    status_filter: DriverStatus | None = None,
    search_text: str | None = None,
    vehicle_id: UUID | None = None,
    organization_id: UUID | None = None,
    person_membership_ids: list[UUID] | None = None,
    license_expires_by: date | None = None,
) -> int:
    """Count the drivers matching the same filters as `list_all`.

    Args:
        db_session: Current database session.
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the licence number, if any.
        vehicle_id: Only the driver at the wheel of this vehicle now, if given.
        organization_id: Data scope; `None` means every organization.
        person_membership_ids: Memberships whose person matches the search
            text (looked up by the service), if any.
        license_expires_by: Licence expires on or before this date, if given.

    Returns:
        Total number of matching drivers.
    """
    conditions = _driver_list_conditions(
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
        person_membership_ids=person_membership_ids,
        license_expires_by=license_expires_by,
    )

    query_result = await db_session.execute(
        select(func.count(DriverModel.driver_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession,
    driver_id: UUID,
    values: dict[str, Any],
    *,
    change_reason: str = DRIVER_EDITED_REASON,
    changed_by: UUID | None = None,
    organization_id: UUID | None = None,
) -> DriverModel | None:
    """Update the specified fields of a driver.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        values: Fields to update.
        change_reason: Reason recorded in the driver's change history.
        changed_by: The acting user, recorded in the history.
        organization_id: Data scope; `None` means no restriction.

    Returns:
        The updated driver record, or None if not found or out of scope.
    """
    driver_record = await get_by_id(
        db_session, driver_id, organization_id=organization_id
    )
    if not driver_record:
        return None

    # Drivers are change-tracked: the trigger needs the actor and the reason
    # in this transaction.
    await set_change_context(
        db_session, changed_by=changed_by, change_reason=change_reason
    )
    for field_name, value in values.items():
        if hasattr(driver_record, field_name):
            setattr(driver_record, field_name, value)

    driver_record.updated_at = utc_now()
    await db_session.flush()
    await db_session.refresh(driver_record)
    return driver_record


async def soft_delete(
    db_session: AsyncSession,
    driver_id: UUID,
    *,
    status_reason: str,
    changed_by: UUID | None = None,
    organization_id: UUID | None = None,
) -> DriverModel | None:
    """Soft-delete a driver: INACTIVE, `deleted_at` set and the reason stored (DM-25).

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        status_reason: Why the profile left the system.
        changed_by: The acting user, recorded in the history.
        organization_id: Data scope; `None` means no restriction.

    Returns:
        The driver record after soft delete, or None if not found or out of
        scope.
    """
    driver_record = await get_by_id(
        db_session, driver_id, organization_id=organization_id
    )
    if not driver_record:
        return None

    await set_change_context(
        db_session, changed_by=changed_by, change_reason=DRIVER_DELETED_REASON
    )
    driver_record.deleted_at = utc_now()
    driver_record.status = DriverStatus.INACTIVE
    driver_record.status_reason = status_reason
    await db_session.flush()
    await db_session.refresh(driver_record)
    return driver_record


async def find_open_session_by_vehicle(
    db_session: AsyncSession, vehicle_id: UUID
) -> DrivingSessionModel | None:
    """Find the open driving session of a truck, if any.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The open session, or None if nobody is checked in to the truck.
    """
    query_result = await db_session.execute(
        select(DrivingSessionModel).where(
            DrivingSessionModel.vehicle_id == vehicle_id,
            DrivingSessionModel.ended_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def find_open_session_by_driver(
    db_session: AsyncSession, driver_id: UUID
) -> DrivingSessionModel | None:
    """Find the open driving session of a driver, if any.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.

    Returns:
        The open session, or None if the driver is not checked in anywhere.
    """
    query_result = await db_session.execute(
        select(DrivingSessionModel).where(
            DrivingSessionModel.driver_id == driver_id,
            DrivingSessionModel.ended_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def list_sessions(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    driver_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    organization_id: UUID | None = None,
    vehicle_ids: frozenset[UUID] | None = None,
) -> list[DrivingSessionModel]:
    """Get a paginated list of driving sessions, newest first.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        driver_id: Only this driver's sessions, if given.
        vehicle_id: Only this truck's sessions, if given.
        organization_id: Data scope; `None` means every organization.
        vehicle_ids: Fleet limit of the caller (FL-10): only sessions on
            these trucks; `None` means no limit.

    Returns:
        Session records ordered by `started_at` descending.
    """
    query_result = await db_session.execute(
        select(DrivingSessionModel)
        .where(
            *_session_conditions(driver_id, vehicle_id, organization_id, vehicle_ids)
        )
        .order_by(DrivingSessionModel.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_sessions(
    db_session: AsyncSession,
    *,
    driver_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    organization_id: UUID | None = None,
    vehicle_ids: frozenset[UUID] | None = None,
) -> int:
    """Count the driving sessions matching the same filters as `list_sessions`.

    Args:
        db_session: Current database session.
        driver_id: Only this driver's sessions, if given.
        vehicle_id: Only this truck's sessions, if given.
        organization_id: Data scope; `None` means every organization.
        vehicle_ids: Fleet limit of the caller (FL-10), if any.

    Returns:
        Total number of matching sessions, open and closed.
    """
    query_result = await db_session.execute(
        select(func.count(DrivingSessionModel.driving_session_id)).where(
            *_session_conditions(driver_id, vehicle_id, organization_id, vehicle_ids)
        )
    )
    return query_result.scalar() or 0


def _session_conditions(
    driver_id: UUID | None,
    vehicle_id: UUID | None,
    organization_id: UUID | None,
    vehicle_ids: frozenset[UUID] | None = None,
) -> list[ColumnElement[bool]]:
    """Build the optional driver/truck conditions of a session query.

    Args:
        driver_id: Only this driver's sessions, if given.
        vehicle_id: Only this truck's sessions, if given.
        organization_id: Data scope: only sessions recorded for this owner
            organization (the truck's owner at check-in, DM-24 C); `None`
            means every organization.
        vehicle_ids: Only sessions on these trucks (the caller's fleet limit,
            FL-10); `None` means no limit, an empty set matches nothing.

    Returns:
        Conditions to AND together (empty when no filter is given).
    """
    conditions: list[ColumnElement[bool]] = []
    if vehicle_ids is not None:
        conditions.append(DrivingSessionModel.vehicle_id.in_(vehicle_ids))
    if organization_id is not None:
        conditions.append(DrivingSessionModel.organization_id == organization_id)
    if driver_id is not None:
        conditions.append(DrivingSessionModel.driver_id == driver_id)
    if vehicle_id is not None:
        conditions.append(DrivingSessionModel.vehicle_id == vehicle_id)
    return conditions


async def insert_session(
    db_session: AsyncSession,
    *,
    organization_id: UUID,
    driver_id: UUID,
    vehicle_id: UUID,
    check_in_method: CheckInMethod,
    check_in_location: WKBElement | None,
    started_at: datetime,
) -> DrivingSessionModel:
    """Open a driving session and flush it.

    Args:
        db_session: Current database session; the repository does not commit.
        organization_id: The truck's owner now, written once (DM-24 case C).
        driver_id: Driver profile checking in.
        vehicle_id: Truck being driven.
        check_in_method: How the driver checked in.
        check_in_location: Phone position at check-in, if known.
        started_at: Check-in time.

    Returns:
        The newly created session.

    Side Effects:
        `flush()` is where the two partial unique indexes raise
        `IntegrityError` when the truck or the driver already has an open
        session.
    """
    session_record = DrivingSessionModel(
        organization_id=organization_id,
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        check_in_method=check_in_method.value,
        check_in_location=check_in_location,
        started_at=started_at,
    )
    db_session.add(session_record)
    await db_session.flush()
    await db_session.refresh(session_record)
    return session_record


async def close_session(
    db_session: AsyncSession,
    session_record: DrivingSessionModel,
    *,
    ended_at: datetime,
    end_cause: DrivingSessionEndCause,
) -> DrivingSessionModel:
    """Close an open driving session.

    Args:
        db_session: Current database session; the repository does not commit.
        session_record: The open session to close.
        ended_at: When the session ends.
        end_cause: Why it ends.

    Returns:
        The closed session.
    """
    session_record.ended_at = ended_at
    session_record.end_cause = end_cause.value
    session_record.updated_at = utc_now()
    await db_session.flush()
    await db_session.refresh(session_record)
    return session_record


async def exists_license_on_other_person(
    db_session: AsyncSession, license_number: str, user_id: UUID
) -> bool:
    """Tell whether a licence number is on the live profile of another person (DR-09).

    The answer is a yes/no only, so one organization learns nothing about
    another's driver.

    Args:
        db_session: Current database session.
        license_number: The licence number to look for (exact match).
        user_id: The person the number is being recorded for; their own
            profiles (in any organization) do not count.

    Returns:
        True when a not-deleted profile of a different person has the number.
    """
    query_result = await db_session.execute(
        select(func.count(DriverModel.driver_id))
        .join(
            _MEMBERSHIPS,
            _MEMBERSHIPS.c.membership_id == DriverModel.membership_id,
        )
        .where(
            DriverModel.license_number == license_number,
            DriverModel.deleted_at.is_(None),
            _MEMBERSHIPS.c.user_id != user_id,
        )
    )
    return (query_result.scalar() or 0) > 0


async def list_open_sessions(db_session: AsyncSession) -> list[DrivingSessionModel]:
    """List every open driving session, oldest first (the auto-end sweep).

    Args:
        db_session: Current database session.

    Returns:
        Sessions whose ``ended_at`` is NULL.
    """
    query_result = await db_session.execute(
        select(DrivingSessionModel)
        .where(DrivingSessionModel.ended_at.is_(None))
        .order_by(DrivingSessionModel.started_at.asc())
    )
    return list(query_result.scalars().all())


async def list_sessions_in_range(
    db_session: AsyncSession,
    *,
    driver_id: UUID,
    start_time: datetime,
    end_time: datetime,
    limit: int,
) -> list[DrivingSessionModel]:
    """List one driver's sessions that started inside a range, newest first.

    Args:
        db_session: Current database session.
        driver_id: The driver profile.
        start_time: Inclusive lower bound of ``started_at``.
        end_time: Exclusive upper bound of ``started_at``.
        limit: Largest number of sessions returned.

    Returns:
        Sessions, open ones included, ordered by ``started_at`` descending.
    """
    query_result = await db_session.execute(
        select(DrivingSessionModel)
        .where(
            DrivingSessionModel.driver_id == driver_id,
            DrivingSessionModel.started_at >= start_time,
            DrivingSessionModel.started_at < end_time,
        )
        .order_by(DrivingSessionModel.started_at.desc())
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def get_session_by_id(
    db_session: AsyncSession, driving_session_id: UUID
) -> DrivingSessionModel | None:
    """Find a driving session by ID.

    Args:
        db_session: Current database session.
        driving_session_id: Internal ID of the session.

    Returns:
        The session, or None if it does not exist.
    """
    query_result = await db_session.execute(
        select(DrivingSessionModel).where(
            DrivingSessionModel.driving_session_id == driving_session_id
        )
    )
    return query_result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Trips (DR-12)
# ---------------------------------------------------------------------------

TRIP_PLANNED_REASON = "Trip planned"
TRIP_EDITED_REASON = "Trip plan edited"
TRIP_STARTED_REASON = "Trip started"
TRIP_FINISHED_REASON = "Trip finished"
TRIP_CANCELLED_REASON = "Trip cancelled"


async def insert_trip(db_session: AsyncSession, values: dict[str, Any]) -> TripModel:
    """Insert a trip and flush it.

    Args:
        db_session: Current database session; the repository does not commit.
        values: Fields used to initialize the ORM record.

    Returns:
        The newly created trip.

    Side Effects:
        `flush()` is where the partial unique index of a trip in progress
        raises `IntegrityError` for a second running trip of one session.
    """
    trip_record = TripModel(**values)
    db_session.add(trip_record)
    await db_session.flush()
    await db_session.refresh(trip_record)
    return trip_record


def _trip_driver_condition(driver_id: UUID) -> ColumnElement[bool]:
    """Build the condition "this driver's trip": assigned to them or run by them.

    Args:
        driver_id: The driver profile.

    Returns:
        A condition on `trips`: the plan names the driver, or the trip was
        started in one of the driver's driving sessions.
    """
    return or_(
        TripModel.planned_driver_id == driver_id,
        TripModel.driving_session_id.in_(
            select(DrivingSessionModel.driving_session_id).where(
                DrivingSessionModel.driver_id == driver_id
            )
        ),
    )


async def get_trip_by_id(
    db_session: AsyncSession,
    trip_id: UUID,
    *,
    organization_id: UUID | None = None,
    driver_id: UUID | None = None,
) -> TripModel | None:
    """Find a trip by ID inside a scope.

    Args:
        db_session: Current database session.
        trip_id: Internal ID of the trip.
        organization_id: Only a trip of this organization; `None` means any.
        driver_id: Only a trip assigned to or run by this driver; `None` means
            any.

    Returns:
        The trip, or None if it does not exist or is out of scope.
    """
    conditions: list[ColumnElement[bool]] = [TripModel.trip_id == trip_id]
    if organization_id is not None:
        conditions.append(TripModel.organization_id == organization_id)
    if driver_id is not None:
        conditions.append(_trip_driver_condition(driver_id))
    query_result = await db_session.execute(select(TripModel).where(and_(*conditions)))
    return query_result.scalar_one_or_none()


def _trip_list_conditions(
    *,
    organization_id: UUID | None,
    driver_id: UUID | None,
    statuses: list[str] | None,
    from_time: datetime | None,
    to_time: datetime | None,
    vehicle_ids: frozenset[UUID] | None = None,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by `list_trips` and `count_trips`.

    Args:
        organization_id: Only trips of this organization; `None` means any.
        driver_id: Only trips assigned to or run by this driver, if given.
        statuses: Only these statuses, if given.
        from_time: Only trips planned or started at or after this time.
        to_time: Only trips planned or started before this time.
        vehicle_ids: Only trips on these trucks (the caller's fleet limit,
            FL-10): planned for one of them, or run in a driving session on
            one of them. `None` means no limit.

    Returns:
        Conditions to AND together (empty when no filter is given).
    """
    conditions: list[ColumnElement[bool]] = []
    if vehicle_ids is not None:
        conditions.append(
            or_(
                TripModel.planned_vehicle_id.in_(vehicle_ids),
                TripModel.driving_session_id.in_(
                    select(DrivingSessionModel.driving_session_id).where(
                        DrivingSessionModel.vehicle_id.in_(vehicle_ids)
                    )
                ),
            )
        )
    # A trip is placed on the board by its plan, or by its start when it has
    # no plan (a personal trip).
    trip_time = func.coalesce(TripModel.planned_start_at, TripModel.started_at)
    if organization_id is not None:
        conditions.append(TripModel.organization_id == organization_id)
    if driver_id is not None:
        conditions.append(_trip_driver_condition(driver_id))
    if statuses:
        conditions.append(TripModel.status.in_(statuses))
    if from_time is not None:
        conditions.append(trip_time >= from_time)
    if to_time is not None:
        conditions.append(trip_time < to_time)
    return conditions


async def list_trips(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    organization_id: UUID | None = None,
    driver_id: UUID | None = None,
    statuses: list[str] | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    vehicle_ids: frozenset[UUID] | None = None,
) -> list[TripModel]:
    """Get a paginated list of trips, newest plan first.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        organization_id: Only trips of this organization; `None` means any.
        driver_id: Only trips assigned to or run by this driver, if given.
        statuses: Only these statuses, if given.
        from_time: Lower bound of the plan (or start) time, if given.
        to_time: Exclusive upper bound of the plan (or start) time, if given.
        vehicle_ids: Fleet limit of the caller (FL-10), if any.

    Returns:
        Trips ordered by plan (or start) time descending.
    """
    trip_time = func.coalesce(TripModel.planned_start_at, TripModel.started_at)
    query_result = await db_session.execute(
        select(TripModel)
        .where(
            *_trip_list_conditions(
                organization_id=organization_id,
                driver_id=driver_id,
                statuses=statuses,
                from_time=from_time,
                to_time=to_time,
                vehicle_ids=vehicle_ids,
            )
        )
        .order_by(trip_time.desc(), TripModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_trips(
    db_session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    driver_id: UUID | None = None,
    statuses: list[str] | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    vehicle_ids: frozenset[UUID] | None = None,
) -> int:
    """Count the trips matching the same filters as `list_trips`.

    Args:
        db_session: Current database session.
        organization_id: Only trips of this organization; `None` means any.
        driver_id: Only trips assigned to or run by this driver, if given.
        statuses: Only these statuses, if given.
        from_time: Lower bound of the plan (or start) time, if given.
        to_time: Exclusive upper bound of the plan (or start) time, if given.
        vehicle_ids: Fleet limit of the caller (FL-10), if any.

    Returns:
        Total number of matching trips.
    """
    query_result = await db_session.execute(
        select(func.count(TripModel.trip_id)).where(
            *_trip_list_conditions(
                organization_id=organization_id,
                driver_id=driver_id,
                statuses=statuses,
                from_time=from_time,
                to_time=to_time,
                vehicle_ids=vehicle_ids,
            )
        )
    )
    return query_result.scalar() or 0


async def find_in_progress_trip_by_session(
    db_session: AsyncSession, driving_session_id: UUID
) -> TripModel | None:
    """Find the trip running in a driving session, if any.

    Args:
        db_session: Current database session.
        driving_session_id: Internal ID of the session.

    Returns:
        The trip whose status is IN_PROGRESS, or None.
    """
    query_result = await db_session.execute(
        select(TripModel).where(
            TripModel.driving_session_id == driving_session_id,
            TripModel.status == "IN_PROGRESS",
        )
    )
    return query_result.scalar_one_or_none()


async def update_trip_fields(
    db_session: AsyncSession,
    trip_record: TripModel,
    values: dict[str, Any],
    *,
    change_reason: str,
    changed_by: UUID | None,
) -> TripModel:
    """Apply new values to a trip, recording who changed it and why.

    Args:
        db_session: Current database session; the repository does not commit.
        trip_record: The trip to change.
        values: Column names and their new values.
        change_reason: Reason recorded in the trip's change history.
        changed_by: The acting user, or None for the system (the auto-close).

    Returns:
        The refreshed trip.

    Side Effects:
        Sets the change context so the history trigger records the actor and
        the reason; `flush()` may raise `IntegrityError` (one running trip per
        session).
    """
    await set_change_context(
        db_session, changed_by=changed_by, change_reason=change_reason
    )
    for field_name, value in values.items():
        setattr(trip_record, field_name, value)
    trip_record.updated_at = utc_now()
    await db_session.flush()
    await db_session.refresh(trip_record)
    return trip_record
