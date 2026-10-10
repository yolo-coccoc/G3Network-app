"""FastAPI router for the HTTP endpoints of the batteries domain (BAT-01).

Handlers only translate HTTP to service calls. Domain exceptions are not
caught here: `app/api/main.py` maps each shared error base once
(not found -> 404, conflict -> 409, invalid input -> 400).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.batteries.service as battery_service
from app.domains.batteries.schemas import (
    BatteryCreateRequest,
    BatteryInstallationPeriodListResponse,
    BatteryInstallRequest,
    BatteryListResponse,
    BatteryModelCreateRequest,
    BatteryModelListResponse,
    BatteryModelResponse,
    BatteryModelUpdateRequest,
    BatteryOwnershipTransferRequest,
    BatteryResponse,
    BatteryUpdateRequest,
)
from app.domains.batteries.types import BatteryChemistry, BatteryStatus
from app.domains.identity.dependencies import get_current_principal, require_roles
from app.domains.identity.types import Principal, roles_for
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["batteries"])

# Mounted separately (``/battery-models``): the shared battery type catalog.
battery_models_router = APIRouter(tags=["battery models"])

# BAT-01 is an internal feature (offer: internal): its roles read the
# registry inside their data scope, and only staff of our own organizations
# change it. The model catalog is shared reference data: any signed-in caller
# reads it.
BATTERY_READERS = require_roles(*roles_for("BAT-01"))
BATTERY_WRITERS = require_roles(*roles_for("BAT-01"), internal_only=True)

BATTERY_DELETED_MESSAGE = "Battery deleted successfully"
BATTERY_MODEL_DELETED_MESSAGE = "Battery model removed successfully"


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=BatteryResponse,
    summary="Register a battery",
    description="Register a physical battery as an asset. The serial number is "
    "unique among batteries still in the system.",
)
async def create_battery_endpoint(
    battery_create_request: BatteryCreateRequest,
    principal: Principal = Depends(BATTERY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryResponse:
    """Register a battery.

    Args:
        battery_create_request: Request data for the new battery.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The created battery.

    Raises:
        BatteryModelNotFoundError: The model is not in the catalog (404).
        BatteryConflictError: The serial number exists (409).
    """
    return await battery_service.create_battery(
        db_session, battery_create_request, principal=principal
    )


@router.get(
    "/",
    response_model=BatteryListResponse,
    summary="Get the list of batteries",
    description="Batteries with pagination, filterable by serial fragment, "
    "status, model, truck, fitted or in stock, and owner.",
)
async def list_batteries_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    search: str | None = Query(
        None, alias="q", min_length=1, max_length=50, description="Serial fragment"
    ),
    status_filter: BatteryStatus | None = Query(
        None, alias="status", description="Filter by status"
    ),
    battery_model_id: UUID | None = Query(None, description="Filter by model"),
    vehicle_id: UUID | None = Query(None, description="The battery fitted to a truck"),
    is_installed: bool | None = Query(
        None, description="True: fitted to a truck; false: in stock"
    ),
    owner_organization_id: UUID | None = Query(
        None, alias="organization_id", description="Filter by owning organization"
    ),
    principal: Principal = Depends(BATTERY_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryListResponse:
    """Get a paginated list of batteries.

    Args:
        page: Page number.
        page_size: Number of records per page.
        search: Serial-number fragment (query parameter ``q``).
        status_filter: Status (query parameter ``status``).
        battery_model_id: Only batteries of this model.
        vehicle_id: Only the battery fitted to this truck.
        is_installed: Fitted or in stock.
        owner_organization_id: Only batteries of this owner (query parameter
            ``organization_id``).
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of batteries.
    """
    return await battery_service.list_batteries(
        db_session,
        principal=principal,
        page=page,
        page_size=page_size,
        search=search,
        status_filter=status_filter,
        battery_model_id=battery_model_id,
        vehicle_id=vehicle_id,
        is_installed=is_installed,
        owner_organization_id=owner_organization_id,
    )


@router.get(
    "/{battery_id}",
    response_model=BatteryResponse,
    summary="Get battery details",
    description="Get a battery by ID.",
)
async def get_battery_endpoint(
    battery_id: UUID,
    principal: Principal = Depends(BATTERY_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryResponse:
    """Get a battery by ID.

    Args:
        battery_id: Internal ID of the battery.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Battery details.

    Raises:
        BatteryNotFoundError: Missing, removed or out of reach (404).
    """
    return await battery_service.get_battery(
        db_session, battery_id, principal=principal
    )


@router.patch(
    "/{battery_id}",
    response_model=BatteryResponse,
    summary="Update a battery",
    description="Update the details or the status of a battery. Only the provided "
    "fields are updated.",
)
async def update_battery_endpoint(
    battery_id: UUID,
    battery_update_request: BatteryUpdateRequest,
    principal: Principal = Depends(BATTERY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryResponse:
    """Partially update a battery.

    Args:
        battery_id: Internal ID of the battery.
        battery_update_request: Request data for the update.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated battery.

    Raises:
        BatteryNotFoundError: Missing, removed or out of reach (404).
        BatteryConflictError: The serial number is used by another battery (409).
    """
    return await battery_service.update_battery(
        db_session, battery_id, battery_update_request, principal=principal
    )


@router.delete(
    "/{battery_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a battery",
    description="Soft-delete a battery (entered by mistake or left the system). "
    "A fitted battery is taken out of its truck.",
)
async def soft_delete_battery_endpoint(
    battery_id: UUID,
    reason: str | None = Query(
        None, min_length=1, max_length=200, description="Why the battery is removed"
    ),
    principal: Principal = Depends(BATTERY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Soft-delete a battery.

    Args:
        battery_id: Internal ID of the battery.
        reason: Why the battery is removed.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The confirmation body.

    Raises:
        BatteryNotFoundError: Missing, removed or out of reach (404).
    """
    await battery_service.soft_delete_battery(
        db_session, battery_id, principal=principal, reason=reason
    )
    return {"message": BATTERY_DELETED_MESSAGE}


