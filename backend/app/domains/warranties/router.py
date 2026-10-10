"""FastAPI router for the HTTP endpoints of the warranties domain (WAR-01).

Handlers only translate HTTP to service calls. Domain exceptions are not
caught here: `app/api/main.py` maps each shared error base once
(not found -> 404, conflict -> 409, invalid input -> 400).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.warranties.service as warranty_service
from app.domains.identity.dependencies import require_roles
from app.domains.identity.types import Principal, roles_for
from app.domains.warranties.schemas import (
    WarrantyCreateRequest,
    WarrantyListResponse,
    WarrantyResponse,
    WarrantyUpdateRequest,
    WarrantyVoidRequest,
)
from app.domains.warranties.types import WarrantyStatus, WarrantyType
from app.libs.common.config import settings
from app.libs.common.reason import Reason
from app.libs.db.session import get_db

router = APIRouter(tags=["warranties"])

# WAR-01 roles read the warranties of the objects their organization owns;
# entering, editing, voiding and removing one is the work of our own warranty
# team (voiding is "by our warranty team", DM-25), so writes are internal only.
WARRANTY_READERS = require_roles(*roles_for("WAR-01"))
WARRANTY_WRITERS = require_roles(*roles_for("WAR-01"), internal_only=True)

WARRANTY_DELETED_MESSAGE = "Warranty deleted successfully"


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=WarrantyResponse,
    summary="Enter a warranty",
    description="Cover exactly one truck, battery, T-Box or charger. The limits "
    "keys must be those the object allows.",
)
async def create_warranty_endpoint(
    warranty_create_request: WarrantyCreateRequest,
    principal: Principal = Depends(WARRANTY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WarrantyResponse:
    """Enter a warranty.

    Args:
        warranty_create_request: Request data for the new warranty.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The created warranty.

    Raises:
        WarrantyObjectNotFoundError: The covered object does not exist (404).
        WarrantyPeriodInvalidError: The coverage ends before it starts (400).
        WarrantyLimitsInvalidError: A limits key is not allowed (400).
        WarrantyConflictError: An overlapping warranty of the same type exists (409).
    """
    return await warranty_service.create_warranty(
        db_session, warranty_create_request, principal=principal
    )


@router.get(
    "/",
    response_model=WarrantyListResponse,
    summary="Get the list of warranties",
    description="Warranties with pagination, filterable by covered object, "
    "owner, status, type, and 'expiring within N days'. The ones ending soonest "
    "come first.",
)
async def list_warranties_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    vehicle_id: UUID | None = Query(None, description="Warranties of a truck"),
    battery_id: UUID | None = Query(None, description="Warranties of a battery"),
    telematic_id: UUID | None = Query(None, description="Warranties of a T-Box"),
    station_id: UUID | None = Query(None, description="Warranties of a charger"),
    status_filter: WarrantyStatus | None = Query(
        None, alias="status", description="Filter by status"
    ),
    warranty_type: WarrantyType | None = Query(None, description="Filter by type"),
    expiring_within_days: int | None = Query(
        None,
        ge=0,
        le=3650,
        description="Only ACTIVE warranties ending from today up to N days ahead",
    ),
    owner_organization_id: UUID | None = Query(
        None,
        alias="organization_id",
        description="Only warranties of objects this organization owns",
    ),
    principal: Principal = Depends(WARRANTY_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WarrantyListResponse:
    """Get a paginated list of warranties.

    Args:
        page: Page number.
        page_size: Number of records per page.
        vehicle_id: Only warranties of this truck.
        battery_id: Only warranties of this battery.
        telematic_id: Only warranties of this T-Box.
        station_id: Only warranties of this charger.
        status_filter: Status (query parameter ``status``).
        warranty_type: Type filter.
        expiring_within_days: Expiring-soon filter.
        owner_organization_id: Owner filter (query parameter ``organization_id``).
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of warranties.
    """
    return await warranty_service.list_warranties(
        db_session,
        principal=principal,
        page=page,
        page_size=page_size,
        vehicle_id=vehicle_id,
        battery_id=battery_id,
        telematic_id=telematic_id,
        station_id=station_id,
        status_filter=status_filter,
        warranty_type=warranty_type,
        expiring_within_days=expiring_within_days,
        owner_organization_id=owner_organization_id,
    )


@router.get(
    "/{warranty_id}",
    response_model=WarrantyResponse,
    summary="Get warranty details",
    description="Get a warranty by ID.",
)
async def get_warranty_endpoint(
    warranty_id: UUID,
    principal: Principal = Depends(WARRANTY_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WarrantyResponse:
    """Get a warranty by ID.

    Args:
        warranty_id: Internal ID of the warranty.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Warranty details.

    Raises:
        WarrantyNotFoundError: Missing, removed or out of reach (404).
    """
    return await warranty_service.get_warranty(
        db_session, warranty_id, principal=principal
    )


@router.patch(
    "/{warranty_id}",
    response_model=WarrantyResponse,
    summary="Update a warranty",
    description="Change the terms of an ACTIVE warranty. Only the provided "
    "fields are updated; the covered object cannot change.",
)
async def update_warranty_endpoint(
    warranty_id: UUID,
    warranty_update_request: WarrantyUpdateRequest,
    principal: Principal = Depends(WARRANTY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WarrantyResponse:
    """Partially update a warranty.

    Args:
        warranty_id: Internal ID of the warranty.
        warranty_update_request: Request data for the update.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated warranty.

    Raises:
        WarrantyNotFoundError: Missing, removed or out of reach (404).
        WarrantyConflictError: Voided, or overlapping another warranty (409).
        WarrantyPeriodInvalidError: The coverage ends before it starts (400).
        WarrantyLimitsInvalidError: A limits key is not allowed (400).
    """
    return await warranty_service.update_warranty(
        db_session, warranty_id, warranty_update_request, principal=principal
    )


@router.post(
    "/{warranty_id}/void",
    response_model=WarrantyResponse,
    summary="Void a warranty",
    description="Revoke a warranty with the reason. A voided warranty is final.",
)
async def void_warranty_endpoint(
    warranty_id: UUID,
    warranty_void_request: WarrantyVoidRequest,
    principal: Principal = Depends(WARRANTY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WarrantyResponse:
    """Void a warranty.

    Args:
        warranty_id: Internal ID of the warranty.
        warranty_void_request: The reason.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The voided warranty.

    Raises:
        WarrantyNotFoundError: Missing, removed or out of reach (404).
        WarrantyConflictError: Already voided (409).
    """
    return await warranty_service.void_warranty(
        db_session,
        warranty_id,
        principal=principal,
        reason=warranty_void_request.reason,
    )


@router.delete(
    "/{warranty_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a warranty entered by mistake",
    description="Soft-delete a warranty that should never have been entered; "
    "it is VOIDED with the reason. An ordinary end of coverage is expiry or a void.",
)
async def soft_delete_warranty_endpoint(
    warranty_id: UUID,
    reason: Reason | None = Query(None, description="Why it is removed"),
    principal: Principal = Depends(WARRANTY_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Soft-delete a warranty.

    Args:
        warranty_id: Internal ID of the warranty.
        reason: Why it is removed.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The confirmation body.

    Raises:
        WarrantyNotFoundError: Missing, removed or out of reach (404).
    """
    await warranty_service.soft_delete_warranty(
        db_session, warranty_id, principal=principal, reason=reason
    )
    return {"message": WARRANTY_DELETED_MESSAGE}
