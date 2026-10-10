"""Members, invitations and roles of an organization (ACC-09, 12, 13).

Business rules for who belongs to an organization and what they may do there:
inviting a person by phone number, accepting, locking, removing, leaving,
granting and revoking roles, and the ORG_ADMIN handover (ID-33). Authorization
is checked here with the caller's `Principal`; an organization the caller's
data reach excludes answers as not found (404) so IDs are not guessable.

Limitations: the DRIVER role is granted without checking for a driver profile
(the `drivers` domain depends on identity, not the reverse). Ending or locking
a membership calls the hooks registered with `register_membership_end_hook`
(the drivers domain closes the person's driver profile and open driving
session that way, DR-10; wired in ``app/api/membership_end_hooks.py``).
"""

import logging
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.account_service as account_service
import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import (
    AccessDeniedError,
    AdminHandoverInvalidError,
    MembershipConflictError,
    MembershipNotFoundError,
    OrgAdminProtectedError,
    OrganizationInactiveError,
    OrganizationNotFoundError,
    RoleConflictError,
    RoleNotAllowedError,
    UserConflictError,
    UserNotFoundError,
)
from app.domains.identity.models import MembershipModel, OrganizationModel, UserModel
from app.domains.identity.providers import get_sms_sender
from app.domains.identity.schemas import (
    AdminHandoverRequest,
    MemberInviteRequest,
    MemberListResponse,
    MemberResponse,
    RoleAssignmentResponse,
)
from app.domains.identity.types import (
    INTERNAL_ONLY_ROLES,
    MembershipEndHook,
    MembershipEndKind,
    MembershipStatus,
    OneTimeCodePurpose,
    OrganizationStatus,
    Principal,
    SessionIdentity,
    UserRole,
    UserStatus,
)
from app.libs.common.clock import utc_now
from app.libs.common.pagination import normalize_page_window
from app.libs.db.history import set_change_context

logger = logging.getLogger(__name__)

# Callbacks run, in the caller's transaction, after a membership ended or was
# locked (DR-10). Registered once at application start-up through
# `register_membership_end_hook`; identity itself imports no other domain.
_membership_end_hooks: list[MembershipEndHook] = []


def register_membership_end_hook(hook: MembershipEndHook) -> None:
    """Register a callback for the end or lock of a membership.

    Args:
        hook: Async callable taking the database session and the keyword
            arguments ``membership_id``, ``kind`` (`MembershipEndKind`),
            ``acting_user_id`` and ``reason``. It runs in the same
            transaction; an exception rolls the whole action back.

    Side Effects:
        Appends to a module-level list; registering the same hook twice is
        ignored.
    """
    if hook not in _membership_end_hooks:
        _membership_end_hooks.append(hook)


async def _run_membership_end_hooks(
    db_session: AsyncSession,
    *,
    membership_id: UUID,
    kind: MembershipEndKind,
    acting_user_id: UUID,
    reason: str,
) -> None:
    """Run every registered membership-end hook, in registration order.

    Args:
        db_session: Session owned by the entry boundary.
        membership_id: The membership that ended or was locked.
        kind: Whether it ended or was locked.
        acting_user_id: Who did it.
        reason: Why.
    """
    for hook in _membership_end_hooks:
        await hook(
            db_session,
            membership_id=membership_id,
            kind=kind,
            acting_user_id=acting_user_id,
            reason=reason,
        )


async def _require_user(db_session: AsyncSession, user_id: UUID) -> UserModel:
    """Load a user that a foreign key guarantees to exist.

    Raises:
        UserNotFoundError: If the row is missing (cannot happen under the
            foreign key; keeps the type non-null without an assert).
    """
    user_record = await identity_repository.get_user(db_session, user_id)
    if user_record is None:
        raise UserNotFoundError("The user does not exist")
    return user_record


