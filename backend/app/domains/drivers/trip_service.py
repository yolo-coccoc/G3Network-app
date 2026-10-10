"""Trips: planned by a manager, run by the driver with Start and Finish (MON-11, DR-12).

A trip is a job inside a driving session. A manager may plan it (A to B as
place names, planned times, a driver and a truck, all optional but the places
and the departure); the driver, checked in to a truck, presses Start and then
Finish in the app, and the system records the time and the truck's T-Box
position, odometer and battery % at both ends. A driver with no plan starts a
personal trip, which exists only from Start. Distance, energy, kWh/km and cost
are differences of the stored readings, computed when read.

Status flow: PLANNED -> IN_PROGRESS -> COMPLETED, or PLANNED -> CANCELLED. One
trip runs at a time in a session (partial unique index); a trip still running
when its session ends is closed automatically by ``service._end_session``
(DR-12). A driver or truck other than the planned one is allowed and flagged in
the response. Every change of a trip writes ``trip_history`` with the actor and
a reason.

A manager who plans a trip for a driver, or moves a planned trip to another
driver, notifies that driver (`TRIP_ASSIGNED`, NT-15). Not built: the
`NO_TRIP_STARTED` reminder.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.repository as driver_repository
import app.domains.drivers.service as driver_service
import app.domains.identity.service as identity_service
import app.domains.notifications.service as notification_service
import app.domains.vehicles.service as vehicle_service
from app.domains.drivers.exceptions import (
    DriverNotFoundError,
    TripInProgressConflictError,
    TripInvalidPlanError,
    TripNotCheckedInError,
    TripNotFoundError,
    TripStateConflictError,
    TripVehicleNotFoundError,
)
from app.domains.drivers.models import DriverModel, DrivingSessionModel, TripModel
from app.domains.drivers.schemas import (
    PersonalTripStartRequest,
    TripListResponse,
    TripPlanRequest,
    TripResponse,
    TripStartRequest,
    TripUpdateRequest,
)
from app.domains.drivers.types import DeclaredLoadStatus, TripStatus
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import Principal, UserRole, roles_for
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.telemetry.types import VehicleLiveStatusReference
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location, location_to_coordinates
from app.libs.common.pagination import normalize_page_window

# Who plans, edits and cancels trips; a DRIVER-only caller runs their own.
TRIP_MANAGER_ROLES = roles_for("MON-11") - {UserRole.DRIVER}


def _require_manager(principal: Principal) -> None:
    """Refuse a caller who may not plan trips.

    Args:
        principal: The caller.

    Raises:
        AccessDeniedError: The caller holds no manager role of MON-11.
    """
    if not principal.has_any_role(*TRIP_MANAGER_ROLES):
        raise AccessDeniedError("Only a fleet manager can plan trips")


async def _find_own_driver(
    db_session: AsyncSession, principal: Principal
) -> DriverModel | None:
    """Find the caller's own live driver profile.

    Args:
        db_session: Current database session.
        principal: The caller.

    Returns:
        The profile, or `None` when the caller has none (or it was deleted).
    """
    driver_record = await driver_repository.find_by_membership_id(
        db_session, principal.membership_id
    )
    if driver_record is None or driver_record.deleted_at is not None:
        return None
    return driver_record


async def _require_own_driver(
    db_session: AsyncSession, principal: Principal
) -> DriverModel:
    """Find the caller's own live driver profile or fail.

    Args:
        db_session: Current database session.
        principal: The caller.

    Returns:
        The profile.

    Raises:
        DriverNotFoundError: The caller has no live driver profile.
    """
    driver_record = await _find_own_driver(db_session, principal)
    if driver_record is None:
        raise DriverNotFoundError("The caller has no driver profile")
    return driver_record


def _reading_values(
    prefix: str, reading: VehicleLiveStatusReference | None
) -> dict[str, object]:
    """Turn a T-Box reading into the trip columns of one end (DR-12).

    Args:
        prefix: ``start`` or ``end``.
        reading: The truck's recent reading, or `None` when it has none.

    Returns:
        Column values for ``<prefix>_location``, ``<prefix>_odometer_km`` and
        ``<prefix>_soc_percent``; empty when there is no reading, and the
        odometer left out when the device did not send one.
    """
    if reading is None:
        return {}
    values: dict[str, object] = {
        f"{prefix}_location": coordinates_to_location(
            reading.latitude, reading.longitude
        )
    }
    if reading.odometer_km is not None:
        values[f"{prefix}_odometer_km"] = Decimal(str(round(reading.odometer_km, 1)))
    if reading.soc_percent is not None:
        values[f"{prefix}_soc_percent"] = Decimal(str(round(reading.soc_percent, 2)))
    return values


def _float_or_none(value: Decimal | None) -> float | None:
    """Convert a stored decimal to a float for the response."""
    return float(value) if value is not None else None


async def build_trip_response(
    db_session: AsyncSession, trip_record: TripModel
) -> TripResponse:
    """Build a trip response with the people, trucks and computed figures.

    Args:
        db_session: Current database session.
        trip_record: The trip.

    Returns:
        The response: names and VINs of the planned and actual driver and
        truck, flags when they differ from the plan, and the distance,
        energy, kWh/km and cost computed from the stored readings.

    Side Effects:
        Per-row reads in the drivers, identity and vehicles domains (no
        batching, per the repo's rule). Read-only.
    """
    planned_driver_name: str | None = None
    if trip_record.planned_driver_id is not None:
        planned_driver = await driver_repository.get_by_id(
            db_session, trip_record.planned_driver_id, include_deleted=True
        )
        if planned_driver is not None:
            person = await identity_service.resolve_membership_person_reference(
                db_session, planned_driver.membership_id
            )
            planned_driver_name = person.full_name if person else None
    planned_vehicle_vin: str | None = None
    if trip_record.planned_vehicle_id is not None:
        planned_vehicle = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, trip_record.planned_vehicle_id
        )
        planned_vehicle_vin = planned_vehicle.vin if planned_vehicle else None

    session_record: DrivingSessionModel | None = None
    actual_vehicle_vin: str | None = None
    battery_capacity_kwh: float | None = None
    if trip_record.driving_session_id is not None:
        session_record = await driver_repository.get_session_by_id(
            db_session, trip_record.driving_session_id
        )
    if session_record is not None:
        actual_vehicle = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, session_record.vehicle_id
        )
        if actual_vehicle is not None:
            actual_vehicle_vin = actual_vehicle.vin
            battery_capacity_kwh = actual_vehicle.battery_capacity_kwh

    distance_km: float | None = None
    if (
        trip_record.start_odometer_km is not None
        and trip_record.end_odometer_km is not None
        and trip_record.end_odometer_km >= trip_record.start_odometer_km
    ):
        distance_km = round(
            float(trip_record.end_odometer_km - trip_record.start_odometer_km), 1
        )
    energy_kwh: float | None = None
    if (
        trip_record.start_soc_percent is not None
        and trip_record.end_soc_percent is not None
        and trip_record.start_soc_percent >= trip_record.end_soc_percent
        and battery_capacity_kwh is not None
    ):
        soc_drop = float(trip_record.start_soc_percent - trip_record.end_soc_percent)
        energy_kwh = round(soc_drop / 100 * battery_capacity_kwh, 2)
    kwh_per_km = (
        round(energy_kwh / distance_km, 3)
        if energy_kwh is not None and distance_km
        else None
    )
    cost_vnd = (
        round(energy_kwh * settings.TELEMETRY_ENERGY_COST_PER_KWH_VND)
        if energy_kwh is not None
        else None
    )

    duration_minutes: int | None = None
    if trip_record.started_at is not None:
        trip_end = trip_record.ended_at or utc_now()
        duration_minutes = max(
            0, int((trip_end - trip_record.started_at).total_seconds() // 60)
        )
    start_latitude, start_longitude = location_to_coordinates(
        trip_record.start_location
    )
    end_latitude, end_longitude = location_to_coordinates(trip_record.end_location)
    actual_driver_id = session_record.driver_id if session_record else None
    actual_vehicle_id = session_record.vehicle_id if session_record else None
    return TripResponse(
        trip_id=trip_record.trip_id,
        organization_id=trip_record.organization_id,
        status=TripStatus(trip_record.status),
        status_reason=trip_record.status_reason,
        planned_by=trip_record.planned_by,
        planned_driver_id=trip_record.planned_driver_id,
        planned_driver_name=planned_driver_name,
        planned_vehicle_id=trip_record.planned_vehicle_id,
        planned_vehicle_vin=planned_vehicle_vin,
        origin_name=trip_record.origin_name,
        destination_name=trip_record.destination_name,
        planned_start_at=trip_record.planned_start_at,
        planned_end_at=trip_record.planned_end_at,
        driving_session_id=trip_record.driving_session_id,
        actual_driver_id=actual_driver_id,
        actual_vehicle_id=actual_vehicle_id,
        actual_vehicle_vin=actual_vehicle_vin,
        started_at=trip_record.started_at,
        ended_at=trip_record.ended_at,
        start_latitude=start_latitude,
        start_longitude=start_longitude,
        end_latitude=end_latitude,
        end_longitude=end_longitude,
        start_odometer_km=_float_or_none(trip_record.start_odometer_km),
        end_odometer_km=_float_or_none(trip_record.end_odometer_km),
        start_soc_percent=_float_or_none(trip_record.start_soc_percent),
        end_soc_percent=_float_or_none(trip_record.end_soc_percent),
        declared_load_status=(
            DeclaredLoadStatus(trip_record.declared_load_status)
            if trip_record.declared_load_status
            else None
        ),
        duration_minutes=duration_minutes,
        distance_km=distance_km,
        energy_kwh=energy_kwh,
        kwh_per_km=kwh_per_km,
        cost_vnd=cost_vnd,
        driver_differs_from_plan=(
            trip_record.planned_driver_id is not None
            and actual_driver_id is not None
            and actual_driver_id != trip_record.planned_driver_id
        ),
        vehicle_differs_from_plan=(
            trip_record.planned_vehicle_id is not None
            and actual_vehicle_id is not None
            and actual_vehicle_id != trip_record.planned_vehicle_id
        ),
        created_at=trip_record.created_at,
        updated_at=trip_record.updated_at,
    )


async def _check_plan_targets(
    db_session: AsyncSession,
    principal: Principal,
    *,
    planned_driver_id: UUID | None,
    planned_vehicle_id: UUID | None,
) -> UUID | None:
    """Check the driver and truck a manager puts on a plan.

    Args:
        db_session: Current database session.
        principal: The planning manager.
        planned_driver_id: Driver profile to assign, if any.
        planned_vehicle_id: Truck to assign, if any.

    Returns:
        The truck's owner organization when a truck is given, else `None`.

    Raises:
        DriverNotFoundError: The driver is not in the manager's reach.
        TripVehicleNotFoundError: The truck does not exist or is out of reach.
    """
    if planned_driver_id is not None:
        driver_record = await driver_repository.get_by_id(
            db_session, planned_driver_id, organization_id=principal.data_scope
        )
        if driver_record is None:
            raise DriverNotFoundError(f"Driver with id '{planned_driver_id}' not found")
    if planned_vehicle_id is None:
        return None
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db_session, planned_vehicle_id
    )
    if vehicle_reference is None or not principal.can_access_organization(
        vehicle_reference.organization_id
    ):
        raise TripVehicleNotFoundError(f"Vehicle '{planned_vehicle_id}' not found")
    return vehicle_reference.organization_id


async def _notify_trip_assigned(
    db_session: AsyncSession, trip_record: TripModel, driver_id: UUID
) -> None:
    """Tell a driver that a manager planned a trip for them (NT-15).

    The alert belongs to the trip's organization and is addressed to the
    driver's person explicitly (no role audience); a driver whose profile no
    longer resolves is skipped. The notifications service never fails the
    caller, so a delivery fault cannot undo the plan.

    Args:
        db_session: The planning transaction.
        trip_record: The planned trip, already stored.
        driver_id: The driver profile the trip was given to.

    Side Effects:
        Writes one `TRIP_ASSIGNED` notification and the driver's inbox row.
    """
    driver_user_id = await driver_service.find_user_id_by_driver_id(
        db_session, driver_id
    )
    if driver_user_id is None:
        return
    planned_start = (
        f" starting {trip_record.planned_start_at.isoformat()}"
        if trip_record.planned_start_at is not None
        else ""
    )
    await notification_service.create_notification(
        db_session,
        organization_id=trip_record.organization_id,
        notification_type=NotificationType.TRIP_ASSIGNED,
        severity=NotificationSeverity.INFO,
        vehicle_id=trip_record.planned_vehicle_id,
        title="New trip assigned",
        body=(
            f"Trip from {trip_record.origin_name or 'an unspecified place'} to "
            f"{trip_record.destination_name or 'an unspecified place'}"
            f"{planned_start}."
        )[:500],
        payload={
            "trip_id": str(trip_record.trip_id),
            "origin_name": trip_record.origin_name,
            "destination_name": trip_record.destination_name,
            "planned_start_at": (
                trip_record.planned_start_at.isoformat()
                if trip_record.planned_start_at is not None
                else None
            ),
            "planned_driver_id": str(driver_id),
        },
        subject_type="TRIP",
        subject_id=trip_record.trip_id,
        recipient_user_ids=[driver_user_id],
    )


async def plan_trip(
    db_session: AsyncSession,
    trip_plan_request: TripPlanRequest,
    *,
    principal: Principal,
) -> TripResponse:
    """Plan a trip: A to B, planned times, optionally a driver and a truck (DR-12).

    Args:
        db_session: Database session owned by the entry boundary.
        trip_plan_request: The plan.
        principal: The planning manager; the trip belongs to their
            organization (the truck's owner when internal staff plan for a
            customer).

    Returns:
        The new PLANNED trip.

    Raises:
        AccessDeniedError: The caller is not a manager.
        DriverNotFoundError: The driver is not in the caller's reach.
        TripVehicleNotFoundError: The truck does not exist or is out of reach.
    """
    _require_manager(principal)
    vehicle_organization_id = await _check_plan_targets(
        db_session,
        principal,
        planned_driver_id=trip_plan_request.planned_driver_id,
        planned_vehicle_id=trip_plan_request.planned_vehicle_id,
    )
    organization_id = (
        vehicle_organization_id
        if principal.is_internal and vehicle_organization_id is not None
        else principal.organization_id
    )
    trip_record = await driver_repository.insert_trip(
        db_session,
        {
            "organization_id": organization_id,
            "status": TripStatus.PLANNED.value,
            "planned_by": principal.user_id,
            "planned_driver_id": trip_plan_request.planned_driver_id,
            "planned_vehicle_id": trip_plan_request.planned_vehicle_id,
            "origin_name": trip_plan_request.origin_name,
            "destination_name": trip_plan_request.destination_name,
            "planned_start_at": trip_plan_request.planned_start_at,
            "planned_end_at": trip_plan_request.planned_end_at,
        },
    )
    if trip_plan_request.planned_driver_id is not None:
        await _notify_trip_assigned(
            db_session, trip_record, trip_plan_request.planned_driver_id
        )
    return await build_trip_response(db_session, trip_record)


async def _load_visible_trip(
    db_session: AsyncSession, trip_id: UUID, principal: Principal
) -> TripModel:
    """Load a trip the caller may read.

    Args:
        db_session: Current database session.
        trip_id: Internal ID of the trip.
        principal: The caller. A manager reads the trips of their data
            scope; a DRIVER-only caller reads the trips assigned to or run by
            their own profile.

    Returns:
        The trip.

    Raises:
        TripNotFoundError: The trip does not exist or is not visible (404,
            never 403, so IDs cannot be guessed).
    """
    if principal.has_any_role(*TRIP_MANAGER_ROLES):
        trip_record = await driver_repository.get_trip_by_id(
            db_session, trip_id, organization_id=principal.data_scope
        )
    else:
        driver_record = await _find_own_driver(db_session, principal)
        trip_record = (
            await driver_repository.get_trip_by_id(
                db_session, trip_id, driver_id=driver_record.driver_id
            )
            if driver_record is not None
            else None
        )
    if trip_record is None:
        raise TripNotFoundError(f"Trip with id '{trip_id}' not found")
    return trip_record


async def get_trip(
    db_session: AsyncSession, trip_id: UUID, *, principal: Principal
) -> TripResponse:
    """Get one trip with its plan, actuals and figures.

    Args:
        db_session: Current database session.
        trip_id: Internal ID of the trip.
        principal: The caller (see `_load_visible_trip`).

    Returns:
        The trip.

    Raises:
        TripNotFoundError: The trip does not exist or is not visible.
    """
    trip_record = await _load_visible_trip(db_session, trip_id, principal)
    return await build_trip_response(db_session, trip_record)


async def list_trips(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    statuses: list[TripStatus] | None = None,
    mine: bool = False,
    driver_id: UUID | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TripListResponse:
    """List trips: the dispatch board for a manager, "my trips" for a driver.

    Args:
        db_session: Current database session.
        principal: The caller. A DRIVER-only caller always gets their own
            trips; a manager gets the trips of their data scope, or their own
            when ``mine`` is set.
        page: Page number, starting from 1.
        page_size: Maximum number of trips per page.
        statuses: Only these statuses, if given.
        mine: Only the caller's own trips (a manager who also drives).
        driver_id: A manager's filter to one driver's trips.
        from_time: Lower bound of the planned (or actual) start, if given.
        to_time: Exclusive upper bound of the planned (or actual) start.
        visible_vehicle_ids: The caller's fleet limit (FL-10): the dispatch
            board of a manager limited to some fleets shows only trips on
            those trucks. `None` means no limit; it does not apply to a
            person's own trips.

    Returns:
        A page of trips, newest plan first. A caller with no driver profile
        who asks for their own trips gets an empty page.
    """
    page_window = normalize_page_window(page, page_size)
    empty_page = TripListResponse(
        items=[], total=0, page=page_window.page, page_size=page_window.page_size
    )
    organization_id = principal.data_scope
    scope_driver_id = driver_id
    if mine or not principal.has_any_role(*TRIP_MANAGER_ROLES):
        own_driver = await _find_own_driver(db_session, principal)
        if own_driver is None:
            return empty_page
        organization_id = None
        scope_driver_id = own_driver.driver_id
        visible_vehicle_ids = None
    status_values = [status.value for status in statuses] if statuses else None
    trip_records = await driver_repository.list_trips(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        organization_id=organization_id,
        driver_id=scope_driver_id,
        statuses=status_values,
        from_time=from_time,
        to_time=to_time,
        vehicle_ids=visible_vehicle_ids,
    )
    total = await driver_repository.count_trips(
        db_session,
        organization_id=organization_id,
        driver_id=scope_driver_id,
        statuses=status_values,
        from_time=from_time,
        to_time=to_time,
        vehicle_ids=visible_vehicle_ids,
    )
    return TripListResponse(
        items=[
            await build_trip_response(db_session, trip_record)
            for trip_record in trip_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_trip(
    db_session: AsyncSession,
    trip_id: UUID,
    trip_update_request: TripUpdateRequest,
    *,
    principal: Principal,
) -> TripResponse:
    """Reroute or reassign a planned trip (the manager's decision, DR-12).

    Args:
        db_session: Current database session.
        trip_id: Internal ID of the trip.
        trip_update_request: The new plan fields and the reason.
        principal: The manager.

    Returns:
        The updated trip.

    Raises:
        AccessDeniedError: The caller is not a manager.
        TripNotFoundError: The trip is not in the caller's reach.
        TripStateConflictError: The trip is not PLANNED any more.
        DriverNotFoundError: The new driver is not in the caller's reach.
        TripVehicleNotFoundError: The new truck is not in the caller's reach.

    Side Effects:
        The change is written to `trip_history` with the caller and the typed
        reason (a fixed text when none is sent).
    """
    _require_manager(principal)
    trip_record = await _load_visible_trip(db_session, trip_id, principal)
    if trip_record.status != TripStatus.PLANNED.value:
        raise TripStateConflictError("Only a planned trip can be changed")
    update_values = {
        field_name: value
        for field_name, value in trip_update_request.model_dump(
            exclude_unset=True, exclude={"reason"}
        ).items()
        if value is not None
    }
    new_start = update_values.get("planned_start_at", trip_record.planned_start_at)
    new_end = update_values.get("planned_end_at", trip_record.planned_end_at)
    if new_start is not None and new_end is not None and new_end <= new_start:
        raise TripInvalidPlanError(
            "The planned arrival must be after the planned departure"
        )
    await _check_plan_targets(
        db_session,
        principal,
        planned_driver_id=update_values.get("planned_driver_id"),
        planned_vehicle_id=update_values.get("planned_vehicle_id"),
    )
    previous_driver_id = trip_record.planned_driver_id
    if update_values:
        trip_record = await driver_repository.update_trip_fields(
            db_session,
            trip_record,
            update_values,
            change_reason=trip_update_request.reason
            or driver_repository.TRIP_EDITED_REASON,
            changed_by=principal.user_id,
        )
    new_driver_id = update_values.get("planned_driver_id")
    if new_driver_id is not None and new_driver_id != previous_driver_id:
        await _notify_trip_assigned(db_session, trip_record, new_driver_id)
    return await build_trip_response(db_session, trip_record)


async def cancel_trip(
    db_session: AsyncSession,
    trip_id: UUID,
    reason: str,
    *,
    principal: Principal,
) -> TripResponse:
    """Cancel a planned trip (the manager's decision, DR-12).

    Args:
        db_session: Current database session.
        trip_id: Internal ID of the trip.
        reason: Why; kept as the status reason and the change reason.
        principal: The manager.

    Returns:
        The CANCELLED trip.

    Raises:
        AccessDeniedError: The caller is not a manager.
        TripNotFoundError: The trip is not in the caller's reach.
        TripStateConflictError: The trip is already running, done or cancelled.
    """
    _require_manager(principal)
    trip_record = await _load_visible_trip(db_session, trip_id, principal)
    if trip_record.status != TripStatus.PLANNED.value:
        raise TripStateConflictError("Only a planned trip can be cancelled")
    trip_record = await driver_repository.update_trip_fields(
        db_session,
        trip_record,
        {"status": TripStatus.CANCELLED.value, "status_reason": reason},
        change_reason=reason,
        changed_by=principal.user_id,
    )
    return await build_trip_response(db_session, trip_record)


async def _require_open_session(
    db_session: AsyncSession, driver_record: DriverModel
) -> DrivingSessionModel:
    """Find the driver's open session or fail (a trip needs a shift, DR-12).

    Args:
        db_session: Current database session.
        driver_record: The driver profile.

    Returns:
        The open driving session.

    Raises:
        TripNotCheckedInError: The driver is not checked in to a truck.
    """
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    if open_session is None:
        raise TripNotCheckedInError("Check in to a truck before starting a trip")
    if (
        await driver_repository.find_in_progress_trip_by_session(
            db_session, open_session.driving_session_id
        )
        is not None
    ):
        raise TripInProgressConflictError("A trip is already in progress")
    return open_session


async def start_trip(
    db_session: AsyncSession,
    trip_id: UUID,
    trip_start_request: TripStartRequest,
    *,
    principal: Principal,
) -> TripResponse:
    """Start a planned trip in the driver's open session (DR-12).

    The driver may be one the plan did not name, and the truck may differ from
    the planned one: both are allowed and flagged in the response. A plan that
    names another driver is not the caller's trip (404).

    Args:
        db_session: Current database session.
        trip_id: Internal ID of the trip.
        trip_start_request: The load the driver declares (MON-13).
        principal: The driver.

    Returns:
        The IN_PROGRESS trip.

    Raises:
        DriverNotFoundError: The caller has no driver profile.
        TripNotFoundError: The trip is not the caller's.
        TripStateConflictError: The trip is not PLANNED.
        TripNotCheckedInError: The driver is not checked in.
        TripInProgressConflictError: The session already has a trip running.

    Side Effects:
        Reads the truck's T-Box sample (telemetry) for the start position,
        odometer and battery %; leaves them empty when it has none recent.
    """
    driver_record = await _require_own_driver(db_session, principal)
    trip_record = await driver_repository.get_trip_by_id(db_session, trip_id)
    if trip_record is None or not (
        trip_record.planned_driver_id == driver_record.driver_id
        or (
            trip_record.planned_driver_id is None
            and trip_record.organization_id == principal.organization_id
        )
    ):
        raise TripNotFoundError(f"Trip with id '{trip_id}' not found")
    if trip_record.status != TripStatus.PLANNED.value:
        raise TripStateConflictError("Only a planned trip can be started")
    open_session = await _require_open_session(db_session, driver_record)

    now = utc_now()
    reading = await driver_service.read_recent_truck_reading(
        db_session, open_session.vehicle_id, now=now
    )
    values: dict[str, object] = {
        "status": TripStatus.IN_PROGRESS.value,
        "driving_session_id": open_session.driving_session_id,
        "started_at": now,
        "declared_load_status": (
            trip_start_request.declared_load_status.value
            if trip_start_request.declared_load_status
            else None
        ),
        **_reading_values("start", reading),
    }
    try:
        trip_record = await driver_repository.update_trip_fields(
            db_session,
            trip_record,
            values,
            change_reason=driver_repository.TRIP_STARTED_REASON,
            changed_by=principal.user_id,
        )
    except IntegrityError as error:
        raise TripInProgressConflictError("A trip is already in progress") from error
    return await build_trip_response(db_session, trip_record)


async def start_personal_trip(
    db_session: AsyncSession,
    personal_trip_request: PersonalTripStartRequest,
    *,
    principal: Principal,
) -> TripResponse:
    """Create and start a trip that nobody planned (a personal driver, DR-12).

    Args:
        db_session: Current database session.
        personal_trip_request: Optional places and the declared load.
        principal: The driver.

    Returns:
        The IN_PROGRESS trip, owned by the truck's owner organization, with
        no plan columns.

    Raises:
        DriverNotFoundError: The caller has no driver profile.
        TripNotCheckedInError: The driver is not checked in.
        TripInProgressConflictError: The session already has a trip running.
    """
    driver_record = await _require_own_driver(db_session, principal)
    open_session = await _require_open_session(db_session, driver_record)
    now = utc_now()
    reading = await driver_service.read_recent_truck_reading(
        db_session, open_session.vehicle_id, now=now
    )
    try:
        trip_record = await driver_repository.insert_trip(
            db_session,
            {
                "organization_id": open_session.organization_id,
                "status": TripStatus.IN_PROGRESS.value,
                "origin_name": personal_trip_request.origin_name,
                "destination_name": personal_trip_request.destination_name,
                "driving_session_id": open_session.driving_session_id,
                "started_at": now,
                "declared_load_status": (
                    personal_trip_request.declared_load_status.value
                    if personal_trip_request.declared_load_status
                    else None
                ),
                **_reading_values("start", reading),
            },
        )
    except IntegrityError as error:
        raise TripInProgressConflictError("A trip is already in progress") from error
    return await build_trip_response(db_session, trip_record)


async def finish_trip(
    db_session: AsyncSession, trip_id: UUID, *, principal: Principal
) -> TripResponse:
    """Finish the driver's trip in progress (DR-12).

    Args:
        db_session: Current database session.
        trip_id: Internal ID of the trip.
        principal: The driver who started it.

    Returns:
        The COMPLETED trip with the end readings.

    Raises:
        DriverNotFoundError: The caller has no driver profile.
        TripNotFoundError: The trip was not run by the caller.
        TripStateConflictError: The trip is not IN_PROGRESS.
    """
    driver_record = await _require_own_driver(db_session, principal)
    trip_record = await driver_repository.get_trip_by_id(db_session, trip_id)
    session_record = (
        await driver_repository.get_session_by_id(
            db_session, trip_record.driving_session_id
        )
        if trip_record is not None and trip_record.driving_session_id is not None
        else None
    )
    if (
        trip_record is None
        or session_record is None
        or session_record.driver_id != driver_record.driver_id
    ):
        raise TripNotFoundError(f"Trip with id '{trip_id}' not found")
    if trip_record.status != TripStatus.IN_PROGRESS.value:
        raise TripStateConflictError("Only a trip in progress can be finished")

    now = utc_now()
    reading = await driver_service.read_recent_truck_reading(
        db_session, session_record.vehicle_id, now=now
    )
    trip_record = await driver_repository.update_trip_fields(
        db_session,
        trip_record,
        {
            "status": TripStatus.COMPLETED.value,
            "ended_at": now,
            **_reading_values("end", reading),
        },
        change_reason=driver_repository.TRIP_FINISHED_REASON,
        changed_by=principal.user_id,
    )
    return await build_trip_response(db_session, trip_record)
