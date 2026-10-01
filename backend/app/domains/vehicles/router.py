"""FastAPI router for the HTTP endpoints of the vehicles domain.

Handlers only translate HTTP to service calls. Domain exceptions are not
caught here: `app/api/main.py` maps each shared error base once
(`VehicleNotFoundError` -> 404, `VehicleConflictError` -> 409) with the
same `{"detail": message}` body for every router.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.service as vehicle_service
from app.domains.vehicles.schemas import (
    VehicleActivationSummaryResponse,
    VehicleCreateRequest,
    VehicleListResponse,
    VehicleResponse,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import VehicleActivationStatus, VehicleStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["vehicles"])

# Body of a successful DELETE /vehicles/{vehicle_id}: part of the HTTP
# contract, so it lives in the router rather than in the service.
VEHICLE_DELETED_MESSAGE = "Vehicle deleted successfully"


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=VehicleResponse,
    summary="Create a new vehicle",
    description="Create a new vehicle in the system. License plate and VIN must be unique.",
)
async def create_vehicle_endpoint(
    vehicle_create_request: VehicleCreateRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleResponse:
    """Create a new vehicle.

    Args:
        vehicle_create_request: Request data for creating the vehicle.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created vehicle.

    Raises:
        VehicleConflictError: The license plate or VIN already exists (409).
    """
    return await vehicle_service.create_vehicle(
        db_session,
        vehicle_create_request,
    )


@router.get(
    "/",
    response_model=VehicleListResponse,
    summary="Get the list of vehicles",
    description="Get the list of vehicles with pagination, filterable by status "
    "and by F-F2 activation status (both filters combine with AND).",
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
    activation_status_filter: VehicleActivationStatus | None = Query(
        None,
        alias="activation_status",
        description="Filter by F-F2 activation status",
    ),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleListResponse:
    """Get a paginated list of vehicles.

    Args:
        page: Page number.
        page_size: Number of records per page.
        status_filter: Lifecycle status filter (query parameter
            ``status``), if any.
        activation_status_filter: F-F2 activation status filter (query
            parameter ``activation_status``), if any.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of vehicles.
    """
    return await vehicle_service.list_vehicles(
        db_session,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        activation_status_filter=activation_status_filter,
    )


@router.get(
    "/activation-summary",
    response_model=VehicleActivationSummaryResponse,
    summary="Get the fleet-wide device-activation success rate",
    description="Get counts and success rate for the F-F2 device-provisioning flow. "
    "Registered before /{vehicle_id} so it isn't captured as a path parameter.",
)
async def get_vehicle_activation_summary_endpoint(
    db_session: AsyncSession = Depends(get_db, scope="function"),
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
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleResponse:
    """Get the details of a vehicle by ID.

    Args:
        vehicle_id: Internal ID of the vehicle.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Vehicle details.

    Raises:
        VehicleNotFoundError: The vehicle does not exist or is soft-deleted (404).
    """
    return await vehicle_service.get_vehicle(db_session, vehicle_id)


@router.patch(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Update a vehicle",
    description="Update vehicle information. Only the provided fields are updated.",
)
async def update_vehicle_endpoint(
    vehicle_id: UUID,
    vehicle_update_request: VehicleUpdateRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleResponse:
    """Partially update a vehicle.

    Args:
        vehicle_id: Internal ID of the vehicle.
        vehicle_update_request: Request data for updating the vehicle.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated vehicle.

    Raises:
        VehicleNotFoundError: The vehicle does not exist or is soft-deleted (404).
        VehicleConflictError: The new license plate or VIN is already in use (409).
    """
    return await vehicle_service.update_vehicle(
        db_session,
        vehicle_id,
        vehicle_update_request,
    )


@router.delete(
    "/{vehicle_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a vehicle",
    description="Soft-delete a vehicle. The vehicle remains in the database but is not shown in the list.",
)
async def soft_delete_vehicle_endpoint(
    vehicle_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Soft-delete (and decommission) a vehicle.

    Args:
        vehicle_id: Internal ID of the vehicle.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The confirmation body ``{"message": "Vehicle deleted successfully"}``.

    Raises:
        VehicleNotFoundError: The vehicle does not exist or is soft-deleted (404).
    """
    await vehicle_service.soft_delete_vehicle(db_session, vehicle_id)
    return {"message": VEHICLE_DELETED_MESSAGE}