async def _require_organization(
    db_session: AsyncSession, organization_id: UUID
) -> OrganizationModel:
    """Load an organization that a foreign key guarantees to exist.

    Raises:
        OrganizationNotFoundError: If the row is missing (cannot happen under
            the foreign key).
    """
    organization_record = await identity_repository.get_organization(
        db_session, organization_id
    )
    if organization_record is None:
        raise OrganizationNotFoundError("The organization does not exist")
    return organization_record


async def _load_reachable_organization(
    db_session: AsyncSession, principal: Principal, organization_id: UUID
) -> OrganizationModel:
    """Load an organization the caller's data reach includes.

    Raises:
        OrganizationNotFoundError: If it does not exist or is out of reach.
    """
    organization_record = await identity_repository.get_organization(
        db_session, organization_id
    )
    if organization_record is None or not principal.can_access_organization(
        organization_id
    ):
        raise OrganizationNotFoundError("The organization does not exist")
    return organization_record


async def _load_reachable_membership(
    db_session: AsyncSession, principal: Principal, membership_id: UUID
) -> MembershipModel:
    """Load a membership of an organization the caller can reach.

    Raises:
        MembershipNotFoundError: If it does not exist or is out of reach.
    """
    membership_record = await identity_repository.get_membership(
        db_session, membership_id
    )
    if membership_record is None or not principal.can_access_organization(
        membership_record.organization_id
    ):
        raise MembershipNotFoundError("The membership does not exist")
    return membership_record


def _is_internal_admin(principal: Principal) -> bool:
    """Tell whether the caller is one of our HEAD_ADMIN / CO_ADMIN."""
    return principal.is_internal and principal.has_any_role(
        UserRole.HEAD_ADMIN, UserRole.CO_ADMIN
    )


def _require_member_manager(principal: Principal, organization_id: UUID) -> None:
    """Allow our administrators anywhere, and an ORG_ADMIN in their own organization.

    Raises:
        AccessDeniedError: For anyone else.
    """
    if _is_internal_admin(principal):
        return
    if (
        principal.has_any_role(UserRole.ORG_ADMIN)
        and principal.organization_id == organization_id
    ):
        return
    raise AccessDeniedError("Only an organization administrator may manage members")


def _check_role_grantable(
    principal: Principal, organization_record: OrganizationModel, role: UserRole
) -> None:
    """Apply the role rules to a grant (ID-12, ID-33, ID-40).

    Raises:
        RoleNotAllowedError: ORG_ADMIN (use handover), or HEAD_ADMIN /
            CO_ADMIN outside an internal organization.
        AccessDeniedError: A caller other than HEAD_ADMIN grants HEAD_ADMIN or
            CO_ADMIN (a CO_ADMIN cannot manage administrators).
    """
    if role is UserRole.ORG_ADMIN:
        raise RoleNotAllowedError("ORG_ADMIN is handed over, not granted")
    if role in INTERNAL_ONLY_ROLES:
        if not organization_record.is_internal:
            raise RoleNotAllowedError(
                f"{role.value} exists only in internal organizations"
            )
        if not principal.has_any_role(UserRole.HEAD_ADMIN):
            raise AccessDeniedError("Only a HEAD_ADMIN may grant an administrator role")


async def _to_member_response(
    db_session: AsyncSession, membership_record: MembershipModel, user_record: UserModel
) -> MemberResponse:
    """Build a member response with the roles the membership holds now."""
    role_values = await identity_repository.list_active_roles_by_membership(
        db_session, membership_record.membership_id
    )
    return MemberResponse(
        membership_id=membership_record.membership_id,
        organization_id=membership_record.organization_id,
        user_id=user_record.user_id,
        full_name=user_record.full_name,
        phone_number=user_record.phone_number,
        email=user_record.email,
        user_status=user_record.status,
        status=membership_record.status,
        status_reason=membership_record.status_reason,
        roles=[UserRole(role_value) for role_value in role_values],
        joined_at=membership_record.joined_at,
        left_at=membership_record.left_at,
        created_at=membership_record.created_at,
    )