@router.post(
    "/{battery_id}/installation",
    response_model=BatteryResponse,
    summary="Fit a battery to a truck",
    description="One battery per truck: the truck must hold no other pack and "
    "the battery must be ACTIVE and not fitted elsewhere.",
)
async def install_battery_endpoint(
    battery_id: UUID,
    battery_install_request: BatteryInstallRequest,
    principal: Principal = Depends(BATTERY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryResponse:
    """Fit a battery to a truck.

    Args:
        battery_id: Internal ID of the battery.
        battery_install_request: The truck and the fitting time.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The fitted battery.

    Raises:
        BatteryNotFoundError: The battery is missing (404).
        BatteryVehicleNotFoundError: The truck does not exist (404).
        BatteryConflictError: Fitted elsewhere, inactive, or the truck is taken (409).
        BatteryDateInvalidError: The fitting time is in the future (400).
    """
    return await battery_service.install_battery(
        db_session, battery_id, battery_install_request, principal=principal
    )


@router.delete(
    "/{battery_id}/installation",
    response_model=BatteryResponse,
    summary="Take a battery out of its truck",
    description="The battery goes back to stock; the installation period ends.",
)
async def remove_battery_from_vehicle_endpoint(
    battery_id: UUID,
    reason: str | None = Query(
        None, min_length=1, max_length=200, description="Why it is removed"
    ),
    principal: Principal = Depends(BATTERY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryResponse:
    """Take a battery out of its truck.

    Args:
        battery_id: Internal ID of the battery.
        reason: Why it is removed.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The battery, now in stock.

    Raises:
        BatteryNotFoundError: The battery is missing (404).
        BatteryConflictError: The battery is not fitted (409).
    """
    return await battery_service.remove_battery_from_vehicle(
        db_session, battery_id, principal=principal, reason=reason
    )


@router.get(
    "/{battery_id}/installation-periods",
    response_model=BatteryInstallationPeriodListResponse,
    summary="Get the trucks a battery has been fitted to",
    description="Every stay of the battery in a truck, read from the view "
    "battery_installation_periods.",
)
async def list_battery_installation_periods_endpoint(
    battery_id: UUID,
    principal: Principal = Depends(BATTERY_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryInstallationPeriodListResponse:
    """List the installation periods of a battery.

    Args:
        battery_id: Internal ID of the battery.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The periods, oldest first.

    Raises:
        BatteryNotFoundError: Missing, removed or out of reach (404).
    """
    return await battery_service.list_battery_installation_periods(
        db_session, battery_id, principal=principal
    )


@router.post(
    "/{battery_id}/transfer-ownership",
    response_model=BatteryResponse,
    summary="Hand a battery to another organization",
    description="Change the owner of the pack alone; a fitted pack stays in its "
    "truck. (A pack moves with its truck in the truck's own transfer.)",
)
async def transfer_battery_ownership_endpoint(
    battery_id: UUID,
    battery_ownership_transfer_request: BatteryOwnershipTransferRequest,
    principal: Principal = Depends(BATTERY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryResponse:
    """Hand a battery to another organization.

    Args:
        battery_id: Internal ID of the battery.
        battery_ownership_transfer_request: New owner, date and reason.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The battery with its new owner.

    Raises:
        BatteryNotFoundError: The battery is missing (404).
        OrganizationNotFoundError: The new owner does not exist (404).
        BatteryConflictError: The organization already owns it (409).
        BatteryDateInvalidError: The date breaks a rule (400).
    """
    return await battery_service.transfer_battery_ownership(
        db_session,
        battery_id,
        battery_ownership_transfer_request,
        principal=principal,
    )


@battery_models_router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=BatteryModelResponse,
    summary="Add a battery type to the catalog",
    description="Maker and model name are unique among models still offered.",
)
async def create_battery_model_endpoint(
    battery_model_create_request: BatteryModelCreateRequest,
    principal: Principal = Depends(BATTERY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryModelResponse:
    """Add a battery type to the catalog.

    Args:
        battery_model_create_request: Request data for the new model.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The created battery model.

    Raises:
        BatteryModelConflictError: The maker and model name exist (409).
    """
    return await battery_service.create_battery_model(
        db_session, battery_model_create_request
    )


@battery_models_router.get(
    "/",
    response_model=BatteryModelListResponse,
    summary="Get the battery type catalog",
    description="Battery models with pagination, filterable by name and chemistry.",
)
async def list_battery_models_endpoint(
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
        description="Maker or model-name fragment",
    ),
    chemistry: BatteryChemistry | None = Query(None, description="Cell chemistry"),
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryModelListResponse:
    """Get a paginated list of battery models.

    Args:
        page: Page number.
        page_size: Number of records per page.
        search: Maker or model-name fragment (query parameter ``q``).
        chemistry: Cell chemistry filter.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of battery models.
    """
    return await battery_service.list_battery_models(
        db_session,
        page=page,
        page_size=page_size,
        search=search,
        chemistry=chemistry.value if chemistry is not None else None,
    )


@battery_models_router.get(
    "/{battery_model_id}",
    response_model=BatteryModelResponse,
    summary="Get a battery model",
    description="Get one battery model by ID.",
)
async def get_battery_model_endpoint(
    battery_model_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryModelResponse:
    """Get a battery model by ID.

    Args:
        battery_model_id: Internal ID of the battery model.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Battery model details.

    Raises:
        BatteryModelNotFoundError: Missing or removed (404).
    """
    return await battery_service.get_battery_model(db_session, battery_model_id)


@battery_models_router.patch(
    "/{battery_model_id}",
    response_model=BatteryModelResponse,
    summary="Update a battery model",
    description="Correct or complete a model's figures. Only the provided fields "
    "are updated.",
)
async def update_battery_model_endpoint(
    battery_model_id: UUID,
    battery_model_update_request: BatteryModelUpdateRequest,
    principal: Principal = Depends(BATTERY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BatteryModelResponse:
    """Partially update a battery model.

    Args:
        battery_model_id: Internal ID of the battery model.
        battery_model_update_request: Fields to change.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated battery model.

    Raises:
        BatteryModelNotFoundError: Missing or removed (404).
        BatteryModelConflictError: The maker and name exist already (409).
    """
    return await battery_service.update_battery_model(
        db_session,
        battery_model_id,
        battery_model_update_request,
        principal=principal,
    )


@battery_models_router.delete(
    "/{battery_model_id}",
    status_code=status.HTTP_200_OK,
    summary="Remove a battery model from the catalog",
    description="Soft-delete a model: batteries that already use it keep it, new "
    "batteries cannot choose it.",
)
async def soft_delete_battery_model_endpoint(
    battery_model_id: UUID,
    principal: Principal = Depends(BATTERY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Remove a battery model from the catalog.

    Args:
        battery_model_id: Internal ID of the battery model.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The confirmation body.

    Raises:
        BatteryModelNotFoundError: Missing or removed (404).
    """
    await battery_service.soft_delete_battery_model(
        db_session, battery_model_id, principal=principal
    )
    return {"message": BATTERY_MODEL_DELETED_MESSAGE}
