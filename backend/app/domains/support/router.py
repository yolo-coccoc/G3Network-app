"""FastAPI router for the HTTP endpoints of the support domain."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.support.service as support_service
from app.domains.support.exceptions import (
    SupportCaseNotFoundError,
    SupportCaseStateError,
    SupportDriverNotFoundError,
    SupportVehicleNotFoundError,
)
from app.domains.support.schemas import (
    SupportCaseListResponse,
    SupportCaseResponse,
    SupportCaseUpdateRequest,
    SupportSosCreateRequest,
    SupportTicketCreateRequest,
)
from app.domains.support.types import SupportCaseStatus, SupportCaseType
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["support"])


@router.post(
    "/cases",
    status_code=status.HTTP_201_CREATED,
    response_model=SupportCaseResponse,
    summary="Create an in-app support ticket",
    description="Create a new support ticket (F-I1), optionally attaching vehicle/driver context.",
)
async def create_support_ticket_endpoint(
    support_ticket_create_request: SupportTicketCreateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> SupportCaseResponse:
    """Create a new support ticket.

    Args:
        support_ticket_create_request: Request data for creating the ticket.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created support ticket.
    """
    try:
        return await support_service.create_support_ticket(
            db_session, support_ticket_create_request
        )
    except (SupportVehicleNotFoundError, SupportDriverNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.post(
    "/sos",
    status_code=status.HTTP_201_CREATED,
    response_model=SupportCaseResponse,
    summary="Report an SOS/roadside incident",
    description="Create a new SOS case (F-I2) with the driver's current location.",
)
async def create_support_sos_endpoint(
    support_sos_create_request: SupportSosCreateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> SupportCaseResponse:
    """Create a new SOS case.

    Args:
        support_sos_create_request: Request data for creating the SOS case.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created SOS case.
    """
    try:
        return await support_service.create_support_sos(
            db_session, support_sos_create_request
        )
    except (SupportVehicleNotFoundError, SupportDriverNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/cases",
    response_model=SupportCaseListResponse,
    summary="Get the list of support cases",
    description="Get the list of support cases with pagination and filtering.",
)
async def list_support_cases_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    status_filter: SupportCaseStatus | None = Query(
        None, alias="status", description="Filter by status"
    ),
    case_type_filter: SupportCaseType | None = Query(
        None, alias="case_type", description="Filter by case type"
    ),
    vehicle_id: UUID | None = Query(None, description="Filter by vehicle ID"),
    db_session: AsyncSession = Depends(get_db),
) -> SupportCaseListResponse:
    """Get a paginated list of support cases.

    Args:
        page: Page number.
        page_size: Number of records per page.
        status_filter: Status filter, if any.
        case_type_filter: Case type filter, if any.
        vehicle_id: Vehicle ID filter, if any.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of support cases.
    """
    return await support_service.list_support_cases(
        db_session,
        page,
        page_size,
        status_filter,
        case_type_filter,
        vehicle_id,
    )


@router.get(
    "/cases/{case_id}",
    response_model=SupportCaseResponse,
    summary="Get support case details",
    description="Get detailed information about a support case by ID.",
)
async def get_support_case_endpoint(
    case_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> SupportCaseResponse:
    """Get the details of a support case by ID.

    Args:
        case_id: Internal ID of the support case.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Support case details.
    """
    try:
        return await support_service.get_support_case(db_session, case_id)
    except SupportCaseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/cases/{case_id}",
    response_model=SupportCaseResponse,
    summary="Update a support case",
    description="Update a support case's status/category/subject/description. Refused once the case is CLOSED or CANCELLED.",
)
async def update_support_case_endpoint(
    case_id: UUID,
    support_case_update_request: SupportCaseUpdateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> SupportCaseResponse:
    """Partially update a support case.

    Args:
        case_id: Internal ID of the support case.
        support_case_update_request: Request data for updating the case.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated support case.
    """
    try:
        return await support_service.update_support_case(
            db_session, case_id, support_case_update_request
        )
    except SupportCaseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except SupportCaseStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


@router.delete(
    "/cases/{case_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a support case",
    description="Soft-delete a support case.",
)
async def soft_delete_support_case_endpoint(
    case_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Soft-delete a support case.

    Args:
        case_id: Internal ID of the support case.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Success message.
    """
    try:
        return await support_service.soft_delete_support_case(db_session, case_id)
    except SupportCaseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
