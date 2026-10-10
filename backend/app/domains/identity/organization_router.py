"""FastAPI router for organizations, their settings and their members.

Mounted at ``/api/v1/organizations``. The caller is the authenticated
`Principal`; data reach (internal sees all, others their own organization) and
the role rules are applied by the services. Domain exceptions are mapped by
`app/api/main.py`.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.member_service as member_service
import app.domains.identity.organization_service as organization_service
from app.domains.identity.dependencies import get_current_principal
from app.domains.identity.schemas import (
    AccountManagerRequest,
    AdminHandoverRequest,
    MemberInviteRequest,
    MemberListResponse,
    MemberResponse,
    OrganizationCreateRequest,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdateRequest,
    OrganizationStatusRequest,
    OrganizationUpdateRequest,
)
from app.domains.identity.types import MembershipStatus, OrganizationStatus, Principal
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["identity-organizations"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=OrganizationResponse,
    summary="Create an organization with its first ORG_ADMIN",
    description=(
        "Our sales and administrators only. The first administrator receives "
        "an invitation by SMS. 409 tax code already used; 400 tax code length "
        "does not fit the legal form."
    ),
)
async def create_organization_endpoint(
    create_request: OrganizationCreateRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OrganizationResponse:
    """Create an organization.

    Args:
        create_request: Profile and first administrator.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The new organization.
    """
    return await organization_service.create_organization(
        db_session, principal, create_request
    )


@router.get(
    "/",
    response_model=OrganizationListResponse,
    summary="List organizations",
    description="Internal callers see every organization, others only their own.",
)
async def list_organizations_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    search_text: str | None = Query(
        None, alias="q", min_length=1, max_length=100, description="Name or tax code"
    ),
    organization_status: OrganizationStatus | None = Query(None, alias="status"),
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OrganizationListResponse:
    """List organizations in the caller's data reach.

    Args:
        page: Page number.
        page_size: Rows per page.
        search_text: Name or tax-code substring.
        organization_status: Only this status.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of organizations.
    """
    return await organization_service.list_organizations(
        db_session,
        principal,
        page=page,
        page_size=page_size,
        search_text=search_text,
        status=organization_status,
    )


@router.get(
    "/{organization_id}",
    response_model=OrganizationResponse,
    summary="Get an organization",
)
async def get_organization_endpoint(
    organization_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OrganizationResponse:
    """Read one organization (404 when outside the caller's data reach).

    Args:
        organization_id: The organization.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The organization.
    """
    return await organization_service.get_organization(
        db_session, principal, organization_id
    )


@router.patch(
    "/{organization_id}",
    response_model=OrganizationResponse,
    summary="Edit an organization",
    description="A typed reason is required (422 without it).",
)
async def update_organization_endpoint(
    organization_id: UUID,
    update_request: OrganizationUpdateRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OrganizationResponse:
    """Edit an organization's profile.

    Args:
        organization_id: The organization.
        update_request: New values and the reason.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated organization.
    """
    return await organization_service.update_organization(
        db_session, principal, organization_id, update_request
    )


@router.post(
    "/{organization_id}/status",
    response_model=OrganizationResponse,
    summary="Suspend, close or reactivate an organization",
    description="Only our HEAD_ADMIN / CO_ADMIN. 409 when the status is unchanged.",
)
async def change_organization_status_endpoint(
    organization_id: UUID,
    status_request: OrganizationStatusRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OrganizationResponse:
    """Change an organization's lifecycle status.

    Args:
        organization_id: The organization.
        status_request: New status and reason.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated organization.
    """
    return await organization_service.change_organization_status(
        db_session, principal, organization_id, status_request
    )


@router.put(
    "/{organization_id}/account-manager",
    response_model=OrganizationResponse,
    summary="Assign the account manager",
)
async def assign_account_manager_endpoint(
    organization_id: UUID,
    manager_request: AccountManagerRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OrganizationResponse:
    """Assign (or clear) the organization's account manager.

    Args:
        organization_id: The organization.
        manager_request: The manager's user ID (or null) and the reason.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated organization.
    """
    return await organization_service.assign_account_manager(
        db_session, principal, organization_id, manager_request
    )


@router.get(
    "/{organization_id}/settings",
    response_model=OrganizationSettingsResponse,
    summary="Get an organization's settings",
)
async def get_organization_settings_endpoint(
    organization_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OrganizationSettingsResponse:
    """Read the settings an organization chose for itself.

    Args:
        organization_id: The organization.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The settings.
    """
    return await organization_service.get_organization_settings(
        db_session, principal, organization_id
    )


@router.patch(
    "/{organization_id}/settings",
    response_model=OrganizationSettingsResponse,
    summary="Change an organization's settings",
)
async def update_organization_settings_endpoint(
    organization_id: UUID,
    update_request: OrganizationSettingsUpdateRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OrganizationSettingsResponse:
    """Change the organization's settings (ORG_ADMIN or our administrators).

    Args:
        organization_id: The organization.
        update_request: New values and the reason.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated settings.
    """
    return await organization_service.update_organization_settings(
        db_session, principal, organization_id, update_request
    )


@router.get(
    "/{organization_id}/members",
    response_model=MemberListResponse,
    summary="List the members of an organization",
)
async def list_members_endpoint(
    organization_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    membership_status: MembershipStatus | None = Query(None, alias="status"),
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MemberListResponse:
    """List members with their roles and status.

    Args:
        organization_id: The organization.
        page: Page number.
        page_size: Rows per page.
        membership_status: Only this membership status.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of members.
    """
    return await member_service.list_members(
        db_session,
        principal,
        organization_id,
        status=membership_status,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/{organization_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=MemberResponse,
    summary="Invite a person by phone number",
    description=(
        "Creates an INVITED membership with the chosen roles and sends an SMS "
        "invitation (72 hours). 409 already a member; 400/403 role not allowed."
    ),
)
async def invite_member_endpoint(
    organization_id: UUID,
    invite_request: MemberInviteRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MemberResponse:
    """Invite a person into an organization.

    Args:
        organization_id: The organization.
        invite_request: Phone, name, e-mail and roles.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The invited member.
    """
    return await member_service.invite_member(
        db_session, principal, organization_id, invite_request
    )


@router.post(
    "/{organization_id}/members/{membership_id}/resend",
    response_model=MemberResponse,
    summary="Send an invitation again",
)
async def resend_invitation_endpoint(
    organization_id: UUID,
    membership_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MemberResponse:
    """Resend a pending invitation.

    Args:
        organization_id: The organization.
        membership_id: The pending membership.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The member.
    """
    return await member_service.resend_invitation(
        db_session, principal, organization_id, membership_id
    )


@router.post(
    "/{organization_id}/admin-handover",
    response_model=MemberResponse,
    summary="Hand the ORG_ADMIN role to another active member",
    description=(
        "One transaction: grant the new, revoke the old. 403 when the caller is "
        "not the current ORG_ADMIN (our administrator may use force when the "
        "ORG_ADMIN is gone); 409 when the target is not active."
    ),
)
async def handover_org_admin_endpoint(
    organization_id: UUID,
    handover_request: AdminHandoverRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MemberResponse:
    """Hand over the ORG_ADMIN role.

    Args:
        organization_id: The organization.
        handover_request: Target membership, reason, force flag.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The new administrator.
    """
    return await member_service.handover_org_admin(
        db_session, principal, organization_id, handover_request
    )
