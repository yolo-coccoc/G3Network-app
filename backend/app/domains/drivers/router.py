"""FastAPI router for the HTTP endpoints of the drivers domain.

Domain exceptions are not caught here: `app/api/main.py` maps each shared
base (`NotFoundError` -> 404, `ConflictError` -> 409) to its HTTP status.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
from app.domains.drivers.schemas import (
    DriverAssignmentHistoryResponse,
    DriverCreateRequest,
    DriverListResponse,
    DriverResponse,
    DriverUpdateRequest,
    DriverVehicleAssignmentResponse,
    DriverVehicleAssignRequest,
)
from app.domains.drivers.types import DriverStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["drivers"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=DriverResponse,
    summary="Create a new driver",
    description="Create a new driver in the system. Phone number and license number must be unique.",
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
        DriverConflictError: 409 when the phone number or license number is
            already used by another driver.
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
        description="Case-insensitive substring of the name, phone or license number",
    ),
    vehicle_vin: str | None = Query(
        None,
        min_length=17,
        max_length=17,
        description="Only the driver currently assigned to this vehicle (VIN)",
    ),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverListResponse:
    """Get a paginated list of drivers.

    Args:
        page: Page number.
        page_size: Number of records per page.
        status_filter: Status filter, if any.
        search_text: Name/phone/license substring filter (`q`), if any.
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
    description="Get detailed information about a driver by ID, including the currently assigned vehicle.",
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
        DriverConflictError: 409 when the new phone number or license number
            is already used by another driver.
    """
    return await driver_service.update_driver(
        db_session, driver_id, driver_update_request
    )


@router.delete(
    "/{driver_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a driver",
    description="Soft-delete a driver, closing any active vehicle assignment first.",
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


@router.post(
    "/{driver_id}/assignment",
    status_code=status.HTTP_201_CREATED,
    response_model=DriverVehicleAssignmentResponse,
    summary="Assign a vehicle to a driver",
    description=(
        "Assign a vehicle to a driver by VIN. Reassigns smoothly - a "
        "driver's previous vehicle, if any, is unassigned automatically."
    ),
)
async def assign_vehicle_endpoint(
    driver_id: UUID,
    driver_vehicle_assign_request: DriverVehicleAssignRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverVehicleAssignmentResponse:
    """Assign a vehicle to a driver.

    Args:
        driver_id: Internal ID of the driver.
        driver_vehicle_assign_request: The vehicle to assign, by VIN.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The resulting open assignment.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist.
        DriverVehicleNotFoundError: 404 when the VIN does not resolve to a
            vehicle.
        DriverAssignmentConflictError: 409 when the vehicle is already
            actively assigned to a different driver.
    """
    return await driver_service.assign_vehicle_to_driver(
        db_session, driver_id, driver_vehicle_assign_request
    )


@router.delete(
    "/{driver_id}/assignment",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unassign a driver's current vehicle",
    description="Close a driver's active vehicle assignment.",
)
async def unassign_vehicle_endpoint(
    driver_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    """Unassign a driver's current vehicle.

    Args:
        driver_id: Internal ID of the driver.
        db_session: Database session owned by the HTTP boundary.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist.
        DriverAssignmentNotFoundError: 404 when the driver has no active
            assignment.
    """
    await driver_service.unassign_vehicle_from_driver(db_session, driver_id)


@router.get(
    "/{driver_id}/assignments",
    response_model=DriverAssignmentHistoryResponse,
    summary="Get a driver's vehicle assignment history",
    description="Get a driver's paginated vehicle assignment history, newest first.",
)
async def list_driver_assignment_history_endpoint(
    driver_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> DriverAssignmentHistoryResponse:
    """Get a driver's vehicle assignment history.

    Args:
        driver_id: Internal ID of the driver.
        page: Page number.
        page_size: Number of records per page.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated assignment history.

    Raises:
        DriverNotFoundError: 404 when the driver does not exist.
    """
    return await driver_service.list_driver_assignment_history(
        db_session, driver_id, page=page, page_size=page_size
    )
