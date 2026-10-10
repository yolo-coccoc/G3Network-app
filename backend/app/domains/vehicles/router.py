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
from app.domains.identity.dependencies import (
    get_current_principal,
    get_visible_vehicle_ids,
    require_roles,
)
from app.domains.identity.types import Principal, roles_for
from app.domains.vehicles.schemas import (
    VehicleCreateRequest,
    VehicleListResponse,
    VehicleModelCreateRequest,
    VehicleModelListResponse,
    VehicleModelResponse,
    VehicleModelUpdateRequest,
    VehicleOwnershipPeriodListResponse,
    VehicleResponse,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["vehicles"])

# Mounted separately (``/vehicle-models``): the shared truck-model catalog.
vehicle_models_router = APIRouter(tags=["vehicle models"])

# Who may call what (features.yaml `users`, via `roles_for`): VEH-01 registry,
# VEH-02 ownership (sales), MON-02/BAT-01/WAR-01 read the registry too.
# The model catalog is shared reference data: any signed-in caller reads it,
# only internal staff (VEH-03) change it.
VEHICLE_READERS = require_roles(
    *roles_for("VEH-01", "VEH-02", "MON-02", "BAT-01", "WAR-01")
)
VEHICLE_WRITERS = require_roles(*roles_for("VEH-01", "VEH-02"))
VEHICLE_MODEL_WRITERS = require_roles(*roles_for("VEH-03"), internal_only=True)

