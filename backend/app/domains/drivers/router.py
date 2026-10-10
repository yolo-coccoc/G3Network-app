"""FastAPI router for the HTTP endpoints of the drivers domain.

Domain exceptions are not caught here: `app/api/main.py` maps each shared
base (`NotFoundError` -> 404, `ConflictError` -> 409, `InvalidInputError` ->
400) to its HTTP status. `driving_sessions_router` is a second router of this
domain, mounted at `/driving-sessions`.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
import app.domains.identity.service as identity_service
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
)
from app.domains.drivers.types import DriverStatus
from app.domains.identity.dependencies import (
    get_client_context,
    require_roles,
)
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    Principal,
    roles_for,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["drivers"])
driving_sessions_router = APIRouter(tags=["driving-sessions"])

# Who may call what (features.yaml `users`, via `roles_for`): DRV-01 driver
# profiles; DRV-02 check-in (the driver themselves, or a manager/dispatcher
# for a driver of the organization). A DRIVER-only caller never reads other
# people's profiles and sees only their own sessions (DR-11).
DRIVER_PROFILE_WRITERS = require_roles(*roles_for("DRV-01"))
DRIVER_PROFILE_READERS = require_roles(*driver_service.DRIVER_MANAGER_ROLES)
DRIVING_SESSION_USERS = require_roles(*roles_for("DRV-02"))


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=DriverResponse,
    summary="Create a driver profile",
    description="Create the driver profile of a membership (one person in one organization).",
)
async def create_driver_endpoint(
    driver_create_request: DriverCreateRequest,
    principal: Principal = Depends(DRIVER_PROFILE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverResponse:
    """Create a new driver.

    Args:
        driver_create_request: Request data for creating the driver.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created driver.

    Raises:
        DriverMembershipNotFoundError: 404 when the membership is unknown.
        DriverMembershipEndedError: 400 when the person already left.
        DriverLicenseExpiredError: 400 when the licence is expired.
        DriverConflictError: 409 when the membership already has a profile.
    """
    return await driver_service.create_driver(
        db_session, driver_create_request, principal=principal
    )


@router.get(
    "/",
    response_model=DriverListResponse,
    summary="Get the list of drivers",
    description="Get the list of drivers with pagination and status filtering.",
)
async def list_drivers_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    status_filter: DriverStatus | None = Query(
        None, alias="status", description="Filter by status"
    ),
    search_text: str | None = Query(
        None,
        alias="q",
        min_length=1,
        max_length=100,
        description=(
            "Case-insensitive substring of the licence number, or of the "
            "person's name or phone number"
        ),
    ),
    vehicle_vin: str | None = Query(
        None,
        min_length=17,
        max_length=17,
        description="Only the driver at the wheel of this vehicle now (VIN)",
    ),
    license_expires_within_days: int | None = Query(
        None,
        ge=0,
        le=3660,
        description="Only licences expiring within this many days (expired included)",
    ),
    principal: Principal = Depends(DRIVER_PROFILE_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverListResponse:
    """Get a paginated list of drivers.

    Args:
        page: Page number.
        page_size: Number of records per page.
        status_filter: Status filter, if any.
        search_text: Licence number / name / phone substring filter (`q`), if any.
        vehicle_vin: "Who drives this vehicle now" filter, if any; an
            unknown VIN yields an empty page.
        license_expires_within_days: Licence expiry reminder filter, if any.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of drivers.
    """
    return await driver_service.list_drivers(
        db_session,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search_text=search_text,
        vehicle_vin=vehicle_vin,
        license_expires_within_days=license_expires_within_days,
        principal=principal,
    )


@router.get(
    "/{driver_id}",
    response_model=DriverResponse,
    summary="Get driver details",
    description="Get a driver profile by ID, including the truck being driven now.",
)
async def get_driver_endpoint(
    driver_id: UUID,
    client_context: ClientContext = Depends(get_client_context),
    principal: Principal = Depends(DRIVER_PROFILE_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverResponse:
    """Get the details of a driver by ID.

    Args:
        driver_id: Internal ID of the driver.
        client_context: IP address and user agent, for the audit row.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Driver details.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist or was
            soft-deleted.
    """
    driver_response = await driver_service.get_driver(
        db_session, driver_id, principal=principal
    )
    # Name, phone and licence of a person: one VIEW row per profile opened.
    await identity_service.record_data_access(
        db_session,
        principal=principal,
        action=AccessAuditAction.VIEW,
        resource_type="DRIVER_PROFILE",
        resource_id=str(driver_id),
        client_context=client_context,
        organization_id=driver_response.organization_id,
    )
    return driver_response


@router.patch(
    "/{driver_id}",
    response_model=DriverResponse,
    summary="Update a driver",
    description="Update driver information. Only the provided fields are updated.",
)
async def update_driver_endpoint(
    driver_id: UUID,
    driver_update_request: DriverUpdateRequest,
    principal: Principal = Depends(DRIVER_PROFILE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverResponse:
    """Partially update a driver.

    Args:
        driver_id: Internal ID of the driver.
        driver_update_request: Request data for updating the driver.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated driver.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist or was
            soft-deleted.
    """
    return await driver_service.update_driver(
        db_session, driver_id, driver_update_request, principal=principal
    )


@router.delete(
    "/{driver_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a driver",
    description="Soft-delete a driver profile, ending its open driving session first.",
)
async def soft_delete_driver_endpoint(
    driver_id: UUID,
    reason: str | None = Query(
        None, min_length=1, max_length=200, description="Why the profile is removed"
    ),
    principal: Principal = Depends(DRIVER_PROFILE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Soft-delete a driver.

    Args:
        driver_id: Internal ID of the driver.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Success message.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist or was
            already soft-deleted.
    """
    return await driver_service.soft_delete_driver(
        db_session, driver_id, principal=principal, reason=reason
    )


