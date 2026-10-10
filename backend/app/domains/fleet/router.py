"""FastAPI router for the HTTP endpoints of the fleet domain.

Domain exceptions are not caught here: `app/api/main.py` maps each shared
base (`NotFoundError` -> 404, `ConflictError` -> 409, `InvalidInputError` -> 400)
to its HTTP status.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.assignment_service as assignment_service
import app.domains.fleet.service as fleet_service
from app.domains.fleet.schemas import (
    FleetCreateRequest,
    FleetListResponse,
    FleetMembershipHistoryResponse,
    FleetMembershipResponse,
    FleetResponse,
    FleetTreeResponse,
    FleetUpdateRequest,
    FleetUserAssignmentCreateRequest,
    FleetUserAssignmentListResponse,
    FleetUserAssignmentResponse,
    FleetVehicleAddRequest,
    FleetVehicleListResponse,
    GeofenceCreateRequest,
    GeofenceListResponse,
    GeofenceResponse,
    GeofenceUpdateRequest,
)
from app.domains.identity.dependencies import require_roles
from app.domains.identity.types import Principal, roles_for
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
from app.libs.common.reason import Reason
from app.libs.db.session import get_db

router = APIRouter(tags=["fleet"])

# The fleet limits of a member (FL-10) are managed under the memberships path
# (`/memberships/{membership_id}/fleets`); the app registers this router with
# the `/memberships` prefix next to the identity memberships router.
membership_fleets_router = APIRouter(tags=["fleet"])

# Who may call what (features.yaml `users`, via `roles_for`): FLT-01 fleets,
# FLT-02 membership, FLT-04 list/map, FLT-05 geofences, FLT-03 fleet limits of
# a member (organization administrator and internal staff). A FLEET_MANAGER or
# DISPATCHER limited by `fleet_user_assignments` (FL-10) only reaches the
# fleets given to them and everything below: the service applies it.
FLEET_READERS = require_roles(*roles_for("FLT-01", "FLT-02", "FLT-04", "FLT-05"))
FLEET_WRITERS = require_roles(*roles_for("FLT-01"))
FLEET_MEMBERSHIP_WRITERS = require_roles(*roles_for("FLT-02"))
GEOFENCE_WRITERS = require_roles(*roles_for("FLT-05"))
FLEET_LIMIT_MANAGERS = require_roles(*roles_for("FLT-03"))


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
    principal: Principal = Depends(FLEET_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetResponse:
    """Create a new fleet.

    Args:
        fleet_create_request: Request data for creating the fleet.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created fleet.

    Raises:
        FleetConflictError: 409 when the fleet code is already used.
        FleetParentNotFoundError: 404 when the parent fleet does not exist.
    """
    return await fleet_service.create_fleet(
        db_session, fleet_create_request, principal=principal
    )


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
    parent_fleet_id: UUID | None = Query(
        None,
        description=(
            "Only the fleets directly under this fleet (every fleet below it "
            "with include_descendants)"
        ),
    ),
    include_descendants: bool = Query(
        False,
        description=(
            "With parent_fleet_id, list all the fleets below it; always makes "
            "vehicle_count include the trucks of the sub-fleets"
        ),
    ),
    principal: Principal = Depends(FLEET_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetListResponse:
    """Get a paginated list of fleets.

    Args:
        page: Page number.
        page_size: Number of records per page.
        search_text: Name/fleet-code substring filter (`q`), if any.
        vehicle_vin: "Which fleet is this vehicle in" filter, if any; an
            unknown VIN yields an empty page.
        parent_fleet_id: Tree filter, if any.
        include_descendants: Whole subtree instead of direct children, and
            roll-up vehicle counts.
        principal: The authenticated caller.
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
        parent_fleet_id=parent_fleet_id,
        include_descendants=include_descendants,
        principal=principal,
    )


