"""FastAPI router for the HTTP endpoints of the vehicles domain."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.service as vehicle_service
from app.domains.vehicles.exceptions import (
    VehicleConflictError,
    VehicleNotFoundError,
)
from app.domains.vehicles.schemas import (
    VehicleActivationSummaryResponse,
    VehicleCreateRequest,
    VehicleListResponse,
    VehicleResponse,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["vehicles"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=VehicleResponse,
    summary="Create a new vehicle",
    description="Create a new vehicle in the system. License plate and VIN must be unique.",
)
async def create_vehicle_endpoint(
    vehicle_create_request: VehicleCreateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> VehicleResponse:
    """Create a new vehicle.

    Args:
        vehicle_create_request: Request data for creating the vehicle.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created vehicle
    """
    try:
        return await vehicle_service.create_vehicle(
            db_session,
            vehicle_create_request,
        )
    except VehicleConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


@router.get(
    "/",
    response_model=VehicleListResponse,
    summary="Get the list of vehicles",
    description="Get the list of vehicles with pagination and status filtering.",
)
async def list_vehicles_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    status_filter: VehicleStatus | None = Query(
        None, alias="status", description="Filter by status"
    ),
    db_session: AsyncSession = Depends(get_db),
) -> VehicleListResponse:
    """Get a paginated list of vehicles.

    Args:
        page: Page number.
        page_size: Number of records per page.
        status_filter: Status filter, if any.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of vehicles
    """
    return await vehicle_service.list_vehicles(
        db_session,
        page,
        page_size,
        status_filter,
    )


@router.get(
    "/activation-summary",
    response_model=VehicleActivationSummaryResponse,
    summary="Get the fleet-wide device-activation success rate",
    description="Get counts and success rate for the F-F2 device-provisioning flow. "
    "Registered before /{vehicle_id} so it isn't captured as a path parameter.",
)
async def get_vehicle_activation_summary_endpoint(
    db_session: AsyncSession = Depends(get_db),
) -> VehicleActivationSummaryResponse:
    """Get the fleet-wide activation success rate (F-F2).

    Args:
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Activation counts and success rate.
    """
    return await vehicle_service.get_vehicle_activation_summary(db_session)


@router.get(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Get vehicle details",
    description="Get detailed information about a vehicle by ID.",
)
async def get_vehicle_endpoint(
    vehicle_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> VehicleResponse:
    """Get the details of a vehicle by ID.

    Args:
        vehicle_id: Internal ID of the vehicle.
        db_session: Database session owned by the HTTP boundary.
        db: Database session

    Returns:
        Vehicle details
    """
    try:
        return await vehicle_service.get_vehicle(db_session, vehicle_id)
    except VehicleNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Update a vehicle",
    description="Update vehicle information. Only the provided fields are updated.",
)
async def update_vehicle_endpoint(
    vehicle_id: UUID,
    vehicle_update_request: VehicleUpdateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> VehicleResponse:
    """Partially update a vehicle.

    Args:
        vehicle_id: Internal ID of the vehicle.
        vehicle_update_request: Request data for updating the vehicle.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated vehicle
    """
    try:
        return await vehicle_service.update_vehicle(
            db_session,
            vehicle_id,
            vehicle_update_request,
        )
    except VehicleNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except VehicleConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


@router.delete(
    "/{vehicle_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a vehicle",
    description="Soft-delete a vehicle. The vehicle remains in the database but is not shown in the list.",
)
async def soft_delete_vehicle_endpoint(
    vehicle_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Soft-delete a vehicle.

    Args:
        vehicle_id: Internal ID of the vehicle.
        db_session: Database session owned by the HTTP boundary.
        db: Database session

    Returns:
        Success message
    """
    try:
        return await vehicle_service.soft_delete_vehicle(db_session, vehicle_id)
    except VehicleNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