async def _notify_invited_person(
    db_session: AsyncSession,
    *,
    user_record: UserModel,
    organization_record: OrganizationModel,
    issued_by: UUID,
) -> None:
    """Tell an invited person how to join: a code for a new account, else a notice.

    Args:
        db_session: Session owned by the entry boundary.
        user_record: The invited person.
        organization_record: The organization that invites.
        issued_by: The user who invites.

    Raises:
        OneTimeCodeRateLimitError: A code was sent to this number too recently.
    """
    if user_record.status == UserStatus.INVITED.value:
        await account_service.issue_one_time_code(
            db_session,
            purpose=OneTimeCodePurpose.INVITE,
            phone_number=user_record.phone_number,
            user_id=user_record.user_id,
            issued_by=issued_by,
        )
        return
    await get_sms_sender().send_sms(
        user_record.phone_number,
        f"G3 Network: ban duoc moi vao {organization_record.display_name}. "
        "Mo ung dung de chap nhan loi moi.",
    )


async def add_invited_member(
    db_session: AsyncSession,
    *,
    invited_by: UUID,
    organization_record: OrganizationModel,
    phone_number: str,
    full_name: str,
    email: str | None,
    roles: list[UserRole],
) -> MembershipModel:
    """Create an INVITED membership (and the account, if the phone is new).

    Does not check who may invite or which roles are allowed; callers do that
    (`invite_member`, and the organization creation that appoints the first
    ORG_ADMIN).

    Args:
        db_session: Session owned by the entry boundary.
        invited_by: The user who invites.
        organization_record: The organization the person joins.
        phone_number: The person's phone number, E.164.
        full_name: Name used when the account is new.
        email: E-mail used when the account is new.
        roles: Roles granted with the invitation (effective once accepted).

    Returns:
        The new membership.

    Raises:
        UserConflictError: The e-mail belongs to another account, or the
            person's account is locked.
        MembershipConflictError: The person is already a member or invited.
        OneTimeCodeRateLimitError: A code was sent to this number too recently.

    Side Effects:
        Inserts the user, membership and role rows and sends the invitation.
    """
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, phone_number
    )
    if user_record is None:
        if (
            email is not None
            and await identity_repository.find_live_user_by_email(db_session, email)
            is not None
        ):
            raise UserConflictError("This e-mail address already has an account")
        user_record = await identity_repository.insert_user(
            db_session,
            {
                "phone_number": phone_number,
                "email": email,
                "full_name": full_name,
                "status": UserStatus.INVITED.value,
                "created_by": invited_by,
            },
        )
    elif user_record.status == UserStatus.LOCKED.value:
        raise UserConflictError("This person's account is locked")
    existing = await identity_repository.find_live_membership(
        db_session,
        organization_id=organization_record.organization_id,
        user_id=user_record.user_id,
    )
    if existing is not None:
        raise MembershipConflictError("This person is already a member or invited")
    membership_record = await identity_repository.insert_membership(
        db_session,
        {
            "organization_id": organization_record.organization_id,
            "user_id": user_record.user_id,
            "status": MembershipStatus.INVITED.value,
            "created_by": invited_by,
        },
    )
    for role in roles:
        await identity_repository.insert_role_assignment(
            db_session,
            membership_record=membership_record,
            role=role.value,
            granted_by=invited_by,
        )
    await _notify_invited_person(
        db_session,
        user_record=user_record,
        organization_record=organization_record,
        issued_by=invited_by,
    )
    return membership_record


