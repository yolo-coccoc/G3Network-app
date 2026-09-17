"""FastAPI router for the HTTP endpoints of the drivers domain."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
from app.domains.drivers.exceptions import (
    DriverAssignmentConflictError,
    DriverAssignmentNotFoundError,
    DriverConflictError,
    DriverNotFoundError,
    DriverVehicleNotFoundError,
)
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
    db_session: AsyncSession = Depends(get_db),
) -> DriverResponse:
    """Create a new driver.

    Args:
        driver_create_request: Request data for creating the driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created driver.
    """
    try:
        return await driver_service.create_driver(
            db_session,
            driver_create_request,
        )
    except DriverConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


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
    db_session: AsyncSession = Depends(get_db),
) -> DriverListResponse:
    """Get a paginated list of drivers.

    Args:
        page: Page number.
        page_size: Number of records per page.
        status_filter: Status filter, if any.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of drivers.
    """
    return await driver_service.list_drivers(
        db_session,
        page,
        page_size,
        status_filter,
    )


@router.get(
    "/{driver_id}",
    response_model=DriverResponse,
    summary="Get driver details",
    description="Get detailed information about a driver by ID, including the currently assigned vehicle.",
)
async def get_driver_endpoint(
    driver_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> DriverResponse:
    """Get the details of a driver by ID.

    Args:
        driver_id: Internal ID of the driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Driver details.
    """
    try:
        return await driver_service.get_driver(db_session, driver_id)
    except DriverNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/{driver_id}",
    response_model=DriverResponse,
    summary="Update a driver",
    description="Update driver information. Only the provided fields are updated.",
)
async def update_driver_endpoint(
    driver_id: UUID,
    driver_update_request: DriverUpdateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> DriverResponse:
    """Partially update a driver.

    Args:
        driver_id: Internal ID of the driver.
        driver_update_request: Request data for updating the driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated driver.
    """
    try:
        return await driver_service.update_driver(
            db_session,
            driver_id,
            driver_update_request,
        )
    except DriverNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except DriverConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


@router.delete(
    "/{driver_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a driver",
    description="Soft-delete a driver, closing any active vehicle assignment first.",
)
async def soft_delete_driver_endpoint(
    driver_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Soft-delete a driver.

    Args:
        driver_id: Internal ID of the driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Success message.
    """
    try:
        return await driver_service.soft_delete_driver(db_session, driver_id)
    except DriverNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.post(
    "/{driver_id}/assignment",
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
    db_session: AsyncSession = Depends(get_db),
) -> DriverVehicleAssignmentResponse:
    """Assign a vehicle to a driver.

    Args:
        driver_id: Internal ID of the driver.
        driver_vehicle_assign_request: The vehicle to assign, by VIN.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The resulting open assignment.
    """
    try:
        return await driver_service.assign_vehicle_to_driver(
            db_session,
            driver_id,
            driver_vehicle_assign_request,
        )
    except (DriverNotFoundError, DriverVehicleNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except DriverAssignmentConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


@router.delete(
    "/{driver_id}/assignment",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unassign a driver's current vehicle",
    description="Close a driver's active vehicle assignment.",
)
async def unassign_vehicle_endpoint(
    driver_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> None:
    """Unassign a driver's current vehicle.

    Args:
        driver_id: Internal ID of the driver.
        db_session: Database session owned by the HTTP boundary.
    """
    try:
        await driver_service.unassign_vehicle_from_driver(db_session, driver_id)
    except (DriverNotFoundError, DriverAssignmentNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


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
    db_session: AsyncSession = Depends(get_db),
) -> DriverAssignmentHistoryResponse:
    """Get a driver's vehicle assignment history.

    Args:
        driver_id: Internal ID of the driver.
        page: Page number.
        page_size: Number of records per page.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated assignment history.
    """
    try:
        return await driver_service.list_driver_assignment_history(
            db_session,
            driver_id,
            page,
            page_size,
        )
    except DriverNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
