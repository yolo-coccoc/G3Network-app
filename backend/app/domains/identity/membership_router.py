"""FastAPI router for single memberships: accept, leave, lock, remove, roles.

Mounted at ``/api/v1/memberships``. Listing and inviting members is under
``/organizations/{organization_id}/members`` (`organization_router.py`).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.member_service as member_service
from app.domains.identity.dependencies import (
    get_current_principal,
    get_session_identity,
)
from app.domains.identity.schemas import (
    MemberResponse,
    MembershipReasonRequest,
    RoleAssignmentResponse,
    RoleGrantRequest,
)
from app.domains.identity.types import Principal, SessionIdentity, UserRole
from app.libs.db.session import get_db

router = APIRouter(tags=["identity-memberships"])


@router.post(
    "/{membership_id}/accept",
    response_model=MemberResponse,
    summary="Accept an invitation from the app",
)
async def accept_membership_endpoint(
    membership_id: UUID,
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MemberResponse:
    """Accept the caller's own pending invitation.

    Args:
        membership_id: The caller's pending membership.
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The now active membership.
    """
    return await member_service.accept_membership(
        db_session, session_identity, membership_id
    )


@router.post(
    "/{membership_id}/leave",
    response_model=MemberResponse,
    summary="Leave an organization",
    description="409 for the ORG_ADMIN: hand the role over first.",
)
async def leave_organization_endpoint(
    membership_id: UUID,
    reason: str | None = Query(None, min_length=1, max_length=200),
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MemberResponse:
    """End the caller's own membership.

    Args:
        membership_id: The caller's membership.
        reason: Optional reason.
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The ended membership.
    """
    return await member_service.leave_organization(
        db_session, session_identity, membership_id, reason
    )


@router.post(
    "/{membership_id}/lock",
    response_model=MemberResponse,
    summary="Lock a member in this organization",
    description="409 for the ORG_ADMIN. The person's other organizations are unaffected.",
)
async def lock_member_endpoint(
    membership_id: UUID,
    lock_request: MembershipReasonRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MemberResponse:
    """Lock a membership.

    Args:
        membership_id: The membership.
        lock_request: The reason.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The locked member.
    """
    return await member_service.lock_member(
        db_session, principal, membership_id, lock_request.reason
    )


@router.post(
    "/{membership_id}/unlock",
    response_model=MemberResponse,
    summary="Unlock a member",
)
async def unlock_member_endpoint(
    membership_id: UUID,
    unlock_request: MembershipReasonRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MemberResponse:
    """Unlock a membership.

    Args:
        membership_id: The membership.
        unlock_request: The reason.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The unlocked member.
    """
    return await member_service.unlock_member(
        db_session, principal, membership_id, unlock_request.reason
    )


@router.delete(
    "/{membership_id}",
    response_model=MemberResponse,
    summary="Remove a member or cancel an invitation",
    description="Ends the membership and revokes its roles. 409 for the ORG_ADMIN.",
)
async def remove_member_endpoint(
    membership_id: UUID,
    reason: str = Query(..., min_length=1, max_length=200),
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MemberResponse:
    """Remove a member.

    Args:
        membership_id: The membership.
        reason: Why (required).
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The ended membership.
    """
    return await member_service.remove_member(
        db_session, principal, membership_id, reason
    )


@router.post(
    "/{membership_id}/roles",
    status_code=status.HTTP_201_CREATED,
    response_model=RoleAssignmentResponse,
    summary="Grant a role",
    description=(
        "409 role already held; 400 ORG_ADMIN (use the handover) or an "
        "internal-only role in a customer organization."
    ),
)
async def grant_role_endpoint(
    membership_id: UUID,
    grant_request: RoleGrantRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> RoleAssignmentResponse:
    """Grant a role to a member.

    Args:
        membership_id: The membership.
        grant_request: The role.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The new assignment.
    """
    return await member_service.grant_role(
        db_session, principal, membership_id, grant_request.role
    )


@router.delete(
    "/{membership_id}/roles/{role}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a role",
    description="409 revoking the ORG_ADMIN (use the handover) or a role not held.",
)
async def revoke_role_endpoint(
    membership_id: UUID,
    role: UserRole,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Revoke a role from a member.

    Args:
        membership_id: The membership.
        role: The role.
        principal: The caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        An empty 204 response.
    """
    await member_service.revoke_role(db_session, principal, membership_id, role)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