async def invite_member(
    db_session: AsyncSession,
    principal: Principal,
    organization_id: UUID,
    invite_request: MemberInviteRequest,
) -> MemberResponse:
    """Invite a person into an organization (ACC-09, ACC-10).

    An ORG_ADMIN or one of our administrators may invite with any grantable
    role; a FLEET_MANAGER may only register drivers (ID-15).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        organization_id: The organization to join.
        invite_request: Phone, name, e-mail and roles.

    Returns:
        The invited member.

    Raises:
        OrganizationNotFoundError: The organization is out of reach.
        AccessDeniedError: The caller may not invite, or not these roles.
        RoleNotAllowedError: A role cannot be held in this organization.
        MembershipConflictError: The person is already a member.
        UserConflictError: The e-mail or account conflicts.
        OneTimeCodeRateLimitError: A code was sent too recently.
    """
    organization_record = await _load_reachable_organization(
        db_session, principal, organization_id
    )
    roles = list(dict.fromkeys(invite_request.roles))
    is_driver_registration = (
        principal.has_any_role(UserRole.FLEET_MANAGER)
        and principal.organization_id == organization_id
        and set(roles) <= {UserRole.DRIVER}
    )
    if not is_driver_registration:
        _require_member_manager(principal, organization_id)
    for role in roles:
        _check_role_grantable(principal, organization_record, role)
    membership_record = await add_invited_member(
        db_session,
        invited_by=principal.user_id,
        organization_record=organization_record,
        phone_number=invite_request.phone_number,
        full_name=invite_request.full_name,
        email=invite_request.email,
        roles=roles,
    )
    user_record = await _require_user(db_session, membership_record.user_id)
    return await _to_member_response(db_session, membership_record, user_record)


async def resend_invitation(
    db_session: AsyncSession,
    principal: Principal,
    organization_id: UUID,
    membership_id: UUID,
) -> MemberResponse:
    """Send the invitation again (new code, new 72-hour window).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        organization_id: The organization.
        membership_id: The pending membership.

    Returns:
        The member.

    Raises:
        MembershipNotFoundError: The membership is not in this organization.
        AccessDeniedError: The caller may not manage members.
        MembershipConflictError: The invitation was already accepted or ended.
        OneTimeCodeRateLimitError: A code was sent too recently.
    """
    organization_record = await _load_reachable_organization(
        db_session, principal, organization_id
    )
    _require_member_manager(principal, organization_id)
    membership_record = await identity_repository.get_membership(
        db_session, membership_id
    )
    if (
        membership_record is None
        or membership_record.organization_id != organization_id
    ):
        raise MembershipNotFoundError("The membership does not exist")
    if (
        membership_record.status != MembershipStatus.INVITED.value
        or membership_record.left_at is not None
    ):
        raise MembershipConflictError("The invitation was already accepted or ended")
    user_record = await _require_user(db_session, membership_record.user_id)
    await _notify_invited_person(
        db_session,
        user_record=user_record,
        organization_record=organization_record,
        issued_by=principal.user_id,
    )
    return await _to_member_response(db_session, membership_record, user_record)


