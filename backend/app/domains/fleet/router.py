"""FastAPI router for the HTTP endpoints of the fleet domain.

Domain exceptions are not caught here: `app/api/main.py` maps each shared
base (`NotFoundError` -> 404, `ConflictError` -> 409) to its HTTP status.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.service as fleet_service
from app.domains.fleet.schemas import (
    FleetCreateRequest,
    FleetListResponse,
    FleetMembershipHistoryResponse,
    FleetMembershipResponse,
    FleetResponse,
    FleetUpdateRequest,
    FleetVehicleAddRequest,
    FleetVehicleListResponse,
)
from app.domains.fleet.types import FleetStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["fleet"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FleetResponse,
    summary="Create a new fleet",
    description="Create a new fleet in the system. Fleet code must be unique.",
)
async def create_fleet_endpoint(
    fleet_create_request: FleetCreateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> FleetResponse:
    """Create a new fleet.

    Args:
        fleet_create_request: Request data for creating the fleet.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created fleet.

    Raises:
        FleetConflictError: 409 when the fleet code is already used.
    """
    return await fleet_service.create_fleet(db_session, fleet_create_request)


@router.get(
    "/",
    response_model=FleetListResponse,
    summary="Get the list of fleets",
    description="Get the list of fleets with pagination and status filtering.",
)
async def list_fleets_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    status_filter: FleetStatus | None = Query(
        None, alias="status", description="Filter by status"
    ),
    db_session: AsyncSession = Depends(get_db),
) -> FleetListResponse:
    """Get a paginated list of fleets.

    Args:
        page: Page number.
        page_size: Number of records per page.
        status_filter: Status filter, if any.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of fleets.
    """
    return await fleet_service.list_fleets(
        db_session, page=page, page_size=page_size, status_filter=status_filter
    )


@router.get(
    "/{fleet_id}",
    response_model=FleetResponse,
    summary="Get fleet details",
    description="Get detailed information about a fleet by ID, including its current vehicle count.",
)
async def get_fleet_endpoint(
    fleet_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> FleetResponse:
    """Get the details of a fleet by ID.

    Args:
        fleet_id: Internal ID of the fleet.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Fleet details.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist or was
            soft-deleted.
    """
    return await fleet_service.get_fleet(db_session, fleet_id)


@router.patch(
    "/{fleet_id}",
    response_model=FleetResponse,
    summary="Update a fleet",
    description="Update fleet information. Only the provided fields are updated.",
)
async def update_fleet_endpoint(
    fleet_id: UUID,
    fleet_update_request: FleetUpdateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> FleetResponse:
    """Partially update a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        fleet_update_request: Request data for updating the fleet.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated fleet.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist or was
            soft-deleted.
        FleetConflictError: 409 when the new fleet code is already used.
    """
    return await fleet_service.update_fleet(db_session, fleet_id, fleet_update_request)


@router.delete(
    "/{fleet_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a fleet",
    description="Soft-delete a fleet, closing any active vehicle memberships first.",
)
async def soft_delete_fleet_endpoint(
    fleet_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Soft-delete a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Success message.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist or was already
            soft-deleted.
    """
    return await fleet_service.soft_delete_fleet(db_session, fleet_id)


@router.post(
    "/{fleet_id}/vehicles",
    status_code=status.HTTP_201_CREATED,
    response_model=FleetMembershipResponse,
    summary="Add a vehicle to a fleet",
    description="Add a vehicle to a fleet by VIN. The vehicle must not already be actively in another fleet.",
)
async def add_vehicle_to_fleet_endpoint(
    fleet_id: UUID,
    fleet_vehicle_add_request: FleetVehicleAddRequest,
    db_session: AsyncSession = Depends(get_db),
) -> FleetMembershipResponse:
    """Add a vehicle to a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        fleet_vehicle_add_request: The vehicle to add, by VIN.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The resulting open membership.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        FleetVehicleNotFoundError: 404 when the VIN does not resolve to a
            vehicle.
        FleetMembershipConflictError: 409 when the vehicle is already
            actively in a different fleet.
    """
    return await fleet_service.add_vehicle_to_fleet(
        db_session, fleet_id, fleet_vehicle_add_request
    )


@router.delete(
    "/{fleet_id}/vehicles/{vehicle_vin}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a vehicle from a fleet",
    description=(
        "Close a vehicle's active membership in a fleet, identifying the "
        "vehicle by VIN (as when it was added)."
    ),
)
async def remove_vehicle_from_fleet_endpoint(
    fleet_id: UUID,
    vehicle_vin: str = Path(..., min_length=17, max_length=17),
    db_session: AsyncSession = Depends(get_db),
) -> None:
    """Remove a vehicle from a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        vehicle_vin: VIN of the vehicle to remove.
        db_session: Database session owned by the HTTP boundary.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        FleetVehicleNotFoundError: 404 when no active vehicle has this VIN.
        FleetMembershipNotFoundError: 404 when the vehicle has no active
            membership in this fleet.
    """
    await fleet_service.remove_vehicle_from_fleet(db_session, fleet_id, vehicle_vin)


@router.get(
    "/{fleet_id}/vehicles",
    response_model=FleetVehicleListResponse,
    summary="Get a fleet's current vehicles",
    description="Get a fleet's paginated current vehicle list (F-E1).",
)
async def list_fleet_vehicles_endpoint(
    fleet_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    db_session: AsyncSession = Depends(get_db),
) -> FleetVehicleListResponse:
    """Get a fleet's current vehicle list.

    Args:
        fleet_id: Internal ID of the fleet.
        page: Page number.
        page_size: Number of records per page.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of vehicles currently in the fleet.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
    """
    return await fleet_service.list_fleet_vehicles(
        db_session, fleet_id, page=page, page_size=page_size
    )


@router.get(
    "/{fleet_id}/memberships",
    response_model=FleetMembershipHistoryResponse,
    summary="Get a fleet's vehicle membership history",
    description="Get a fleet's paginated vehicle membership history, newest first.",
)
async def list_fleet_membership_history_endpoint(
    fleet_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    db_session: AsyncSession = Depends(get_db),
) -> FleetMembershipHistoryResponse:
    """Get a fleet's vehicle membership history.

    Args:
        fleet_id: Internal ID of the fleet.
        page: Page number.
        page_size: Number of records per page.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated membership history.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
    """
    return await fleet_service.list_fleet_membership_history(
        db_session, fleet_id, page=page, page_size=page_size
    )
