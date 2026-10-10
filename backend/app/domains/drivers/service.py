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

Not built yet: trips (plan / start / finish, DR-12), the check-in location
check against the truck's last T-Box position and the auto-end of an idle
session. They need telemetry and come with WP5.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.repository as driver_repository
import app.domains.identity.service as identity_service
import app.domains.vehicles.service as vehicle_service
from app.domains.drivers.exceptions import (
    DriverConflictError,
    DriverLicenseExpiredError,
    DriverMembershipEndedError,
    DriverMembershipNotFoundError,
    DriverNotEligibleError,
    DriverNotFoundError,
    DriverVehicleNotFoundError,
    DrivingSessionConflictError,
    DrivingSessionNotFoundError,
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
)
from app.domains.drivers.types import (
    CheckInMethod,
    DriverReference,
    DriverStatus,
    DrivingSessionEndCause,
)
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import (
    MembershipPersonReference,
    MembershipStatus,
    Principal,
    UserRole,
    UserStatus,
    roles_for,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location
from app.libs.common.pagination import normalize_page_window

DRIVER_DELETED_STATUS_REASON = "Driver profile deleted"

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
    db_session: AsyncSession, driver_record: DriverModel
) -> DriverResponse:
    """Build a driver response enriched with the person and the current truck.

    Args:
        db_session: Current database session.
        driver_record: Driver ORM record already queried, created, or
            updated by the caller (all columns populated).

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
        status=driver_record.status,
        status_reason=driver_record.status_reason,
        current_vehicle_id=current_vehicle_id,
        current_vehicle_vin=current_vehicle_vin,
        created_at=driver_record.created_at,
        updated_at=driver_record.updated_at,
    )


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

    return await build_driver_response(db_session, driver_record)


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
) -> DriverListResponse:
    """Get a paginated list of active drivers.

    Args:
        db_session: Current database session.
        principal: The caller; only profiles of the caller's organization are
            listed unless the caller is internal.
        page: Page number, starting from 1.
        page_size: Maximum number of drivers per page.
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the licence number, if any.
        vehicle_vin: Only the driver at the wheel of this vehicle now, if
            given. A VIN that doesn't resolve to an active vehicle yields an
            empty page, not an error: it is a filter, like the others.

    Returns:
        Paginated driver list response.

    Side Effects:
        Calls the vehicles domain's public service to resolve
        ``vehicle_vin``. Read-only; does not commit or rollback.
    """
    page_window = normalize_page_window(page, page_size)

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
    )
    total = await driver_repository.count(
        db_session,
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=principal.data_scope,
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

    Side Effects:
        The change is written to `driver_history` with the caller as actor; a
        `status_reason` typed in the request becomes the change reason,
        otherwise a fixed text.
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

    return await build_driver_response(db_session, updated_driver_record)


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
        await driver_repository.close_session(
            db_session,
            open_session,
            ended_at=utc_now(),
            end_cause=DrivingSessionEndCause.DRIVER_REMOVED,
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


async def build_driving_session_response(
    db_session: AsyncSession,
    session_record: DrivingSessionModel,
    ended_session_records: list[DrivingSessionModel] | None = None,
) -> DrivingSessionResponse:
    """Build a driving session response with the truck's VIN.

    Args:
        db_session: Current database session.
        session_record: The session to present.
        ended_session_records: Sessions the same check-in ended, if any.

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
    """Check a driver in to a truck (DR-07, DR-10).

    Rules:
        The driver must be ACTIVE, not deleted, with an unexpired licence, and
        the membership and the user must be ACTIVE. A driver from another
        organization may drive the truck. Checking in to the truck the driver
        is already at the wheel of returns that session unchanged. Otherwise
        the driver's open session on another truck ends `OTHER_TRUCK` and the
        truck's current session ends `TAKEN_OVER`, then the new session opens
        with `organization_id` = the truck's owner now.

    Args:
        db_session: Database session owned by the entry boundary.
        check_in_request: Driver, truck VIN, method and optional phone position.
        principal: The caller. Without a driver in the request the caller's
            own profile checks in (the truck may belong to another
            organization: a driver scans any truck, DR-07); a manager may
            check in another driver of their organization, then the truck
            must be in the manager's reach too.

    Returns:
        The open session; `ended_sessions` lists the ones this check-in ended.

    Raises:
        DriverNotFoundError: When the driver does not exist in the caller's
            reach.
        AccessDeniedError: A non-manager named another driver.
        DriverVehicleNotFoundError: When the VIN does not resolve to a vehicle.
        DriverNotEligibleError: When the driver may not check in.
        DrivingSessionConflictError: When a concurrent check-in won the race.

    Side Effects:
        May close up to two sessions and inserts one, in the caller's
        transaction; does not commit or rollback.
    """
    driver_record = await _resolve_acting_driver(
        db_session, check_in_request.driver_id, principal
    )
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
        db_session, check_in_request.vehicle_vin
    )
    acts_for_another = driver_record.membership_id != principal.membership_id
    if vehicle_reference is None or (
        acts_for_another
        and not principal.can_access_organization(vehicle_reference.organization_id)
    ):
        raise DriverVehicleNotFoundError(
            f"Vehicle with VIN '{check_in_request.vehicle_vin}' not found"
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
    ended_session_records: list[DrivingSessionModel] = []
    if driver_open_session is not None:
        ended_session_records.append(
            await driver_repository.close_session(
                db_session,
                driver_open_session,
                ended_at=now,
                end_cause=DrivingSessionEndCause.OTHER_TRUCK,
            )
        )
    vehicle_open_session = await driver_repository.find_open_session_by_vehicle(
        db_session, vehicle_reference.vehicle_id
    )
    if vehicle_open_session is not None:
        ended_session_records.append(
            await driver_repository.close_session(
                db_session,
                vehicle_open_session,
                ended_at=now,
                end_cause=DrivingSessionEndCause.TAKEN_OVER,
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
        db_session, session_record, ended_session_records
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
    closed_session = await driver_repository.close_session(
        db_session,
        open_session,
        ended_at=utc_now(),
        end_cause=DrivingSessionEndCause.CHECKED_OUT,
    )
    return await build_driving_session_response(db_session, closed_session)


async def list_driving_sessions(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    driver_id: UUID | None = None,
    vehicle_vin: str | None = None,
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
    )
    total = await driver_repository.count_sessions(
        db_session,
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
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