async def list_members(
    db_session: AsyncSession,
    principal: Principal,
    organization_id: UUID,
    *,
    status: MembershipStatus | None,
    page: int,
    page_size: int,
) -> MemberListResponse:
    """List the current members of an organization with their roles (ACC-12).

    Args:
        db_session: Current database session.
        principal: The caller.
        organization_id: The organization.
        status: Only this membership status.
        page: Page number from 1.
        page_size: Rows per page.

    Returns:
        A page of members, oldest first.

    Raises:
        OrganizationNotFoundError: The organization is out of reach.
        AccessDeniedError: The caller is neither a manager nor a FLEET_MANAGER
            of that organization.
    """
    await _load_reachable_organization(db_session, principal, organization_id)
    if not (
        principal.has_any_role(UserRole.FLEET_MANAGER)
        and principal.organization_id == organization_id
    ):
        _require_member_manager(principal, organization_id)
    page_window = normalize_page_window(page, page_size)
    status_value = None if status is None else status.value
    pairs = await identity_repository.list_members(
        db_session,
        organization_id=organization_id,
        status=status_value,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await identity_repository.count_members(
        db_session, organization_id=organization_id, status=status_value
    )
    return MemberListResponse(
        items=[
            await _to_member_response(db_session, membership_record, user_record)
            for membership_record, user_record in pairs
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def _assert_membership_can_end(
    db_session: AsyncSession, membership_record: MembershipModel
) -> None:
    """Refuse to lock or end the ORG_ADMIN or the last HEAD_ADMIN.

    Raises:
        OrgAdminProtectedError: The membership is its organization's ORG_ADMIN.
        RoleConflictError: The membership is the only HEAD_ADMIN.
    """
    if (
        await identity_repository.find_active_role_assignment(
            db_session,
            membership_id=membership_record.membership_id,
            role=UserRole.ORG_ADMIN.value,
        )
        is not None
    ):
        raise OrgAdminProtectedError("Hand over the ORG_ADMIN role first")
    if (
        await identity_repository.find_active_role_assignment(
            db_session,
            membership_id=membership_record.membership_id,
            role=UserRole.HEAD_ADMIN.value,
        )
        is not None
        and await identity_repository.count_active_role_holders(
            db_session, UserRole.HEAD_ADMIN.value
        )
        <= 1
    ):
        raise RoleConflictError("The last HEAD_ADMIN cannot be removed")


async def _end_membership(
    db_session: AsyncSession,
    membership_record: MembershipModel,
    *,
    acting_user_id: UUID,
    reason: str,
) -> MemberResponse:
    """End a membership: close it, revoke its roles, unset sessions' organization.

    Args:
        db_session: Session owned by the entry boundary.
        membership_record: The membership to end.
        acting_user_id: Who ends it (the person themself when leaving).
        reason: Why (kept in `status_reason` and the history).

    Returns:
        The ended membership as a member response.
    """
    if membership_record.left_at is not None:
        raise MembershipConflictError("The membership already ended")
    await _assert_membership_can_end(db_session, membership_record)
    now = utc_now()
    await set_change_context(
        db_session, changed_by=acting_user_id, change_reason=reason
    )
    await identity_repository.apply_membership_values(
        db_session,
        membership_record,
        {"left_at": now, "status_reason": reason},
    )
    await identity_repository.revoke_all_roles_of_membership(
        db_session,
        membership_record.membership_id,
        revoked_at=now,
        revoked_by=acting_user_id,
    )
    await identity_repository.clear_session_organization(
        db_session,
        user_id=membership_record.user_id,
        organization_id=membership_record.organization_id,
    )
    await _run_membership_end_hooks(
        db_session,
        membership_id=membership_record.membership_id,
        kind=MembershipEndKind.ENDED,
        acting_user_id=acting_user_id,
        reason=reason,
    )
    user_record = await _require_user(db_session, membership_record.user_id)
    return await _to_member_response(db_session, membership_record, user_record)


async def remove_member(
    db_session: AsyncSession,
    principal: Principal,
    membership_id: UUID,
    reason: str,
) -> MemberResponse:
    """Remove a member, or cancel a pending invitation (ACC-12, DR-10).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        membership_id: The membership to end.
        reason: Why.

    Returns:
        The ended membership.

    Raises:
        MembershipNotFoundError: The membership is out of reach.
        AccessDeniedError: The caller may not manage members.
        OrgAdminProtectedError: The person is the ORG_ADMIN.
        MembershipConflictError: The membership already ended.
    """
    membership_record = await _load_reachable_membership(
        db_session, principal, membership_id
    )
    _require_member_manager(principal, membership_record.organization_id)
    return await _end_membership(
        db_session,
        membership_record,
        acting_user_id=principal.user_id,
        reason=reason,
    )


async def leave_organization(
    db_session: AsyncSession,
    session_identity: SessionIdentity,
    membership_id: UUID,
    reason: str | None,
) -> MemberResponse:
    """Let a person leave an organization themself (ACC-12).

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        membership_id: The person's own membership.
        reason: Why, or `None` for a default text.

    Returns:
        The ended membership.

    Raises:
        MembershipNotFoundError: The membership is not the caller's.
        OrgAdminProtectedError: The person is the ORG_ADMIN (hand over first).
        MembershipConflictError: The membership already ended.
    """
    membership_record = await identity_repository.get_membership(
        db_session, membership_id
    )
    if (
        membership_record is None
        or membership_record.user_id != session_identity.user_id
    ):
        raise MembershipNotFoundError("The membership does not exist")
    return await _end_membership(
        db_session,
        membership_record,
        acting_user_id=session_identity.user_id,
        reason=reason or "Left the organization",
    )


async def accept_membership(
    db_session: AsyncSession, session_identity: SessionIdentity, membership_id: UUID
) -> MemberResponse:
    """Accept an invitation to another organization from the app (ID-26).

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        membership_id: The person's own pending membership.

    Returns:
        The now active membership.

    Raises:
        MembershipNotFoundError: The membership is not the caller's.
        MembershipConflictError: It is not a pending invitation.
        OrganizationInactiveError: The organization is suspended or closed.
    """
    membership_record = await identity_repository.get_membership(
        db_session, membership_id
    )
    if (
        membership_record is None
        or membership_record.user_id != session_identity.user_id
    ):
        raise MembershipNotFoundError("The membership does not exist")
    if (
        membership_record.status != MembershipStatus.INVITED.value
        or membership_record.left_at is not None
    ):
        raise MembershipConflictError("This is not a pending invitation")
    organization_record = await identity_repository.get_organization(
        db_session, membership_record.organization_id
    )
    if (
        organization_record is None
        or organization_record.status != OrganizationStatus.ACTIVE.value
        or organization_record.deleted_at is not None
    ):
        raise OrganizationInactiveError("This organization is suspended or closed")
    await set_change_context(
        db_session,
        changed_by=session_identity.user_id,
        change_reason="Invitation accepted",
    )
    await identity_repository.apply_membership_values(
        db_session,
        membership_record,
        {"status": MembershipStatus.ACTIVE.value, "joined_at": utc_now()},
    )
    user_record = await _require_user(db_session, membership_record.user_id)
    return await _to_member_response(db_session, membership_record, user_record)


async def lock_member(
    db_session: AsyncSession,
    principal: Principal,
    membership_id: UUID,
    reason: str,
) -> MemberResponse:
    """Block a person in one organization without touching their other ones.

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        membership_id: The membership to lock.
        reason: Why.

    Returns:
        The locked member.

    Raises:
        MembershipNotFoundError: The membership is out of reach.
        AccessDeniedError: The caller may not manage members.
        OrgAdminProtectedError: The person is the ORG_ADMIN.
        MembershipConflictError: Locking oneself, or not an active membership.
    """
    membership_record = await _load_reachable_membership(
        db_session, principal, membership_id
    )
    _require_member_manager(principal, membership_record.organization_id)
    if membership_record.user_id == principal.user_id:
        raise MembershipConflictError("You cannot lock yourself")
    if (
        membership_record.status != MembershipStatus.ACTIVE.value
        or membership_record.left_at is not None
    ):
        raise MembershipConflictError("Only an active membership can be locked")
    await _assert_membership_can_end(db_session, membership_record)
    await set_change_context(
        db_session, changed_by=principal.user_id, change_reason=reason
    )
    await identity_repository.apply_membership_values(
        db_session,
        membership_record,
        {"status": MembershipStatus.LOCKED.value, "status_reason": reason},
    )
    await identity_repository.clear_session_organization(
        db_session,
        user_id=membership_record.user_id,
        organization_id=membership_record.organization_id,
    )
    await _run_membership_end_hooks(
        db_session,
        membership_id=membership_record.membership_id,
        kind=MembershipEndKind.LOCKED,
        acting_user_id=principal.user_id,
        reason=reason,
    )
    user_record = await _require_user(db_session, membership_record.user_id)
    return await _to_member_response(db_session, membership_record, user_record)


async def unlock_member(
    db_session: AsyncSession,
    principal: Principal,
    membership_id: UUID,
    reason: str,
) -> MemberResponse:
    """Lift a membership lock.

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        membership_id: The membership to unlock.
        reason: Why.

    Returns:
        The unlocked member.

    Raises:
        MembershipNotFoundError: The membership is out of reach.
        AccessDeniedError: The caller may not manage members.
        MembershipConflictError: The membership is not locked.
    """
    membership_record = await _load_reachable_membership(
        db_session, principal, membership_id
    )
    _require_member_manager(principal, membership_record.organization_id)
    if (
        membership_record.status != MembershipStatus.LOCKED.value
        or membership_record.left_at is not None
    ):
        raise MembershipConflictError("The membership is not locked")
    await set_change_context(
        db_session, changed_by=principal.user_id, change_reason=reason
    )
    await identity_repository.apply_membership_values(
        db_session,
        membership_record,
        {"status": MembershipStatus.ACTIVE.value, "status_reason": None},
    )
    user_record = await _require_user(db_session, membership_record.user_id)
    return await _to_member_response(db_session, membership_record, user_record)


async def grant_role(
    db_session: AsyncSession,
    principal: Principal,
    membership_id: UUID,
    role: UserRole,
) -> RoleAssignmentResponse:
    """Grant a role to a member (ACC-13).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        membership_id: The member's membership.
        role: The role to grant.

    Returns:
        The new assignment.

    Raises:
        MembershipNotFoundError: The membership is out of reach.
        AccessDeniedError: The caller may not grant this role.
        RoleNotAllowedError: The role cannot be held in this organization.
        RoleConflictError: The member already holds the role.
        MembershipConflictError: The membership ended.
    """
    membership_record = await _load_reachable_membership(
        db_session, principal, membership_id
    )
    _require_member_manager(principal, membership_record.organization_id)
    if membership_record.left_at is not None:
        raise MembershipConflictError("The membership already ended")
    organization_record = await _require_organization(
        db_session, membership_record.organization_id
    )
    _check_role_grantable(principal, organization_record, role)
    if (
        await identity_repository.find_active_role_assignment(
            db_session, membership_id=membership_id, role=role.value
        )
        is not None
    ):
        raise RoleConflictError("The member already holds this role")
    assignment_record = await identity_repository.insert_role_assignment(
        db_session,
        membership_record=membership_record,
        role=role.value,
        granted_by=principal.user_id,
    )
    return RoleAssignmentResponse(
        membership_id=membership_id,
        role=role,
        granted_at=assignment_record.granted_at,
        granted_by=assignment_record.granted_by,
        revoked_at=None,
    )


async def revoke_role(
    db_session: AsyncSession,
    principal: Principal,
    membership_id: UUID,
    role: UserRole,
) -> None:
    """Revoke a role from a member (ACC-13).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        membership_id: The member's membership.
        role: The role to revoke.

    Raises:
        MembershipNotFoundError: The membership is out of reach.
        AccessDeniedError: The caller may not revoke this role.
        OrgAdminProtectedError: The role is ORG_ADMIN (use the handover).
        RoleConflictError: The member does not hold the role, or it is the
            last HEAD_ADMIN.
    """
    membership_record = await _load_reachable_membership(
        db_session, principal, membership_id
    )
    _require_member_manager(principal, membership_record.organization_id)
    if role is UserRole.ORG_ADMIN:
        raise OrgAdminProtectedError("Hand over the ORG_ADMIN role instead")
    if role in INTERNAL_ONLY_ROLES and not principal.has_any_role(UserRole.HEAD_ADMIN):
        raise AccessDeniedError("Only a HEAD_ADMIN may revoke an administrator role")
    assignment_record = await identity_repository.find_active_role_assignment(
        db_session, membership_id=membership_id, role=role.value
    )
    if assignment_record is None:
        raise RoleConflictError("The member does not hold this role")
    if (
        role is UserRole.HEAD_ADMIN
        and await identity_repository.count_active_role_holders(
            db_session, UserRole.HEAD_ADMIN.value
        )
        <= 1
    ):
        raise RoleConflictError("The last HEAD_ADMIN cannot lose the role")
    await identity_repository.revoke_role_assignment(
        db_session,
        assignment_record,
        revoked_at=utc_now(),
        revoked_by=principal.user_id,
    )


async def handover_org_admin(
    db_session: AsyncSession,
    principal: Principal,
    organization_id: UUID,
    handover_request: AdminHandoverRequest,
) -> MemberResponse:
    """Hand the ORG_ADMIN role to another active member in one transaction (ID-33).

    The current ORG_ADMIN may hand it over; when the administrator is gone
    one of our HEAD_ADMIN / CO_ADMIN appoints the next with `force`.

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        organization_id: The organization.
        handover_request: The new administrator's membership, a reason and
            whether our administrator forces the appointment.

    Returns:
        The new administrator.

    Raises:
        OrganizationNotFoundError: The organization is out of reach.
        AccessDeniedError: The caller is neither the current ORG_ADMIN nor an
            internal administrator using `force`.
        MembershipNotFoundError: The target is not in this organization.
        AdminHandoverInvalidError: The target is not active, or is already
            the ORG_ADMIN.
        RoleConflictError: A concurrent handover won.

    Side Effects:
        Revokes the old assignment and inserts the new one; the reason is
        logged (role assignments keep no reason column).
    """
    await _load_reachable_organization(db_session, principal, organization_id)
    current_assignment = await identity_repository.find_active_org_admin_assignment(
        db_session, organization_id
    )
    caller_is_admin = (
        current_assignment is not None
        and current_assignment.membership_id == principal.membership_id
        and principal.organization_id == organization_id
    )
    if not (
        caller_is_admin or (handover_request.force and _is_internal_admin(principal))
    ):
        raise AccessDeniedError(
            "Only the current ORG_ADMIN, or our administrator with force, may hand over"
        )
    target_membership = await identity_repository.get_membership(
        db_session, handover_request.to_membership_id
    )
    if (
        target_membership is None
        or target_membership.organization_id != organization_id
        or target_membership.left_at is not None
    ):
        raise MembershipNotFoundError("The new administrator is not a member")
    if target_membership.status != MembershipStatus.ACTIVE.value:
        raise AdminHandoverInvalidError(
            "The new administrator must be an active member"
        )
    if (
        current_assignment is not None
        and current_assignment.membership_id == target_membership.membership_id
    ):
        raise AdminHandoverInvalidError("The member is already the ORG_ADMIN")
    now = utc_now()
    try:
        if current_assignment is not None:
            await identity_repository.revoke_role_assignment(
                db_session,
                current_assignment,
                revoked_at=now,
                revoked_by=principal.user_id,
            )
        await identity_repository.insert_role_assignment(
            db_session,
            membership_record=target_membership,
            role=UserRole.ORG_ADMIN.value,
            granted_by=principal.user_id,
        )
    except IntegrityError as error:
        raise RoleConflictError("The ORG_ADMIN changed meanwhile; try again") from error
    logger.info(
        "org_admin_handover",
        extra={
            "organization_id": str(organization_id),
            "to_membership_id": str(target_membership.membership_id),
            "by_user_id": str(principal.user_id),
            "reason": handover_request.reason,
        },
    )
    user_record = await _require_user(db_session, target_membership.user_id)
    return await _to_member_response(db_session, target_membership, user_record)
