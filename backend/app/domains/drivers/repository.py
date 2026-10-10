"""Repository querying the drivers tables; contains no business rules."""

from datetime import datetime
from typing import Any
from uuid import UUID

from geoalchemy2.elements import WKBElement
from sqlalchemy import and_, column, func, select, table
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.drivers.models import DriverModel, DrivingSessionModel
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
) -> DriverModel | None:
    """Find a driver by ID, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        organization_id: Data scope (ACC-15): only a profile whose membership
            is in this organization is found; `None` means no restriction.

    Returns:
        The driver record, or None if not found or out of scope.
    """
    conditions = [DriverModel.driver_id == driver_id, DriverModel.deleted_at.is_(None)]
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
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by `list_all` and `count`.

    Args:
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the licence number, if any.
            The name and phone number live on the user (another domain).
        vehicle_id: Only the driver at the wheel of this vehicle now (open
            driving session), if given.
        organization_id: Data scope; `None` means every organization.

    Returns:
        Conditions to AND together; always excludes soft-deleted drivers.
    """
    conditions: list[ColumnElement[bool]] = [DriverModel.deleted_at.is_(None)]
    if organization_id is not None:
        conditions.append(_in_organization(organization_id))

    if status_filter:
        conditions.append(DriverModel.status == status_filter)
    if search_text:
        conditions.append(
            DriverModel.license_number.ilike(
                _contains_pattern(search_text), escape="\\"
            )
        )
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

    Returns:
        List of driver records, newest first.
    """
    conditions = _driver_list_conditions(
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
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
) -> int:
    """Count the drivers matching the same filters as `list_all`.

    Args:
        db_session: Current database session.
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the licence number, if any.
        vehicle_id: Only the driver at the wheel of this vehicle now, if given.
        organization_id: Data scope; `None` means every organization.

    Returns:
        Total number of matching drivers.
    """
    conditions = _driver_list_conditions(
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
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
) -> list[DrivingSessionModel]:
    """Get a paginated list of driving sessions, newest first.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        driver_id: Only this driver's sessions, if given.
        vehicle_id: Only this truck's sessions, if given.
        organization_id: Data scope; `None` means every organization.

    Returns:
        Session records ordered by `started_at` descending.
    """
    query_result = await db_session.execute(
        select(DrivingSessionModel)
        .where(*_session_conditions(driver_id, vehicle_id, organization_id))
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
) -> int:
    """Count the driving sessions matching the same filters as `list_sessions`.

    Args:
        db_session: Current database session.
        driver_id: Only this driver's sessions, if given.
        vehicle_id: Only this truck's sessions, if given.
        organization_id: Data scope; `None` means every organization.

    Returns:
        Total number of matching sessions, open and closed.
    """
    query_result = await db_session.execute(
        select(func.count(DrivingSessionModel.driving_session_id)).where(
            *_session_conditions(driver_id, vehicle_id, organization_id)
        )
    )
    return query_result.scalar() or 0


def _session_conditions(
    driver_id: UUID | None, vehicle_id: UUID | None, organization_id: UUID | None
) -> list[ColumnElement[bool]]:
    """Build the optional driver/truck conditions of a session query.

    Args:
        driver_id: Only this driver's sessions, if given.
        vehicle_id: Only this truck's sessions, if given.
        organization_id: Data scope: only sessions recorded for this owner
            organization (the truck's owner at check-in, DM-24 C); `None`
            means every organization.

    Returns:
        Conditions to AND together (empty when no filter is given).
    """
    conditions: list[ColumnElement[bool]] = []
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
