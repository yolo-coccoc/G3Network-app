"""Business service and public contract of the drivers domain.

This module holds the business rules for driver profiles and driving
sessions (check-in / check-out). Other domains may only call the public
`resolve_*` functions to obtain internal DTOs, and must never receive the ORM
model or HTTP response schema of the drivers domain. This domain depends on
the `vehicles` domain's public service (VIN to vehicle, VIN for a response)
and on the `identity` domain's public service (the person behind a membership:
name, phone number, status); both edges are one-directional.

Every router-facing function takes the authenticated `Principal`: a profile
is read through its membership's organization, a driving session through the
truck owner's organization recorded on it (DM-24), and both answer "not
found" outside the caller's data reach. A driver who is only a DRIVER acts on
their own profile and sees only their own sessions (DR-11).

A check-in compares the phone with the truck's last T-Box position (the
`telemetry` public service, DR-07); an idle session is ended by
`end_idle_driving_sessions` (run by ``monitoring/``), and every way a session
ends also closes the trip still running in it (DR-12). The driver's own
summary (DR-11) reads the distance from the telemetry odometer. Trips
themselves (plan / start / finish) are in ``trip_service.py``.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.repository as driver_repository
import app.domains.identity.service as identity_service
import app.domains.telemetry.service as telemetry_service
import app.domains.telemetry.types as telemetry_types
import app.domains.vehicles.service as vehicle_service
from app.domains.drivers.exceptions import (
    DriverCheckInLocationRequiredError,
    DriverConflictError,
    DriverLicenseExpiredError,
    DriverMembershipEndedError,
    DriverMembershipLockedError,
    DriverMembershipNotFoundError,
    DriverNotEligibleError,
    DriverNotFoundError,
    DriverRoleMissingError,
    DriverTooFarFromVehicleError,
    DriverVehicleNotFoundError,
    DrivingSessionConflictError,
    DrivingSessionNotFoundError,
    DrivingSummaryRangeError,
)
from app.domains.drivers.models import DriverModel, DrivingSessionModel
from app.domains.drivers.schemas import (
    DriverCreateRequest,
    DriverListResponse,
    DriverResponse,
    DriverUpdateRequest,
    DrivingSessionCheckInRequest,
    DrivingSessionCheckOutRequest,
    DrivingSessionListResponse,
    DrivingSessionResponse,
    DrivingSummaryResponse,
    DrivingSummarySessionItem,
    DrivingSummaryTotal,
)
from app.domains.drivers.types import (
    AutoEndSweepResult,
    CheckInMethod,
    CheckInWarning,
    DriverReference,
    DriverStatus,
    DriverWarning,
    DrivingSessionEndCause,
    TripStatus,
)
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import (
    MembershipEndKind,
    MembershipPersonReference,
    MembershipStatus,
    Principal,
    UserRole,
    UserStatus,
    roles_for,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import (
    calculate_distance_m,
    coordinates_to_location,
)
from app.libs.common.pagination import normalize_page_window

logger = logging.getLogger(__name__)

DRIVER_DELETED_STATUS_REASON = "Driver profile deleted"
MEMBERSHIP_ENDED_STATUS_REASON = "Membership ended"
TRIP_AUTO_CLOSED_REASON = "Closed automatically when the driving session ended"

# The driver's own summary is cut at this range and number of sessions, so one
# call stays bounded (a driver has a few sessions a day).
SUMMARY_DEFAULT_RANGE_DAYS = 30
SUMMARY_MAX_RANGE_DAYS = 366
SUMMARY_MAX_SESSIONS = 1000
# Auto-end time used when an organization's settings cannot be read.
DEFAULT_AUTO_END_MINUTES = 120

# Roles that may act for another driver (check a driver in or out): the
# users of DRV-02 except the driver themselves; a DRIVER-only caller acts on
# their own profile.
DRIVER_MANAGER_ROLES = roles_for("DRV-02") - {UserRole.DRIVER}


def _is_license_expired(license_expires_on: date) -> bool:
    """Tell whether a licence expiry date is already past (computed, DR-09).

    Args:
        license_expires_on: Expiry date printed on the licence.

    Returns:
        True when the date is before today (UTC); the expiry day itself is
        still valid.
    """
    return license_expires_on < utc_now().date()


def to_driver_reference(
    driver_record: DriverModel, person: MembershipPersonReference | None
) -> DriverReference:
    """Convert an ORM record into a minimal DTO for other domains.

    Args:
        driver_record: An active driver record.
        person: The person behind the profile's membership, if it resolved.

    Returns:
        DTO containing the internal ID and the person's full name (empty if
        the membership no longer resolves, which the foreign key prevents).
    """
    return DriverReference(
        driver_id=driver_record.driver_id,
        full_name=person.full_name if person else "",
    )


async def resolve_driver_reference_by_id(
    db_session: AsyncSession,
    driver_id: UUID,
    *,
    organization_id: UUID | None = None,
) -> DriverReference | None:
    """Find an active driver by ID and return its internal DTO.

    Args:
        db_session: Database session owned by the entry boundary.
        driver_id: Internal ID of the driver.
        organization_id: Data scope of an HTTP caller: a profile whose
            membership is in another organization is not found. `None`
            means no restriction.

    Returns:
        `DriverReference` if the driver is found; otherwise `None`.

    Side Effects:
        Performs read-only queries only (the driver, then its person through
        the identity service); does not commit or rollback.
    """
    driver_record = await driver_repository.get_by_id(
        db_session, driver_id, organization_id=organization_id
    )
    if driver_record is None:
        return None
    person = await identity_service.resolve_membership_person_reference(
        db_session, driver_record.membership_id
    )
    return to_driver_reference(driver_record, person)


async def resolve_own_driver_reference(
    db_session: AsyncSession,
    membership_id: UUID,
) -> DriverReference | None:
    """Find the live driver profile of a membership. Cross-domain entry point.

    Args:
        db_session: Database session owned by the entry boundary.
        membership_id: Internal ID of the person's membership.

    Returns:
        `DriverReference` of the membership's live profile; `None` if the
        membership has none (or it was deleted).

    Side Effects:
        Read-only queries; does not commit or rollback.
    """
    driver_record = await driver_repository.find_by_membership_id(
        db_session, membership_id
    )
    if driver_record is None or driver_record.deleted_at is not None:
        return None
    person = await identity_service.resolve_membership_person_reference(
        db_session, membership_id
    )
    return to_driver_reference(driver_record, person)


async def build_driver_response(
    db_session: AsyncSession,
    driver_record: DriverModel,
    warnings: list[DriverWarning] | None = None,
) -> DriverResponse:
    """Build a driver response enriched with the person and the current truck.

    Args:
        db_session: Current database session.
        driver_record: Driver ORM record already queried, created, or
            updated by the caller (all columns populated).
        warnings: Notices to carry in the response (create / licence change).

    Returns:
        Response data with the person's name/phone/organization (identity) and
        `current_vehicle_id`/`current_vehicle_vin` from the driver's open
        driving session (both `None` when the driver is not checked in).

    Side Effects:
        Calls the identity and vehicles domains' public services. Read-only;
        does not commit or rollback.
    """
    person = await identity_service.resolve_membership_person_reference(
        db_session, driver_record.membership_id
    )
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    current_vehicle_id: UUID | None = None
    current_vehicle_vin: str | None = None
    if open_session is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, open_session.vehicle_id
        )
        current_vehicle_id = open_session.vehicle_id
        current_vehicle_vin = vehicle_reference.vin if vehicle_reference else None
    return DriverResponse(
        driver_id=driver_record.driver_id,
        membership_id=driver_record.membership_id,
        organization_id=person.organization_id if person else None,
        full_name=person.full_name if person else None,
        phone_number=person.phone_number if person else None,
        license_number=driver_record.license_number,
        license_class=driver_record.license_class,
        license_expires_on=driver_record.license_expires_on,
        is_license_expired=_is_license_expired(driver_record.license_expires_on),
        days_until_license_expiry=(
            driver_record.license_expires_on - utc_now().date()
        ).days,
        status=driver_record.status,
        status_reason=driver_record.status_reason,
        current_vehicle_id=current_vehicle_id,
        current_vehicle_vin=current_vehicle_vin,
        created_at=driver_record.created_at,
        updated_at=driver_record.updated_at,
        warnings=warnings or [],
    )


async def _license_warnings(
    db_session: AsyncSession, license_number: str, user_id: UUID
) -> list[DriverWarning]:
    """Build the notices about a licence number being recorded (DR-09).

    Args:
        db_session: Current database session.
        license_number: The number being recorded.
        user_id: The person it is recorded for.

    Returns:
        `LICENSE_NUMBER_ON_OTHER_PERSON` when a live profile of someone else
        already has the number (a yes/no only, so one organization learns
        nothing about another's driver); otherwise empty.
    """
    if await driver_repository.exists_license_on_other_person(
        db_session, license_number, user_id
    ):
        return [DriverWarning.LICENSE_NUMBER_ON_OTHER_PERSON]
    return []


async def create_driver(
    db_session: AsyncSession,
    driver_create_request: DriverCreateRequest,
    *,
    principal: Principal,
) -> DriverResponse:
    """Create the driver profile of a membership (DR-09).

    Args:
        db_session: Database session owned by the entry boundary.
        driver_create_request: Request data that has passed Pydantic validation.
        principal: The caller; the membership must be in the caller's data
            reach.

    Returns:
        Response for the newly created driver.

    Raises:
        DriverMembershipNotFoundError: When the membership does not exist or
            is in an organization out of the caller's reach.
        DriverMembershipEndedError: When the person already left the
            organization.
        DriverMembershipLockedError: When the membership is locked (an
            invited, not yet accepted membership is allowed).
        DriverRoleMissingError: When the membership holds no DRIVER role.
        DriverLicenseExpiredError: When the licence is already expired.
        DriverConflictError: When the membership already has a profile
            (deleted ones included: a new membership gets a new profile).
    """
    person = await identity_service.resolve_membership_person_reference(
        db_session, driver_create_request.membership_id
    )
    if person is None or not principal.can_access_organization(person.organization_id):
        raise DriverMembershipNotFoundError(
            f"Membership with id '{driver_create_request.membership_id}' not found"
        )
    if person.left_at is not None:
        raise DriverMembershipEndedError(
            "The person has left the organization; a new membership is needed"
        )
    if person.membership_status == MembershipStatus.LOCKED.value:
        raise DriverMembershipLockedError("The person's membership is locked")
    if not await identity_service.membership_holds_role(
        db_session, driver_create_request.membership_id, UserRole.DRIVER
    ):
        raise DriverRoleMissingError(
            "The membership does not hold the DRIVER role; grant it first"
        )
    if _is_license_expired(driver_create_request.license_expires_on):
        raise DriverLicenseExpiredError("The licence is already expired")

    existing_driver = await driver_repository.find_by_membership_id(
        db_session, driver_create_request.membership_id
    )
    if existing_driver is not None:
        raise DriverConflictError(
            "This membership already has a driver profile "
            f"('{existing_driver.driver_id}')"
        )

    try:
        driver_record = await driver_repository.insert(
            db_session,
            {
                "membership_id": driver_create_request.membership_id,
                "license_number": driver_create_request.license_number,
                "license_class": driver_create_request.license_class.value,
                "license_expires_on": driver_create_request.license_expires_on,
                "status": driver_create_request.status,
            },
        )
    except IntegrityError as error:
        raise DriverConflictError(
            "This membership already has a driver profile"
        ) from error

    return await build_driver_response(
        db_session,
        driver_record,
        await _license_warnings(
            db_session, driver_create_request.license_number, person.user_id
        ),
    )


async def get_driver(
    db_session: AsyncSession,
    driver_id: UUID,
    *,
    principal: Principal,
) -> DriverResponse:
    """Get an active driver by ID inside the caller's data reach.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        principal: The caller; a profile of another organization is not found
            unless the caller is internal.

    Returns:
        Response for the driver.

    Raises:
        DriverNotFoundError: When the driver does not exist or has been soft-deleted.
    """
    driver_record = await driver_repository.get_by_id(
        db_session, driver_id, organization_id=principal.data_scope
    )
    if not driver_record:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    return await build_driver_response(db_session, driver_record)


async def list_drivers(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: DriverStatus | None = None,
    search_text: str | None = None,
    vehicle_vin: str | None = None,
    license_expires_within_days: int | None = None,
) -> DriverListResponse:
    """Get a paginated list of active drivers.

    Args:
        db_session: Current database session.
        principal: The caller; only profiles of the caller's organization are
            listed unless the caller is internal.
        page: Page number, starting from 1.
        page_size: Maximum number of drivers per page.
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the licence number, or of
            the person's name or phone number, if any.
        vehicle_vin: Only the driver at the wheel of this vehicle now, if
            given. A VIN that doesn't resolve to an active vehicle yields an
            empty page, not an error: it is a filter, like the others.
        license_expires_within_days: Only profiles whose licence expires within
            this many days (already expired ones included), for the expiry
            reminder (DRV-01).

    Returns:
        Paginated driver list response.

    Side Effects:
        Calls the vehicles domain's public service to resolve
        ``vehicle_vin`` and the identity domain's to find the people matching
        ``search_text``. Read-only; does not commit or rollback.
    """
    page_window = normalize_page_window(page, page_size)
    person_membership_ids: list[UUID] | None = None
    if search_text:
        person_membership_ids = await identity_service.search_membership_ids_by_person(
            db_session, search_text, organization_id=principal.data_scope
        )
    license_expires_by = (
        utc_now().date() + timedelta(days=license_expires_within_days)
        if license_expires_within_days is not None
        else None
    )

    vehicle_id: UUID | None = None
    if vehicle_vin is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
            db_session, vehicle_vin
        )
        if vehicle_reference is None:
            return DriverListResponse(
                items=[],
                total=0,
                page=page_window.page,
                page_size=page_window.page_size,
            )
        vehicle_id = vehicle_reference.vehicle_id

    driver_records = await driver_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=principal.data_scope,
        person_membership_ids=person_membership_ids,
        license_expires_by=license_expires_by,
    )
    total = await driver_repository.count(
        db_session,
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=principal.data_scope,
        person_membership_ids=person_membership_ids,
        license_expires_by=license_expires_by,
    )

    return DriverListResponse(
        items=[
            await build_driver_response(db_session, driver_record)
            for driver_record in driver_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_driver(
    db_session: AsyncSession,
    driver_id: UUID,
    driver_update_request: DriverUpdateRequest,
    *,
    principal: Principal,
) -> DriverResponse:
    """Partially update a driver profile.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        driver_update_request: Field data to update.
        principal: The caller; a profile of another organization is not found
            unless the caller is internal.

    Returns:
        Response for the updated driver.

    Raises:
        DriverNotFoundError: When the driver does not exist or has been soft-deleted.
        DriverLicenseExpiredError: When the new licence expiry is in the past.

    Side Effects:
        The change is written to `driver_history` with the caller as actor; a
        `status_reason` typed in the request becomes the change reason,
        otherwise a fixed text. Setting the profile INACTIVE (deactivation)
        ends the driver's open driving session (`DRIVER_REMOVED`) and the trip
        running in it, in the same transaction.
    """
    driver_record = await driver_repository.get_by_id(
        db_session, driver_id, organization_id=principal.data_scope
    )
    if not driver_record:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    update_values = {
        field_name: value
        for field_name, value in driver_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if "license_class" in update_values:
        update_values["license_class"] = update_values["license_class"].value
    if not update_values:
        return await build_driver_response(db_session, driver_record)
    new_expiry = update_values.get("license_expires_on")
    if new_expiry is not None and _is_license_expired(new_expiry):
        raise DriverLicenseExpiredError("The licence is already expired")

    if update_values.get("status") == DriverStatus.INACTIVE:
        open_session = await driver_repository.find_open_session_by_driver(
            db_session, driver_id
        )
        if open_session is not None:
            await _end_session(
                db_session,
                open_session,
                ended_at=utc_now(),
                end_cause=DrivingSessionEndCause.DRIVER_REMOVED,
                changed_by=principal.user_id,
            )

    reason = update_values.get("status_reason")
    updated_driver_record = await driver_repository.update_fields(
        db_session,
        driver_id,
        update_values,
        change_reason=reason or driver_repository.DRIVER_EDITED_REASON,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if updated_driver_record is None:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    warnings: list[DriverWarning] = []
    if "license_number" in update_values:
        person = await identity_service.resolve_membership_person_reference(
            db_session, updated_driver_record.membership_id
        )
        if person is not None:
            warnings = await _license_warnings(
                db_session, update_values["license_number"], person.user_id
            )
    return await build_driver_response(db_session, updated_driver_record, warnings)


async def soft_delete_driver(
    db_session: AsyncSession,
    driver_id: UUID,
    *,
    principal: Principal,
    reason: str | None = None,
) -> dict[str, str]:
    """Soft-delete a driver, ending the open driving session first.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        principal: The caller; a profile of another organization is not found
            unless the caller is internal.
        reason: Why the profile leaves the system, stored as its status
            reason; a fixed text when omitted.

    Returns:
        Success deletion message.

    Raises:
        DriverNotFoundError: When the driver does not exist or has been soft-deleted.

    Side Effects:
        Ends the driver's open driving session (`DRIVER_REMOVED`) in the same
        transaction, so a deleted driver never holds a truck against the
        open-session unique index.
    """
    if (
        await driver_repository.get_by_id(
            db_session, driver_id, organization_id=principal.data_scope
        )
        is None
    ):
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_id
    )
    if open_session is not None:
        await _end_session(
            db_session,
            open_session,
            ended_at=utc_now(),
            end_cause=DrivingSessionEndCause.DRIVER_REMOVED,
            changed_by=principal.user_id,
        )

    driver_record = await driver_repository.soft_delete(
        db_session,
        driver_id,
        status_reason=reason or DRIVER_DELETED_STATUS_REASON,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if not driver_record:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    return {"message": "Driver deleted successfully"}


async def read_recent_truck_reading(
    db_session: AsyncSession, vehicle_id: UUID, *, now: datetime
) -> telemetry_types.VehicleLiveStatusReference | None:
    """Read the truck's latest T-Box sample if it is recent enough to rely on.

    Used for the phone-to-truck check at check-in (DR-07) and for the
    position, odometer and battery % a trip records at both ends (DR-12).

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the truck.
        now: The current time.

    Returns:
        The latest reading, or `None` when the truck has no telemetry or its
        newest sample was received longer ago than
        ``settings.DRIVERS_CHECKIN_POSITION_MAX_AGE_MINUTES``.

    Side Effects:
        Read-only; calls the telemetry domain's public service.
    """
    live_status = await telemetry_service.resolve_vehicle_live_status(
        db_session, vehicle_id
    )
    if live_status is None:
        return None
    max_age = timedelta(minutes=settings.DRIVERS_CHECKIN_POSITION_MAX_AGE_MINUTES)
    if now - live_status.received_at > max_age:
        return None
    return live_status


async def _auto_close_trip(
    db_session: AsyncSession,
    session_record: DrivingSessionModel,
    *,
    ended_at: datetime,
    changed_by: UUID | None,
) -> None:
    """Close the trip running in a driving session that ends (DR-12).

    The trip becomes COMPLETED with the reason that the system closed it. The
    end position, odometer and battery % are taken from the truck's latest
    sample when it is recent, otherwise left empty.

    Args:
        db_session: Current database session.
        session_record: The session that ends.
        ended_at: When the session ends.
        changed_by: The acting user, or `None` for the auto-end worker.

    Side Effects:
        Updates the trip (and its history row) in the caller's transaction.
    """
    trip_record = await driver_repository.find_in_progress_trip_by_session(
        db_session, session_record.driving_session_id
    )
    if trip_record is None:
        return
    # A session may end before the trip started when the auto-end reference is
    # a past movement time: the trip then lasts zero seconds, never less.
    trip_ended_at = (
        max(ended_at, trip_record.started_at) if trip_record.started_at else ended_at
    )
    values: dict[str, object] = {
        "status": TripStatus.COMPLETED.value,
        "status_reason": TRIP_AUTO_CLOSED_REASON,
        "ended_at": trip_ended_at,
    }
    reading = await read_recent_truck_reading(
        db_session, session_record.vehicle_id, now=utc_now()
    )
    if reading is not None:
        values["end_location"] = coordinates_to_location(
            reading.latitude, reading.longitude
        )
        if reading.odometer_km is not None:
            values["end_odometer_km"] = Decimal(str(round(reading.odometer_km, 1)))
        if reading.soc_percent is not None:
            values["end_soc_percent"] = Decimal(str(round(reading.soc_percent, 2)))
    await driver_repository.update_trip_fields(
        db_session,
        trip_record,
        values,
        change_reason=TRIP_AUTO_CLOSED_REASON,
        changed_by=changed_by,
    )


async def _end_session(
    db_session: AsyncSession,
    session_record: DrivingSessionModel,
    *,
    ended_at: datetime,
    end_cause: DrivingSessionEndCause,
    changed_by: UUID | None,
) -> DrivingSessionModel:
    """End a driving session and close the trip still running in it (DR-12).

    Every way a session can end goes through here, so a trip never stays
    IN_PROGRESS against a closed session.

    Args:
        db_session: Current database session.
        session_record: The open session.
        ended_at: When it ends.
        end_cause: Why it ends.
        changed_by: The acting user, or `None` for the system.

    Returns:
        The closed session.
    """
    closed_session = await driver_repository.close_session(
        db_session, session_record, ended_at=ended_at, end_cause=end_cause
    )
    await _auto_close_trip(
        db_session, closed_session, ended_at=ended_at, changed_by=changed_by
    )
    return closed_session


async def build_driving_session_response(
    db_session: AsyncSession,
    session_record: DrivingSessionModel,
    ended_session_records: list[DrivingSessionModel] | None = None,
    warnings: list[CheckInWarning] | None = None,
) -> DrivingSessionResponse:
    """Build a driving session response with the truck's VIN.

    Args:
        db_session: Current database session.
        session_record: The session to present.
        ended_session_records: Sessions the same check-in ended, if any.
        warnings: Notices of the check-in, if this response answers one.

    Returns:
        Response data; `vehicle_vin` is `None` if the truck no longer resolves.

    Side Effects:
        One per-row call to the vehicles domain's public service (no
        batching, per the repo's query-batching rule). Read-only.
    """
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db_session, session_record.vehicle_id
    )
    return DrivingSessionResponse(
        driving_session_id=session_record.driving_session_id,
        organization_id=session_record.organization_id,
        driver_id=session_record.driver_id,
        vehicle_id=session_record.vehicle_id,
        vehicle_vin=vehicle_reference.vin if vehicle_reference else None,
        check_in_method=CheckInMethod(session_record.check_in_method),
        started_at=session_record.started_at,
        ended_at=session_record.ended_at,
        end_cause=(
            DrivingSessionEndCause(session_record.end_cause)
            if session_record.end_cause
            else None
        ),
        ended_sessions=[
            await build_driving_session_response(db_session, ended_record)
            for ended_record in (ended_session_records or [])
        ],
        warnings=warnings or [],
    )


async def _resolve_acting_driver(
    db_session: AsyncSession, requested_driver_id: UUID | None, principal: Principal
) -> DriverModel:
    """Find the driver profile a check-in or check-out acts for.

    Args:
        db_session: Current database session.
        requested_driver_id: The driver named in the request, if any.
        principal: The caller.

    Returns:
        The caller's own live profile when no driver is named (or the caller
        names themselves); otherwise the named profile, which only a manager
        may name and only inside their data reach.

    Raises:
        DriverNotFoundError: The caller has no live profile, or the named
            driver does not exist in the caller's reach.
        AccessDeniedError: A caller who is not a manager named another driver.
    """
    own_record = await driver_repository.find_by_membership_id(
        db_session, principal.membership_id
    )
    if own_record is not None and own_record.deleted_at is not None:
        own_record = None
    if requested_driver_id is None:
        if own_record is None:
            raise DriverNotFoundError("The caller has no driver profile")
        return own_record
    if own_record is not None and own_record.driver_id == requested_driver_id:
        return own_record
    if not principal.has_any_role(*DRIVER_MANAGER_ROLES):
        raise AccessDeniedError("Only a manager can act for another driver")
    named_record = await driver_repository.get_by_id(
        db_session, requested_driver_id, organization_id=principal.data_scope
    )
    if named_record is None:
        raise DriverNotFoundError(f"Driver with id '{requested_driver_id}' not found")
    return named_record


async def check_in_driver(
    db_session: AsyncSession,
    check_in_request: DrivingSessionCheckInRequest,
    *,
    principal: Principal,
) -> DrivingSessionResponse:
    """Check a driver in to a truck (DR-07, DR-10; DRV-02, DRV-04).

    Checking in also identifies who drives the shift (DRV-04): the session is
    the shift record, with its start and end.

    Rules:
        The driver must be ACTIVE, not deleted, with an unexpired licence, and
        the membership and the user must be ACTIVE. A driver from another
        organization may drive the truck; the response only warns. A QR or APP
        check-in carries the phone position and is refused when it is farther
        than ``settings.DRIVERS_CHECKIN_MAX_DISTANCE_M`` from the truck's last
        T-Box position; a truck with no recent position cannot be checked, so
        the check-in goes through with a `NO_RECENT_TRUCK_POSITION` warning
        (and a log line). Checking in to the truck the driver is already at
        the wheel of returns that session unchanged. Otherwise the driver's
        open session on another truck ends `OTHER_TRUCK` and the truck's
        current session ends `TAKEN_OVER` (each also closes its running
        trip), then the new session opens with `organization_id` = the
        truck's owner now.

    Args:
        db_session: Database session owned by the entry boundary.
        check_in_request: Driver, truck code (VIN or plate), method and the
            phone position.
        principal: The caller. Without a driver in the request the caller's
            own profile checks in (the truck may belong to another
            organization: a driver scans any truck, DR-07); a manager may
            check in another driver of their organization, then the truck
            must be in the manager's reach too, and only a manager may use
            the PORTAL method.

    Returns:
        The open session; `ended_sessions` lists the ones this check-in ended
        and `warnings` the notices.

    Raises:
        DriverNotFoundError: When the driver does not exist in the caller's
            reach.
        AccessDeniedError: A non-manager named another driver or used PORTAL.
        DriverCheckInLocationRequiredError: A QR or APP check-in without the
            phone position.
        DriverVehicleNotFoundError: When the code does not resolve to a vehicle.
        DriverNotEligibleError: When the driver may not check in.
        DriverTooFarFromVehicleError: The phone is too far from the truck.
        DrivingSessionConflictError: When a concurrent check-in won the race.

    Side Effects:
        May close up to two sessions (and their running trips) and inserts
        one, in the caller's transaction; does not commit or rollback.
    """
    is_manager = principal.has_any_role(*DRIVER_MANAGER_ROLES)
    if check_in_request.check_in_method == CheckInMethod.PORTAL and not is_manager:
        raise AccessDeniedError("Only a manager can check a driver in from the portal")
    if (
        check_in_request.check_in_method != CheckInMethod.PORTAL
        and check_in_request.latitude is None
    ):
        raise DriverCheckInLocationRequiredError(
            "The phone position is needed to check in by QR or from the app"
        )
    driver_record = await _resolve_acting_driver(
        db_session, check_in_request.driver_id, principal
    )
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_code(
        db_session, check_in_request.truck_code
    )
    acts_for_another = driver_record.membership_id != principal.membership_id
    if vehicle_reference is None or (
        acts_for_another
        and not principal.can_access_organization(vehicle_reference.organization_id)
    ):
        raise DriverVehicleNotFoundError(
            f"Vehicle '{check_in_request.truck_code}' not found"
        )

    person = await identity_service.resolve_membership_person_reference(
        db_session, driver_record.membership_id
    )
    if driver_record.status != DriverStatus.ACTIVE:
        raise DriverNotEligibleError("The driver profile is not ACTIVE")
    if _is_license_expired(driver_record.license_expires_on):
        raise DriverNotEligibleError("The driver's licence has expired")
    if (
        person is None
        or person.membership_status != MembershipStatus.ACTIVE.value
        or person.left_at is not None
        or person.user_status != UserStatus.ACTIVE.value
    ):
        raise DriverNotEligibleError("The driver's membership or account is not ACTIVE")

    driver_open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    if (
        driver_open_session is not None
        and driver_open_session.vehicle_id == vehicle_reference.vehicle_id
    ):
        return await build_driving_session_response(db_session, driver_open_session)

    now = utc_now()
    warnings: list[CheckInWarning] = []
    if person.organization_id != vehicle_reference.organization_id:
        warnings.append(CheckInWarning.OTHER_ORGANIZATION)
    if (
        check_in_request.check_in_method != CheckInMethod.PORTAL
        and check_in_request.latitude is not None
        and check_in_request.longitude is not None
    ):
        truck_reading = await read_recent_truck_reading(
            db_session, vehicle_reference.vehicle_id, now=now
        )
        if truck_reading is None:
            warnings.append(CheckInWarning.NO_RECENT_TRUCK_POSITION)
            logger.info(
                "check-in without a recent truck position",
                extra={
                    "vehicle_id": str(vehicle_reference.vehicle_id),
                    "driver_id": str(driver_record.driver_id),
                },
            )
        else:
            distance_m = calculate_distance_m(
                check_in_request.latitude,
                check_in_request.longitude,
                truck_reading.latitude,
                truck_reading.longitude,
            )
            if distance_m > settings.DRIVERS_CHECKIN_MAX_DISTANCE_M:
                raise DriverTooFarFromVehicleError(
                    "The phone is too far from the truck "
                    f"({round(distance_m)} m; at most "
                    f"{round(settings.DRIVERS_CHECKIN_MAX_DISTANCE_M)} m)"
                )

    ended_session_records: list[DrivingSessionModel] = []
    if driver_open_session is not None:
        ended_session_records.append(
            await _end_session(
                db_session,
                driver_open_session,
                ended_at=now,
                end_cause=DrivingSessionEndCause.OTHER_TRUCK,
                changed_by=principal.user_id,
            )
        )
    vehicle_open_session = await driver_repository.find_open_session_by_vehicle(
        db_session, vehicle_reference.vehicle_id
    )
    if vehicle_open_session is not None:
        ended_session_records.append(
            await _end_session(
                db_session,
                vehicle_open_session,
                ended_at=now,
                end_cause=DrivingSessionEndCause.TAKEN_OVER,
                changed_by=principal.user_id,
            )
        )

    try:
        session_record = await driver_repository.insert_session(
            db_session,
            organization_id=vehicle_reference.organization_id,
            driver_id=driver_record.driver_id,
            vehicle_id=vehicle_reference.vehicle_id,
            check_in_method=check_in_request.check_in_method,
            check_in_location=coordinates_to_location(
                check_in_request.latitude, check_in_request.longitude
            ),
            started_at=now,
        )
    except IntegrityError as error:
        raise DrivingSessionConflictError(
            "The truck or the driver already has an open driving session"
        ) from error

    return await build_driving_session_response(
        db_session, session_record, ended_session_records, warnings
    )


async def check_out_driver(
    db_session: AsyncSession,
    check_out_request: DrivingSessionCheckOutRequest,
    *,
    principal: Principal,
) -> DrivingSessionResponse:
    """End a driver's open driving session (`CHECKED_OUT`).

    Args:
        db_session: Database session owned by the entry boundary.
        check_out_request: The driver checking out.
        principal: The caller; without a driver in the request the caller's
            own profile checks out, a manager may check out another driver of
            their organization.

    Returns:
        The closed session.

    Raises:
        DriverNotFoundError: When the driver does not exist in the caller's
            reach.
        AccessDeniedError: A non-manager named another driver.
        DrivingSessionNotFoundError: When the driver has no open session.

    Side Effects:
        A trip still in progress in the session is closed as COMPLETED with
        the reason that the system closed it (DR-12).
    """
    driver_record = await _resolve_acting_driver(
        db_session, check_out_request.driver_id, principal
    )
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    if open_session is None:
        raise DrivingSessionNotFoundError(
            f"Driver with id '{driver_record.driver_id}' has no open session"
        )
    closed_session = await _end_session(
        db_session,
        open_session,
        ended_at=utc_now(),
        end_cause=DrivingSessionEndCause.CHECKED_OUT,
        changed_by=principal.user_id,
    )
    return await build_driving_session_response(db_session, closed_session)


async def get_current_driving_session(
    db_session: AsyncSession, *, principal: Principal
) -> DrivingSessionResponse:
    """Get the caller's own open driving session (the app's home, DR-07).

    Args:
        db_session: Current database session.
        principal: The caller; their own driver profile is used.

    Returns:
        The open session.

    Raises:
        DriverNotFoundError: The caller has no driver profile.
        DrivingSessionNotFoundError: The caller is not checked in.
    """
    driver_record = await _resolve_acting_driver(db_session, None, principal)
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    if open_session is None:
        raise DrivingSessionNotFoundError("You are not checked in to a truck")
    return await build_driving_session_response(db_session, open_session)


async def list_driving_sessions(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    driver_id: UUID | None = None,
    vehicle_vin: str | None = None,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> DrivingSessionListResponse:
    """Get a paginated list of driving sessions, newest first.

    Args:
        db_session: Current database session.
        principal: The caller. Sessions are read through the truck owner's
            organization recorded on them; a caller who is only a DRIVER sees
            their own sessions, whatever the filters say (DR-11).
        page: Page number, starting from 1.
        page_size: Maximum number of sessions per page.
        driver_id: Only this driver's sessions, if given.
        vehicle_vin: Only this truck's sessions, if given; an unknown VIN
            yields an empty page.
        visible_vehicle_ids: The caller's fleet limit (FL-10): a manager
            limited to some fleets sees only sessions on those trucks.
            `None` means no limit. A DRIVER-only caller's own sessions are
            not limited.

    Returns:
        Paginated session list, open and closed sessions.
    """
    page_window = normalize_page_window(page, page_size)
    organization_id = principal.data_scope
    if not principal.has_any_role(*DRIVER_MANAGER_ROLES):
        # A DRIVER-only caller: own sessions only, on any owner's trucks.
        own_record = await driver_repository.find_by_membership_id(
            db_session, principal.membership_id
        )
        if own_record is None:
            return DrivingSessionListResponse(
                items=[],
                total=0,
                page=page_window.page,
                page_size=page_window.page_size,
            )
        driver_id = own_record.driver_id
        organization_id = None
        visible_vehicle_ids = None
    vehicle_id: UUID | None = None
    if vehicle_vin is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
            db_session, vehicle_vin
        )
        if vehicle_reference is None:
            return DrivingSessionListResponse(
                items=[],
                total=0,
                page=page_window.page,
                page_size=page_window.page_size,
            )
        vehicle_id = vehicle_reference.vehicle_id

    session_records = await driver_repository.list_sessions(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
        vehicle_ids=visible_vehicle_ids,
    )
    total = await driver_repository.count_sessions(
        db_session,
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
        vehicle_ids=visible_vehicle_ids,
    )
    return DrivingSessionListResponse(
        items=[
            await build_driving_session_response(db_session, session_record)
            for session_record in session_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def is_membership_checked_in_to_vehicle(
    db_session: AsyncSession, membership_id: UUID, vehicle_id: UUID
) -> bool:
    """Tell whether a person is at the wheel of a truck right now. Cross-domain.

    Used by telemetry to let a driver read the live data of the truck they
    are checked in to, whichever organization owns it (mobile scenario 2).

    Args:
        db_session: Session owned by the caller's entry boundary.
        membership_id: The person's membership (their driver profile's owner).
        vehicle_id: Internal ID of the truck.

    Returns:
        True when the membership has a live driver profile with an open
        driving session on that truck.

    Side Effects:
        Read-only queries; does not commit or rollback.
    """
    driver_record = await driver_repository.find_by_membership_id(
        db_session, membership_id
    )
    if driver_record is None or driver_record.deleted_at is not None:
        return False
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    return open_session is not None and open_session.vehicle_id == vehicle_id


async def end_open_session_on_ownership_change(
    db_session: AsyncSession, vehicle_id: UUID, *, ended_at: datetime
) -> UUID | None:
    """End the open driving session of a truck that was sold (VH-12).

    Public cross-domain entry point, called by the ownership-transfer
    orchestration (``app/api/vehicle_transfer.py``): the driver belongs to the
    seller's organization, so the session ends with `OWNER_CHANGED`.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_id: Internal ID of the sold truck.
        ended_at: Effective date of the sale.

    Returns:
        The ID of the session that was ended, or `None` when nobody was
        checked in to the truck.

    Side Effects:
        Stamps ``ended_at`` and ``end_cause``; does not commit or roll back.
    """
    open_session = await driver_repository.find_open_session_by_vehicle(
        db_session, vehicle_id
    )
    if open_session is None:
        return None
    await _end_session(
        db_session,
        open_session,
        # A sale dated before the check-in must not give a negative duration.
        ended_at=max(ended_at, open_session.started_at),
        end_cause=DrivingSessionEndCause.OWNER_CHANGED,
        changed_by=None,
    )
    return open_session.driving_session_id


async def handle_membership_end(
    db_session: AsyncSession,
    *,
    membership_id: UUID,
    kind: MembershipEndKind,
    acting_user_id: UUID,
    reason: str,
) -> None:
    """Close a person's driver profile and driving session when they leave (DR-10).

    Registered with the identity domain's membership-end hook by
    ``app/api/membership_end_hooks.py``, so it runs inside the same
    transaction as the removal, the leave or the lock.

    Rules:
        The open driving session ends `DRIVER_REMOVED` (and its running trip
        closes) for both kinds. An ended membership also sets the profile
        INACTIVE and soft-deletes it with the reason (DM-25); a lock leaves
        the profile alone, because it is reversible and check-in already
        refuses a locked membership.

    Args:
        db_session: Session owned by the entry boundary.
        membership_id: The membership that ended or was locked.
        kind: Whether it ended or was locked.
        acting_user_id: Who did it.
        reason: Why.

    Side Effects:
        Updates the profile (history row with the actor and reason) and the
        session in the caller's transaction; does nothing when the membership
        has no profile.
    """
    driver_record = await driver_repository.find_by_membership_id(
        db_session, membership_id
    )
    if driver_record is None:
        return
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    if open_session is not None:
        await _end_session(
            db_session,
            open_session,
            ended_at=max(utc_now(), open_session.started_at),
            end_cause=DrivingSessionEndCause.DRIVER_REMOVED,
            changed_by=acting_user_id,
        )
    if kind == MembershipEndKind.ENDED and driver_record.deleted_at is None:
        await driver_repository.soft_delete(
            db_session,
            driver_record.driver_id,
            status_reason=reason or MEMBERSHIP_ENDED_STATUS_REASON,
            changed_by=acting_user_id,
        )


async def end_idle_driving_sessions(
    db_session: AsyncSession, *, now: datetime
) -> AutoEndSweepResult:
    """End the open driving sessions whose truck has not moved for too long (DR-07).

    One sweep, run by ``monitoring/auto_end_worker.py``. For each open session
    the organization's auto-end time is read from its settings (ID-45). The
    countdown starts at the truck's last movement since the check-in, or at
    the check-in itself when it has not moved; stopping or switching off only
    starts it. A session ends when the countdown passed and the truck has
    reported since it started (proof that it stood still), and its end time is
    the countdown's start, so the shift does not include the idle wait.

    Args:
        db_session: Session whose transaction the caller owns.
        now: The current time.

    Returns:
        How many sessions were checked, ended and skipped (a truck with no
        telemetry, such as one without a T-Box, is never auto-ended: nothing
        can tell that it stood still).

    Side Effects:
        Per-session reads and, for an idle one, the same writes as a
        check-out (the running trip is closed too); does not commit.
    """
    open_sessions = await driver_repository.list_open_sessions(db_session)
    ended_count = 0
    skipped_count = 0
    for session_record in open_sessions:
        organization_settings = await identity_service.resolve_organization_settings(
            db_session, session_record.organization_id
        )
        auto_end_minutes = (
            organization_settings.driving_session_auto_end_minutes
            if organization_settings is not None
            else DEFAULT_AUTO_END_MINUTES
        )
        last_telemetry_at = await telemetry_service.resolve_last_telemetry_at(
            db_session, session_record.vehicle_id
        )
        if last_telemetry_at is None:
            skipped_count += 1
            continue
        last_movement_at = await telemetry_service.resolve_last_movement_at(
            db_session,
            session_record.vehicle_id,
            since=session_record.started_at,
            min_speed_kmh=settings.DRIVERS_MOVING_SPEED_KMH,
        )
        countdown_start = (
            max(last_movement_at, session_record.started_at)
            if last_movement_at is not None
            else session_record.started_at
        )
        if now - countdown_start < timedelta(minutes=auto_end_minutes):
            continue
        if last_telemetry_at <= countdown_start:
            skipped_count += 1
            continue
        await _end_session(
            db_session,
            session_record,
            ended_at=countdown_start,
            end_cause=DrivingSessionEndCause.AUTO_ENDED,
            changed_by=None,
        )
        ended_count += 1
    return AutoEndSweepResult(
        checked=len(open_sessions), ended=ended_count, skipped=skipped_count
    )


async def get_own_driving_summary(
    db_session: AsyncSession,
    *,
    principal: Principal,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> DrivingSummaryResponse:
    """Build the driver's own driving summary: sessions and totals (DR-11).

    A driver sees only a summary of their own sessions on any owner's trucks:
    date, truck plate, check-in and check-out, duration and distance, plus
    driving-time totals per day and per week. Never the route, the GPS trail,
    stops or places; the distance is the odometer difference.

    Args:
        db_session: Current database session.
        principal: The caller; their own driver profile is used, whatever the
            roles.
        from_time: Start of the range (with a zone); defaults to 30 days
            before ``to_time``.
        to_time: End of the range (with a zone); defaults to now.

    Returns:
        Sessions newest first and totals per day and week; days and weeks are
        cut in ``settings.APP_REPORT_TIMEZONE`` (weeks start on Monday) and a
        session counts in the period it started in. Empty when the caller has
        no driver profile.

    Raises:
        DrivingSummaryRangeError: A time has no zone, the range is empty or
            longer than a year.

    Side Effects:
        One vehicle lookup and one telemetry read per session (no batching,
        per the repo's rule). Read-only.
    """
    now = utc_now()
    range_end = to_time or now
    range_start = from_time or range_end - timedelta(days=SUMMARY_DEFAULT_RANGE_DAYS)
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise DrivingSummaryRangeError("The range times must carry a time zone")
    if range_end <= range_start:
        raise DrivingSummaryRangeError("The range must end after it starts")
    if range_end - range_start > timedelta(days=SUMMARY_MAX_RANGE_DAYS):
        raise DrivingSummaryRangeError(
            f"The range is at most {SUMMARY_MAX_RANGE_DAYS} days"
        )

    empty_response = DrivingSummaryResponse(
        from_time=range_start,
        to_time=range_end,
        sessions=[],
        totals_per_day=[],
        totals_per_week=[],
    )
    driver_record = await driver_repository.find_by_membership_id(
        db_session, principal.membership_id
    )
    if driver_record is None:
        return empty_response
    session_records = await driver_repository.list_sessions_in_range(
        db_session,
        driver_id=driver_record.driver_id,
        start_time=range_start,
        end_time=range_end,
        limit=SUMMARY_MAX_SESSIONS,
    )

    report_zone = ZoneInfo(settings.APP_REPORT_TIMEZONE)
    items: list[DrivingSummarySessionItem] = []
    day_totals: dict[date, list[float]] = defaultdict(lambda: [0, 0, 0.0])
    week_totals: dict[date, list[float]] = defaultdict(lambda: [0, 0, 0.0])
    for session_record in session_records:
        session_end = session_record.ended_at or now
        duration_minutes = max(
            0, int((session_end - session_record.started_at).total_seconds() // 60)
        )
        distance_km = await telemetry_service.resolve_distance_km_in_window(
            db_session,
            session_record.vehicle_id,
            start_time=session_record.started_at,
            end_time=session_end,
        )
        vehicle_summary = await vehicle_service.resolve_vehicle_summary_by_id(
            db_session, session_record.vehicle_id
        )
        start_day = session_record.started_at.astimezone(report_zone).date()
        week_start = start_day - timedelta(days=start_day.weekday())
        for totals, key in ((day_totals, start_day), (week_totals, week_start)):
            totals[key][0] += 1
            totals[key][1] += duration_minutes
            totals[key][2] += distance_km or 0.0
        items.append(
            DrivingSummarySessionItem(
                driving_session_id=session_record.driving_session_id,
                day=start_day,
                license_plate=(
                    vehicle_summary.license_plate if vehicle_summary else None
                ),
                started_at=session_record.started_at,
                ended_at=session_record.ended_at,
                duration_minutes=duration_minutes,
                distance_km=distance_km,
                end_cause=(
                    DrivingSessionEndCause(session_record.end_cause)
                    if session_record.end_cause
                    else None
                ),
            )
        )

    def to_totals(
        totals: dict[date, list[float]],
    ) -> list[DrivingSummaryTotal]:
        """Turn the accumulated figures into response rows, newest first."""
        return [
            DrivingSummaryTotal(
                period_start=period_start,
                session_count=int(figures[0]),
                driving_minutes=int(figures[1]),
                distance_km=round(figures[2], 1),
            )
            for period_start, figures in sorted(totals.items(), reverse=True)
        ]

    return DrivingSummaryResponse(
        from_time=range_start,
        to_time=range_end,
        sessions=items,
        totals_per_day=to_totals(day_totals),
        totals_per_week=to_totals(week_totals),
    )
