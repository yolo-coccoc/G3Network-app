"""FastAPI router for the HTTP endpoints of the drivers domain.

Domain exceptions are not caught here: `app/api/main.py` maps each shared
base (`NotFoundError` -> 404, `ConflictError` -> 409, `InvalidInputError` ->
400) to its HTTP status. `driving_sessions_router` is a second router of this
domain, mounted at `/driving-sessions`.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
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
from app.domains.drivers.types import DriverStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["drivers"])
driving_sessions_router = APIRouter(tags=["driving-sessions"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=DriverResponse,
    summary="Create a driver profile",
    description="Create the driver profile of a membership (one person in one organization).",
)
async def create_driver_endpoint(
    driver_create_request: DriverCreateRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverResponse:
    """Create a new driver.

    Args:
        driver_create_request: Request data for creating the driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created driver.

    Raises:
        DriverMembershipNotFoundError: 404 when the membership is unknown.
        DriverMembershipEndedError: 400 when the person already left.
        DriverLicenseExpiredError: 400 when the licence is expired.
        DriverConflictError: 409 when the membership already has a profile.
    """
    return await driver_service.create_driver(db_session, driver_create_request)


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
        description="Case-insensitive substring of the licence number",
    ),
    vehicle_vin: str | None = Query(
        None,
        min_length=17,
        max_length=17,
        description="Only the driver at the wheel of this vehicle now (VIN)",
    ),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverListResponse:
    """Get a paginated list of drivers.

    Args:
        page: Page number.
        page_size: Number of records per page.
        status_filter: Status filter, if any.
        search_text: Licence number substring filter (`q`), if any.
        vehicle_vin: "Who drives this vehicle now" filter, if any; an
            unknown VIN yields an empty page.
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
    )


@router.get(
    "/{driver_id}",
    response_model=DriverResponse,
    summary="Get driver details",
    description="Get a driver profile by ID, including the truck being driven now.",
)
async def get_driver_endpoint(
    driver_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverResponse:
    """Get the details of a driver by ID.

    Args:
        driver_id: Internal ID of the driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Driver details.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist or was
            soft-deleted.
    """
    return await driver_service.get_driver(db_session, driver_id)


@router.patch(
    "/{driver_id}",
    response_model=DriverResponse,
    summary="Update a driver",
    description="Update driver information. Only the provided fields are updated.",
)
async def update_driver_endpoint(
    driver_id: UUID,
    driver_update_request: DriverUpdateRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverResponse:
    """Partially update a driver.

    Args:
        driver_id: Internal ID of the driver.
        driver_update_request: Request data for updating the driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated driver.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist or was
            soft-deleted.
    """
    return await driver_service.update_driver(
        db_session, driver_id, driver_update_request
    )


@router.delete(
    "/{driver_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a driver",
    description="Soft-delete a driver profile, ending its open driving session first.",
)
async def soft_delete_driver_endpoint(
    driver_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Soft-delete a driver.

    Args:
        driver_id: Internal ID of the driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Success message.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist or was
            already soft-deleted.
    """
    return await driver_service.soft_delete_driver(db_session, driver_id)


@driving_sessions_router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=DrivingSessionResponse,
    summary="Check a driver in to a truck",
    description=(
        "Open a driving session. A truck with another driver at the wheel is "
        "taken over, and the driver's open session on another truck ends; "
        "the response lists the sessions ended."
    ),
)
async def check_in_driver_endpoint(
    check_in_request: DrivingSessionCheckInRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DrivingSessionResponse:
    """Check a driver in to a truck.

    Args:
        check_in_request: Driver, truck VIN, method and optional position.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The open driving session.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist.
        DriverVehicleNotFoundError: 404 when the VIN is unknown.
        DriverNotEligibleError: 400 when the driver may not check in.
    """
    return await driver_service.check_in_driver(db_session, check_in_request)


@driving_sessions_router.post(
    "/check-out",
    response_model=DrivingSessionResponse,
    summary="Check a driver out of a truck",
    description="End the driver's open driving session.",
)
async def check_out_driver_endpoint(
    check_out_request: DrivingSessionCheckOutRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DrivingSessionResponse:
    """End a driver's open driving session.

    Args:
        check_out_request: The driver checking out.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The closed driving session.

    Raises:
        DrivingSessionNotFoundError: 404 when the driver has no open session.
    """
    return await driver_service.check_out_driver(db_session, check_out_request)


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
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DrivingSessionListResponse:
    """Get a paginated list of driving sessions.

    Args:
        page: Page number.
        page_size: Number of records per page.
        driver_id: Driver filter, if any.
        vehicle_vin: Truck filter (VIN), if any.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated driving sessions.
    """
    return await driver_service.list_driving_sessions(
        db_session,
        page=page,
        page_size=page_size,
        driver_id=driver_id,
        vehicle_vin=vehicle_vin,
    )
