"""FastAPI routers for legal documents, consent and the access audit log.

Three routers (mounted at ``/api/v1/legal-documents``, ``/api/v1/consents``
and ``/api/v1/access-audit-logs``): publishing and reading the legal texts
(ACC-17), recording acceptance, and searching the personal-data audit log by
our staff and each ORG_ADMIN (ACC-18).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.audit_service as audit_service
import app.domains.identity.legal_service as legal_service
from app.domains.identity.dependencies import get_client_context, get_current_principal
from app.domains.identity.schemas import (
    AccessAuditLogListResponse,
    ConsentCreateRequest,
    ConsentResponse,
    LegalDocumentCreateRequest,
    LegalDocumentResponse,
    LegalDocumentSummaryResponse,
)
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    LegalDocumentPurpose,
    Principal,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

legal_documents_router = APIRouter(tags=["identity-legal"])
consents_router = APIRouter(tags=["identity-legal"])
audit_router = APIRouter(tags=["identity-audit"])


@legal_documents_router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=LegalDocumentResponse,
    summary="Publish a legal document version",
    description="Our HEAD_ADMIN / CO_ADMIN only; a version is never edited. 409 duplicate.",
)
async def publish_legal_document_endpoint(
    create_request: LegalDocumentCreateRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> LegalDocumentResponse:
    """Publish a final legal text.

    Args:
        create_request: Purpose, version, title and text.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The published document.
    """
    return await legal_service.publish_legal_document(
        db_session, principal, create_request
    )


@legal_documents_router.get(
    "/",
    response_model=list[LegalDocumentSummaryResponse],
    summary="List legal document versions (without their text)",
)
async def list_legal_documents_endpoint(
    purpose: LegalDocumentPurpose | None = Query(None),
    _principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> list[LegalDocumentSummaryResponse]:
    """List published versions.

    Args:
        purpose: Only this purpose.
        _principal: The authenticated caller (authentication only).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The documents, newest first.
    """
    return await legal_service.list_legal_documents(db_session, purpose)


@legal_documents_router.get(
    "/current",
    response_model=list[LegalDocumentResponse],
    summary="The current version of every legal text (public)",
)
async def list_current_legal_documents_endpoint(
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> list[LegalDocumentResponse]:
    """Return the newest version of each purpose, readable before sign-up.

    Args:
        db_session: Database session owned by the HTTP boundary.

    Returns:
        One document per published purpose.
    """
    return await legal_service.list_current_legal_documents(db_session)


@legal_documents_router.get(
    "/pending",
    response_model=list[LegalDocumentResponse],
    summary="Current texts the caller still has to accept",
)
async def list_pending_legal_documents_endpoint(
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> list[LegalDocumentResponse]:
    """List the applicable current texts not yet accepted.

    Args:
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The pending documents.
    """
    return await legal_service.list_pending_legal_documents(db_session, principal)


@legal_documents_router.get(
    "/{legal_document_id}",
    response_model=LegalDocumentResponse,
    summary="Get a legal document version with its text",
)
async def get_legal_document_endpoint(
    legal_document_id: UUID,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> LegalDocumentResponse:
    """Read one version.

    Args:
        legal_document_id: The document.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The document.
    """
    return await legal_service.get_legal_document(db_session, legal_document_id)


@consents_router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=ConsentResponse,
    summary="Accept the current version of a legal text",
    description="Idempotent. 409 when a newer version is in force.",
)
async def accept_legal_document_endpoint(
    consent_request: ConsentCreateRequest,
    device_label: str | None = Query(None, max_length=100),
    principal: Principal = Depends(get_current_principal),
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> ConsentResponse:
    """Record an acceptance with its proof (IP address and device).

    Args:
        consent_request: The document and the level of acceptance.
        device_label: Readable device name to keep as proof.
        principal: The caller.
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The consent record.
    """
    return await legal_service.accept_legal_document(
        db_session, principal, consent_request, device_label, client_context
    )


@consents_router.get(
    "/me",
    response_model=list[ConsentResponse],
    summary="What I accepted",
)
async def list_my_consents_endpoint(
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> list[ConsentResponse]:
    """List the caller's consent records.

    Args:
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The records, newest first.
    """
    return await legal_service.list_my_consents(db_session, principal)


@audit_router.get(
    "/",
    response_model=AccessAuditLogListResponse,
    summary="Search the access audit log",
    description=(
        "Our HEAD_ADMIN / CO_ADMIN search everything; an ORG_ADMIN only their "
        "own organization. 400 when the range is longer than the limit."
    ),
)
async def search_audit_logs_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    user_id: UUID | None = Query(None),
    organization_id: UUID | None = Query(None),
    action: AccessAuditAction | None = Query(None),
    resource_type: str | None = Query(None, min_length=1, max_length=50),
    occurred_from: datetime | None = Query(None, alias="from"),
    occurred_to: datetime | None = Query(None, alias="to"),
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> AccessAuditLogListResponse:
    """Search who viewed or exported what, logins, failed logins and lockouts.

    Args:
        page: Page number.
        page_size: Rows per page.
        user_id: Only entries about or by this user.
        organization_id: Only this organization (internal administrators).
        action: Only this action.
        resource_type: Only this resource type.
        occurred_from: Start of the range (default 30 days before the end).
        occurred_to: End of the range (default now).
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of entries, newest first.
    """
    return await audit_service.search_audit_logs(
        db_session,
        principal,
        user_id=user_id,
        organization_id=organization_id,
        action=action,
        resource_type=resource_type,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        page=page,
        page_size=page_size,
    )