@router.get(
    "/tree",
    response_model=FleetTreeResponse,
    summary="Get the fleet tree",
    description=(
        "The nested fleet tree of the organization (FLT-01), each node with "
        "its own and roll-up vehicle count. A manager limited to some fleets "
        "(FL-10) sees only their part of the tree. Declared before the "
        "`/{fleet_id}` route."
    ),
)
async def get_fleet_tree_endpoint(
    organization_id: UUID | None = Query(
        None, description="Organization to show; internal staff only"
    ),
    principal: Principal = Depends(FLEET_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetTreeResponse:
    """Get the nested fleet tree.

    Args:
        organization_id: Organization to show, if not the caller's own
            (internal staff only).
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The tree, roots first.

    Raises:
        OrganizationNotFoundError: 404 when the organization is unknown or
            out of the caller's reach.
    """
    return await fleet_service.get_fleet_tree(
        db_session, principal=principal, organization_id=organization_id
    )


@router.get(
    "/{fleet_id}",
    response_model=FleetResponse,
    summary="Get fleet details",
    description="Get detailed information about a fleet by ID, including its current vehicle count.",
)
async def get_fleet_endpoint(
    fleet_id: UUID,
    include_descendants: bool = Query(
        False, description="Count the trucks of the sub-fleets too"
    ),
    principal: Principal = Depends(FLEET_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetResponse:
    """Get the details of a fleet by ID.

    Args:
        fleet_id: Internal ID of the fleet.
        include_descendants: Roll the vehicle count up over the sub-fleets.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Fleet details.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist or was
            soft-deleted.
    """
    return await fleet_service.get_fleet(
        db_session,
        fleet_id,
        principal=principal,
        include_descendants=include_descendants,
    )


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
    principal: Principal = Depends(FLEET_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetResponse:
    """Partially update a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        fleet_update_request: Request data for updating the fleet.
        principal: The authenticated caller.
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
    return await fleet_service.update_fleet(
        db_session, fleet_id, fleet_update_request, principal=principal
    )


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
    reason: Reason | None = Query(None, description="Why the fleet is deleted"),
    principal: Principal = Depends(FLEET_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Soft-delete a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        reason: Why the fleet is deleted (kept in the fleet's history).
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Success message.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist or was already
            soft-deleted.
        FleetHasSubFleetsError: 409 when live fleets still sit under it.
    """
    return await fleet_service.soft_delete_fleet(
        db_session, fleet_id, principal=principal, reason=reason
    )


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
    principal: Principal = Depends(FLEET_MEMBERSHIP_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetMembershipResponse:
    """Add a vehicle to a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        fleet_vehicle_add_request: The vehicle to add, by VIN.
        principal: The authenticated caller.
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
        db_session, fleet_id, fleet_vehicle_add_request, principal=principal
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
    principal: Principal = Depends(FLEET_MEMBERSHIP_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    """Remove a vehicle from a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        vehicle_vin: VIN of the vehicle to remove.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        FleetVehicleNotFoundError: 404 when no active vehicle has this VIN.
        FleetMembershipNotFoundError: 404 when the vehicle has no active
            membership in this fleet.
    """
    await fleet_service.remove_vehicle_from_fleet(
        db_session, fleet_id, vehicle_vin, principal=principal
    )


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
    principal: Principal = Depends(FLEET_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetVehicleListResponse:
    """Get a fleet's current vehicle list.

    Args:
        fleet_id: Internal ID of the fleet.
        page: Page number.
        page_size: Number of records per page.
        status_filter: Vehicle lifecycle status filter, if any.
        search_text: VIN/license-plate substring filter (`q`), if any.
        principal: The authenticated caller.
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
        principal=principal,
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
    principal: Principal = Depends(FLEET_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetMembershipHistoryResponse:
    """Get a fleet's vehicle membership history.

    Args:
        fleet_id: Internal ID of the fleet.
        page: Page number.
        page_size: Number of records per page.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated membership history.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
    """
    return await fleet_service.list_fleet_membership_history(
        db_session, fleet_id, page=page, page_size=page_size, principal=principal
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
    principal: Principal = Depends(FLEET_MEMBERSHIP_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    """Close an open membership of a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        fleet_vehicle_membership_id: Internal ID of the membership to close.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        FleetMembershipNotFoundError: 404 when the membership is unknown,
            belongs to another fleet, or is already closed.
    """
    await fleet_service.close_fleet_membership(
        db_session, fleet_id, fleet_vehicle_membership_id, principal=principal
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
    principal: Principal = Depends(GEOFENCE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> GeofenceResponse:
    """Create a geofence for a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        geofence_create_request: Name and boundary of the geofence.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The created geofence.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
    """
    return await fleet_service.create_geofence(
        db_session, fleet_id, geofence_create_request, principal=principal
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
    principal: Principal = Depends(FLEET_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> GeofenceListResponse:
    """Get a fleet's geofences.

    Args:
        fleet_id: Internal ID of the fleet.
        page: Page number.
        page_size: Number of records per page.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of live geofences.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
    """
    return await fleet_service.list_geofences(
        db_session, fleet_id, page=page, page_size=page_size, principal=principal
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
    principal: Principal = Depends(FLEET_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> GeofenceResponse:
    """Get one geofence of a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The geofence.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        GeofenceNotFoundError: 404 when the geofence is not a live geofence
            of this fleet.
    """
    return await fleet_service.get_geofence(
        db_session, fleet_id, geofence_id, principal=principal
    )


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
    principal: Principal = Depends(GEOFENCE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> GeofenceResponse:
    """Partially update a geofence.

    Args:
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        geofence_update_request: Fields to change.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated geofence.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        GeofenceNotFoundError: 404 when the geofence is not a live geofence
            of this fleet.
    """
    return await fleet_service.update_geofence(
        db_session, fleet_id, geofence_id, geofence_update_request, principal=principal
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
    principal: Principal = Depends(GEOFENCE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Soft-delete a geofence.

    Args:
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Success message.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
        GeofenceNotFoundError: 404 when the geofence is not a live geofence
            of this fleet.
    """
    return await fleet_service.soft_delete_geofence(
        db_session, fleet_id, geofence_id, principal=principal
    )


@router.get(
    "/{fleet_id}/user-assignments",
    response_model=FleetUserAssignmentListResponse,
    summary="Get the members limited to a fleet",
    description=(
        "The memberships that hold this fleet itself as a fleet limit "
        "(FL-10); members who reach it through an assigned parent are not "
        "listed."
    ),
)
async def list_fleet_user_assignments_endpoint(
    fleet_id: UUID,
    include_closed: bool = Query(False, description="Also list taken-away rows"),
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    principal: Principal = Depends(FLEET_LIMIT_MANAGERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetUserAssignmentListResponse:
    """List the assignments that name a fleet.

    Args:
        fleet_id: Internal ID of the fleet.
        include_closed: Also list assignments that were taken away.
        page: Page number.
        page_size: Number of records per page.
        principal: The authenticated caller (organization administrator or
            internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated assignments.

    Raises:
        FleetNotFoundError: 404 when the fleet does not exist.
    """
    return await assignment_service.list_assignments_by_fleet(
        db_session,
        fleet_id,
        principal=principal,
        include_closed=include_closed,
        page=page,
        page_size=page_size,
    )


@membership_fleets_router.post(
    "/{membership_id}/fleets",
    status_code=status.HTTP_201_CREATED,
    response_model=FleetUserAssignmentResponse,
    summary="Limit a member to a fleet",
    description=(
        "Give a fleet to a membership (FL-10): its fleet manager / dispatcher "
        "roles then reach this fleet and everything below it, and nothing "
        "else. The first assignment turns the limit on. A fleet below one "
        "already assigned is allowed; the response warns about it."
    ),
)
async def assign_fleet_to_membership_endpoint(
    membership_id: UUID,
    fleet_user_assignment_create_request: FleetUserAssignmentCreateRequest,
    principal: Principal = Depends(FLEET_LIMIT_MANAGERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetUserAssignmentResponse:
    """Give a fleet to a membership.

    Args:
        membership_id: Internal ID of the membership to limit.
        fleet_user_assignment_create_request: The fleet to give.
        principal: The authenticated caller, recorded as ``assigned_by``.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The new assignment, with warnings.

    Raises:
        FleetUserAssignmentMembershipNotFoundError: 404 unknown membership.
        FleetNotFoundError: 404 unknown fleet.
        FleetUserAssignmentOrganizationMismatchError: 400 the fleet is not in
            the membership's organization.
        FleetUserAssignmentConflictError: 409 the fleet is already held.
    """
    return await assignment_service.assign_fleet_to_membership(
        db_session,
        membership_id,
        fleet_user_assignment_create_request.fleet_id,
        principal=principal,
    )


@membership_fleets_router.delete(
    "/{membership_id}/fleets/{fleet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take a fleet away from a member",
    description=(
        "End the fleet limit on one fleet (FL-10). Taking away the last one "
        "lifts the limit: the member then reaches the whole organization."
    ),
)
async def unassign_fleet_from_membership_endpoint(
    membership_id: UUID,
    fleet_id: UUID,
    principal: Principal = Depends(FLEET_LIMIT_MANAGERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    """Take a fleet away from a membership.

    Args:
        membership_id: Internal ID of the membership.
        fleet_id: Internal ID of the fleet to take away.
        principal: The authenticated caller, recorded as ``unassigned_by``.
        db_session: Database session owned by the HTTP boundary.

    Raises:
        FleetUserAssignmentMembershipNotFoundError: 404 unknown membership.
        FleetUserAssignmentNotFoundError: 404 the member does not hold it.
    """
    await assignment_service.unassign_fleet_from_membership(
        db_session, membership_id, fleet_id, principal=principal
    )


@membership_fleets_router.get(
    "/{membership_id}/fleets",
    response_model=FleetUserAssignmentListResponse,
    summary="Get the fleets a member is limited to",
    description=(
        "The fleets given to a membership (FL-10). An empty list means the "
        "member is not limited: the whole organization."
    ),
)
async def list_membership_fleet_assignments_endpoint(
    membership_id: UUID,
    include_closed: bool = Query(False, description="Also list taken-away rows"),
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    principal: Principal = Depends(FLEET_LIMIT_MANAGERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> FleetUserAssignmentListResponse:
    """List the fleets given to a membership.

    Args:
        membership_id: Internal ID of the membership.
        include_closed: Also list assignments that were taken away.
        page: Page number.
        page_size: Number of records per page.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated assignments.

    Raises:
        FleetUserAssignmentMembershipNotFoundError: 404 unknown membership.
    """
    return await assignment_service.list_assignments_by_membership(
        db_session,
        membership_id,
        principal=principal,
        include_closed=include_closed,
        page=page,
        page_size=page_size,
    )