# Body of a successful DELETE /vehicles/{vehicle_id}: part of the HTTP
# contract, so it lives in the router rather than in the service.
VEHICLE_DELETED_MESSAGE = "Vehicle deleted successfully"
VEHICLE_MODEL_DELETED_MESSAGE = "Vehicle model removed successfully"


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=VehicleResponse,
    summary="Create a new vehicle",
    description="Create a new vehicle in the system. License plate and VIN must be "
    "unique among vehicles still in the system; the organization and the vehicle "
    "model must exist.",
)
async def create_vehicle_endpoint(
    vehicle_create_request: VehicleCreateRequest,
    principal: Principal = Depends(VEHICLE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleResponse:
    """Create a new vehicle.

    Args:
        vehicle_create_request: Request data for creating the vehicle.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created vehicle.

    Raises:
        VehicleModelNotFoundError: The vehicle model does not exist (404).
        OrganizationNotFoundError: The organization does not exist or is out of
            the caller's reach (404).
        VehicleConflictError: The license plate or VIN already exists (409).
    """
    return await vehicle_service.create_vehicle(
        db_session,
        vehicle_create_request,
        principal=principal,
    )


@router.get(
    "/",
    response_model=VehicleListResponse,
    summary="Get the list of vehicles",
    description="Get the list of vehicles with pagination, filterable by status.",
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
    search: str | None = Query(
        None,
        alias="q",
        min_length=1,
        max_length=50,
        description="Plate or VIN fragment (case-insensitive)",
    ),
    vehicle_model_id: UUID | None = Query(None, description="Filter by truck model"),
    owner_organization_id: UUID | None = Query(
        None,
        alias="organization_id",
        description="Filter by owning organization (internal staff)",
    ),
    principal: Principal = Depends(VEHICLE_READERS),
    visible_vehicle_ids: frozenset[UUID] | None = Depends(get_visible_vehicle_ids),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleListResponse:
    """Get a paginated list of vehicles.

    Args:
        page: Page number.
        page_size: Number of records per page.
        status_filter: Service status filter (query parameter ``status``),
            if any.
        search: Plate or VIN fragment (query parameter ``q``), if any.
        vehicle_model_id: Only trucks of this model, if given.
        owner_organization_id: Only trucks of this owner (query parameter
            ``organization_id``), if given.
        principal: The authenticated caller.
        visible_vehicle_ids: The caller's fleet limit (FL-10), `None` if none.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of vehicles.
    """
    return await vehicle_service.list_vehicles(
        db_session,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
        vehicle_model_id=vehicle_model_id,
        owner_organization_id=owner_organization_id,
        principal=principal,
        visible_vehicle_ids=visible_vehicle_ids,
    )


@router.get(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Get vehicle details",
    description="Get detailed information about a vehicle by ID.",
)
async def get_vehicle_endpoint(
    vehicle_id: UUID,
    principal: Principal = Depends(VEHICLE_READERS),
    visible_vehicle_ids: frozenset[UUID] | None = Depends(get_visible_vehicle_ids),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleResponse:
    """Get the details of a vehicle by ID.

    Args:
        vehicle_id: Internal ID of the vehicle.
        principal: The authenticated caller.
        visible_vehicle_ids: The caller's fleet limit (FL-10), `None` if none.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Vehicle details.

    Raises:
        VehicleNotFoundError: The vehicle does not exist or is soft-deleted (404).
    """
    return await vehicle_service.get_vehicle(
        db_session,
        vehicle_id,
        principal=principal,
        visible_vehicle_ids=visible_vehicle_ids,
    )


@router.get(
    "/{vehicle_id}/ownership-periods",
    response_model=VehicleOwnershipPeriodListResponse,
    summary="Get the ownership periods of a vehicle",
    description="Every period in which an organization owned the truck, read "
    "from the view vehicle_ownership_periods. Internal staff see all owners; "
    "others only their own periods.",
)
async def list_vehicle_ownership_periods_endpoint(
    vehicle_id: UUID,
    principal: Principal = Depends(VEHICLE_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleOwnershipPeriodListResponse:
    """List the ownership periods of a vehicle.

    Args:
        vehicle_id: Internal ID of the vehicle.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The periods, oldest first.

    Raises:
        VehicleNotFoundError: The vehicle does not exist or is out of reach (404).
    """
    return await vehicle_service.list_vehicle_ownership_periods(
        db_session, vehicle_id, principal=principal
    )


@router.patch(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Update a vehicle",
    description="Update vehicle information. Only the provided fields are updated.",
)
async def update_vehicle_endpoint(
    vehicle_id: UUID,
    vehicle_update_request: VehicleUpdateRequest,
    principal: Principal = Depends(VEHICLE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleResponse:
    """Partially update a vehicle.

    Args:
        vehicle_id: Internal ID of the vehicle.
        vehicle_update_request: Request data for updating the vehicle.
        principal: The authenticated caller.
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
        principal=principal,
    )


@router.delete(
    "/{vehicle_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a vehicle",
    description="Soft-delete a vehicle. The vehicle remains in the database but is not shown in the list.",
)
async def soft_delete_vehicle_endpoint(
    vehicle_id: UUID,
    reason: str | None = Query(
        None, min_length=1, max_length=200, description="Why the vehicle is removed"
    ),
    principal: Principal = Depends(VEHICLE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Soft-delete a vehicle (it leaves the system and becomes INACTIVE).

    Args:
        vehicle_id: Internal ID of the vehicle.
        reason: Why the vehicle is removed (kept as the status reason).
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The confirmation body ``{"message": "Vehicle deleted successfully"}``.

    Raises:
        VehicleNotFoundError: The vehicle does not exist or is soft-deleted (404).
    """
    await vehicle_service.soft_delete_vehicle(
        db_session, vehicle_id, principal=principal, reason=reason
    )
    return {"message": VEHICLE_DELETED_MESSAGE}


@vehicle_models_router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=VehicleModelResponse,
    summary="Add a truck model to the catalog",
    description="Add a truck model. Make and model name are unique among models "
    "still offered.",
)
async def create_vehicle_model_endpoint(
    vehicle_model_create_request: VehicleModelCreateRequest,
    principal: Principal = Depends(VEHICLE_MODEL_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleModelResponse:
    """Add a truck model to the catalog.

    Args:
        vehicle_model_create_request: Request data for the new model.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created vehicle model.

    Raises:
        VehicleModelConflictError: The make and model name already exist (409).
    """
    return await vehicle_service.create_vehicle_model(
        db_session, vehicle_model_create_request
    )


@vehicle_models_router.get(
    "/",
    response_model=VehicleModelListResponse,
    summary="Get the truck model catalog",
    description="Get the vehicle models with pagination.",
)
async def list_vehicle_models_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    search: str | None = Query(
        None,
        alias="q",
        min_length=1,
        max_length=50,
        description="Make or model-name fragment (case-insensitive)",
    ),
    make: str | None = Query(
        None, min_length=1, max_length=50, description="Manufacturer"
    ),
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleModelListResponse:
    """Get a paginated list of vehicle models.

    Args:
        page: Page number.
        page_size: Number of records per page.
        search: Make or model-name fragment (query parameter ``q``), if any.
        make: Exact manufacturer, if any.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of vehicle models.
    """
    return await vehicle_service.list_vehicle_models(
        db_session, page=page, page_size=page_size, search=search, make=make
    )


@vehicle_models_router.get(
    "/{vehicle_model_id}",
    response_model=VehicleModelResponse,
    summary="Get a truck model",
    description="Get one vehicle model by ID.",
)
async def get_vehicle_model_endpoint(
    vehicle_model_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleModelResponse:
    """Get a vehicle model by ID.

    Args:
        vehicle_model_id: Internal ID of the vehicle model.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Vehicle model details.

    Raises:
        VehicleModelNotFoundError: The model does not exist or was removed (404).
    """
    return await vehicle_service.get_vehicle_model(db_session, vehicle_model_id)


@vehicle_models_router.patch(
    "/{vehicle_model_id}",
    response_model=VehicleModelResponse,
    summary="Update a truck model",
    description="Correct or complete a model's figures. Only the provided "
    "fields are updated.",
)
async def update_vehicle_model_endpoint(
    vehicle_model_id: UUID,
    vehicle_model_update_request: VehicleModelUpdateRequest,
    principal: Principal = Depends(VEHICLE_MODEL_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleModelResponse:
    """Partially update a vehicle model.

    Args:
        vehicle_model_id: Internal ID of the vehicle model.
        vehicle_model_update_request: Fields to change.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated vehicle model.

    Raises:
        VehicleModelNotFoundError: The model does not exist or was removed (404).
        VehicleModelConflictError: The make and model name exist already (409).
    """
    return await vehicle_service.update_vehicle_model(
        db_session,
        vehicle_model_id,
        vehicle_model_update_request,
        principal=principal,
    )


@vehicle_models_router.delete(
    "/{vehicle_model_id}",
    status_code=status.HTTP_200_OK,
    summary="Remove a truck model from the catalog",
    description="Soft-delete a model: trucks that already use it keep it, new "
    "trucks cannot choose it.",
)
async def soft_delete_vehicle_model_endpoint(
    vehicle_model_id: UUID,
    principal: Principal = Depends(VEHICLE_MODEL_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Remove a vehicle model from the catalog.

    Args:
        vehicle_model_id: Internal ID of the vehicle model.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The confirmation body.

    Raises:
        VehicleModelNotFoundError: The model does not exist or was removed (404).
    """
    await vehicle_service.soft_delete_vehicle_model(
        db_session, vehicle_model_id, principal=principal
    )
    return {"message": VEHICLE_MODEL_DELETED_MESSAGE}