@driving_sessions_router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=DrivingSessionResponse,
    summary="Check a driver in to a truck",
    description=(
        "Open a driving session. A truck with another driver at the wheel is "
        "taken over, and the driver's open session on another truck ends; "
        "the response lists the sessions ended and the warnings. The truck is "
        "named by `vehicle_code` (VIN or plate); QR and APP check-ins carry "
        "the phone position."
    ),
)
async def check_in_driver_endpoint(
    check_in_request: DrivingSessionCheckInRequest,
    principal: Principal = Depends(DRIVING_SESSION_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DrivingSessionResponse:
    """Check a driver in to a truck.

    Args:
        check_in_request: Driver, truck code, method and phone position.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The open driving session.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist.
        DriverVehicleNotFoundError: 404 when the truck code is unknown.
        DriverNotEligibleError: 400 when the driver may not check in.
        DriverTooFarFromVehicleError: 400 when the phone is far from the truck.
    """
    return await driver_service.check_in_driver(
        db_session, check_in_request, principal=principal
    )


@driving_sessions_router.post(
    "/check-out",
    response_model=DrivingSessionResponse,
    summary="Check a driver out of a truck",
    description="End the driver's open driving session.",
)
async def check_out_driver_endpoint(
    check_out_request: DrivingSessionCheckOutRequest,
    principal: Principal = Depends(DRIVING_SESSION_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DrivingSessionResponse:
    """End a driver's open driving session.

    Args:
        check_out_request: The driver checking out.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The closed driving session.

    Raises:
        DrivingSessionNotFoundError: 404 when the driver has no open session.
    """
    return await driver_service.check_out_driver(
        db_session, check_out_request, principal=principal
    )


@driving_sessions_router.get(
    "/",
    response_model=DrivingSessionListResponse,
    summary="Get the list of driving sessions",
    description="Driving sessions, newest first, optionally of one driver or truck.",
)
async def list_driving_sessions_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    driver_id: UUID | None = Query(None, description="Only this driver's sessions"),
    vehicle_vin: str | None = Query(
        None, min_length=17, max_length=17, description="Only this truck's sessions"
    ),
    client_context: ClientContext = Depends(get_client_context),
    principal: Principal = Depends(DRIVING_SESSION_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DrivingSessionListResponse:
    """Get a paginated list of driving sessions.

    Args:
        page: Page number.
        page_size: Number of records per page.
        driver_id: Driver filter, if any.
        vehicle_vin: Truck filter (VIN), if any.
        client_context: IP address and user agent, for the audit row.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated driving sessions.
    """
    session_list_response = await driver_service.list_driving_sessions(
        db_session,
        page=page,
        page_size=page_size,
        driver_id=driver_id,
        vehicle_vin=vehicle_vin,
        principal=principal,
    )
    if principal.has_any_role(*driver_service.DRIVER_MANAGER_ROLES):
        # A manager sees who drove where (personal data, DR-08): one VIEW row
        # per screen. A driver reading their own summary needs none.
        await identity_service.record_data_access(
            db_session,
            principal=principal,
            action=AccessAuditAction.VIEW,
            resource_type="DRIVING_SESSIONS",
            resource_id=None if driver_id is None else str(driver_id),
            client_context=client_context,
            details={"vehicle_vin": vehicle_vin} if vehicle_vin else None,
        )
    return session_list_response


@driving_sessions_router.get(
    "/current",
    response_model=DrivingSessionResponse,
    summary="Get the caller's open driving session",
    description=(
        "The truck the caller is checked in to now (the app's home, DR-07); "
        "404 when not checked in."
    ),
)
async def get_current_driving_session_endpoint(
    principal: Principal = Depends(DRIVING_SESSION_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DrivingSessionResponse:
    """Get the caller's own open driving session.

    Args:
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The open session.

    Raises:
        DriverNotFoundError: 404 when the caller has no driver profile.
        DrivingSessionNotFoundError: 404 when the caller is not checked in.
    """
    return await driver_service.get_current_driving_session(
        db_session, principal=principal
    )


@driving_sessions_router.get(
    "/mine/summary",
    response_model=DrivingSummaryResponse,
    summary="Get the caller's own driving summary",
    description=(
        "Per session the date, truck plate, check-in and check-out, duration "
        "and distance, plus totals per day and week (DR-11). Never the route, "
        "GPS trail or places."
    ),
)
async def get_own_driving_summary_endpoint(
    from_time: datetime | None = Query(
        None, alias="from", description="Start of the range (with a time zone)"
    ),
    to_time: datetime | None = Query(
        None, alias="to", description="End of the range (with a time zone)"
    ),
    principal: Principal = Depends(DRIVING_SESSION_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DrivingSummaryResponse:
    """Get the caller's own driving summary.

    Args:
        from_time: Start of the range; 30 days before `to` when omitted.
        to_time: End of the range; now when omitted.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Sessions and totals per day and week.

    Raises:
        DrivingSummaryRangeError: 400 when the range is empty, longer than a
            year, or a time has no zone.
    """
    return await driver_service.get_own_driving_summary(
        db_session, principal=principal, from_time=from_time, to_time=to_time
    )
