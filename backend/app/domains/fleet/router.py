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
    GeofenceCreateRequest,
    GeofenceListResponse,
    GeofenceResponse,
    GeofenceUpdateRequest,
)
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["fleet"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FleetResponse,
    summary="Create a new fleet",
    description=(
        "Create a new fleet, optionally under a parent fleet. A fleet needs a "
        "name, a fleet code, or both; a fleet code must be unique."
    ),
)
async def create_fleet_endpoint(
    fleet_create_request: FleetCreateRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetResponse:
    """Create a new fleet.

    Args:
        fleet_create_request: Request data for creating the fleet.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created fleet.

    Raises:
        FleetConflictError: 409 when the fleet code is already used.
        FleetParentNotFoundError: 404 when the parent fleet does not exist.
    """
    return await fleet_service.create_fleet(db_session, fleet_create_request)


@router.get(
    "/",
    response_model=FleetListResponse,
    summary="Get the list of fleets",
    description="Get the list of fleets with pagination and search.",
)
async def list_fleets_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    search_text: str | None = Query(
        None,
        alias="q",
        min_length=1,
        max_length=100,
        description="Case-insensitive substring of the fleet name or fleet code",
    ),
    vehicle_vin: str | None = Query(
        None,
        min_length=17,
        max_length=17,
        description="Only the fleet this vehicle (VIN) is currently a member of",
    ),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetListResponse:
    """Get a paginated list of fleets.

    Args:
        page: Page number.
        page_size: Number of records per page.
        search_text: Name/fleet-code substring filter (`q`), if any.
        vehicle_vin: "Which fleet is this vehicle in" filter, if any; an
            unknown VIN yields an empty page.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of fleets.
    """
    return await fleet_service.list_fleets(
        db_session,
        page=page,
        page_size=page_size,
        search_text=search_text,
        vehicle_vin=vehicle_vin,
    )


@router.get(
    "/{fleet_id}",
    response_model=FleetResponse,
    summary="Get fleet details",
    description="Get detailed information about a fleet by ID, including its current vehicle count.",
)
async def get_fleet_endpoint(
    fleet_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
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
    description=(
        "Rename a fleet, change its code, or move it under another fleet. "
        "Only the provided fields are updated."
    ),
)
async def update_fleet_endpoint(
    fleet_id: UUID,
    fleet_update_request: FleetUpdateRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
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
        FleetParentNotFoundError: 404 when the new parent fleet does not
            exist.
        FleetHierarchyLoopError: 400 when the new parent is the fleet itself
            or one of its sub-fleets.
    """
    return await fleet_service.update_fleet(db_session, fleet_id, fleet_update_request)


@router.delete(
    "/{fleet_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a fleet",
    description=(
        "Soft-delete a fleet, closing any active vehicle memberships first. "
        "Refused while live fleets still sit under it."
    ),
)
async def soft_delete_fleet_endpoint(
    fleet_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
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
        FleetHasSubFleetsError: 409 when live fleets still sit under it.
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
    db_session: AsyncSession = Depends(get_db, scope="function"),
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
    db_session: AsyncSession = Depends(get_db, scope="function"),
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
    description=(
        "Get a fleet's paginated current vehicle list (F-E1), optionally "
        "filtered by vehicle status and by a VIN/license-plate substring."
    ),
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
    status_filter: VehicleStatus | None = Query(
        None, alias="status", description="Filter by vehicle lifecycle status"
    ),
    search_text: str | None = Query(
        None,
        alias="q",
        min_length=1,
        max_length=20,
        description="Case-insensitive substring of the VIN or license plate",
    ),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetVehicleListResponse:
    """Get a fleet's current vehicle list.

    Args:
        fleet_id: Internal ID of the fleet.
        page: Page number.
        page_size: Number of records per page.
        status_filter: Vehicle lifecycle status filter, if any.
        search_text: VIN/license-plate substring filter (`q`), if any.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of vehicles currently in the fleet.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
    """
    return await fleet_service.list_fleet_vehicles(
        db_session,
        fleet_id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search_text=search_text,
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
    db_session: AsyncSession = Depends(get_db, scope="function"),
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


@router.delete(
    "/{fleet_id}/memberships/{fleet_vehicle_membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Close a fleet membership by ID",
    description=(
        "Close an open membership by its ID - the way to release a member "
        "whose vehicle was deleted and no longer resolves by VIN."
    ),
)
async def close_fleet_membership_endpoint(
    fleet_id: UUID,
    fleet_vehicle_membership_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    """Close an open membership of a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        fleet_vehicle_membership_id: Internal ID of the membership to close.
        db_session: Database session owned by the HTTP boundary.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        FleetMembershipNotFoundError: 404 when the membership is unknown,
            belongs to another fleet, or is already closed.
    """
    await fleet_service.close_fleet_membership(
        db_session, fleet_id, fleet_vehicle_membership_id
    )


@router.post(
    "/{fleet_id}/geofences",
    status_code=status.HTTP_201_CREATED,
    response_model=GeofenceResponse,
    summary="Create a fleet geofence",
    description=(
        "Create a geofence (F-A5) that applies to the fleet's current member "
        "vehicles. The boundary is a GeoJSON Polygon with exactly one closed "
        "ring of [longitude, latitude] positions."
    ),
)
async def create_geofence_endpoint(
    fleet_id: UUID,
    geofence_create_request: GeofenceCreateRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> GeofenceResponse:
    """Create a geofence for a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        geofence_create_request: Name and boundary of the geofence.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The created geofence.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
    """
    return await fleet_service.create_geofence(
        db_session, fleet_id, geofence_create_request
    )


@router.get(
    "/{fleet_id}/geofences",
    response_model=GeofenceListResponse,
    summary="Get a fleet's geofences",
    description="Get a fleet's paginated live geofences, newest first (F-A5).",
)
async def list_geofences_endpoint(
    fleet_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> GeofenceListResponse:
    """Get a fleet's geofences.

    Args:
        fleet_id: Internal ID of the fleet.
        page: Page number.
        page_size: Number of records per page.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of live geofences.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
    """
    return await fleet_service.list_geofences(
        db_session, fleet_id, page=page, page_size=page_size
    )


@router.get(
    "/{fleet_id}/geofences/{geofence_id}",
    response_model=GeofenceResponse,
    summary="Get a fleet geofence",
    description="Get one live geofence of a fleet (F-A5).",
)
async def get_geofence_endpoint(
    fleet_id: UUID,
    geofence_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> GeofenceResponse:
    """Get one geofence of a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The geofence.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        GeofenceNotFoundError: 404 when the geofence is not a live geofence
            of this fleet.
    """
    return await fleet_service.get_geofence(db_session, fleet_id, geofence_id)


@router.patch(
    "/{fleet_id}/geofences/{geofence_id}",
    response_model=GeofenceResponse,
    summary="Update a fleet geofence",
    description="Update a geofence's name and/or boundary. Only the provided fields are updated.",
)
async def update_geofence_endpoint(
    fleet_id: UUID,
    geofence_id: UUID,
    geofence_update_request: GeofenceUpdateRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> GeofenceResponse:
    """Partially update a geofence.

    Args:
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        geofence_update_request: Fields to change.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated geofence.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        GeofenceNotFoundError: 404 when the geofence is not a live geofence
            of this fleet.
    """
    return await fleet_service.update_geofence(
        db_session, fleet_id, geofence_id, geofence_update_request
    )


@router.delete(
    "/{fleet_id}/geofences/{geofence_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a fleet geofence",
    description="Soft-delete a geofence; it stops applying immediately.",
)
async def soft_delete_geofence_endpoint(
    fleet_id: UUID,
    geofence_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Soft-delete a geofence.

    Args:
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Success message.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        GeofenceNotFoundError: 404 when the geofence is not a live geofence
            of this fleet.
    """
    return await fleet_service.soft_delete_geofence(db_session, fleet_id, geofence_id)
