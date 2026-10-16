"""A person's account: password and phone changes, profile, administration (ACC-06, 07, 16).

Business rules for the changes a person makes to their own account (password
reset and change, phone-number change, profile) and for the internal
administrators' account management (list, read, lock and unlock, with the
last-HEAD_ADMIN guard, RV-ID7). Where the rest lives (CV-20, CV-23): sign-up,
invitation acceptance, login with its lockout, tokens, the organization
picker and the authorization of a request (`authenticate_session`,
`resolve_principal`, exposed to FastAPI by `dependencies.py`) are in
``session_service.py``; one-time codes in ``one_time_code_service.py``;
password hashing, strength and history in ``passwords.py``.

Transactions: nothing here commits. A failure that must leave a trace
(a failed-attempt counter, a lockout, an audit row) is flushed and then
signalled with an exception derived from `FailureRecordedError`; the router
commits before it lets such an exception propagate.

Limitations: a refresh token that was already replaced is simply unknown (no
reuse detection); recycled-phone protection (ID-27) is not built; SMS goes
through the logging fake of `providers.py`.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.audit_service as audit_service
import app.domains.identity.one_time_code_service as one_time_code_service
import app.domains.identity.passwords as identity_passwords
import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import (
    AccessDeniedError,
    AccountLockedError,
    InvalidOneTimeCodeError,
    UserConflictError,
    UserNotFoundError,
)
from app.domains.identity.models import (
    UserModel,
)
from app.domains.identity.schemas import (
    OneTimeCodeSendResponse,
    PasswordChangeRequest,
    PasswordResetRequest,
    PhoneChangeConfirmRequest,
    PhoneChangeRequest,
    ProfileUpdateRequest,
    UserListResponse,
    UserResponse,
)
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    MembershipStatus,
    OneTimeCodePurpose,
    OrganizationStatus,
    Principal,
    SessionIdentity,
    UserRole,
    UserStatus,
)
from app.libs.common.pagination import normalize_page_window
from app.libs.db.history import set_change_context

logger = logging.getLogger(__name__)


async def holds_internal_membership(db_session: AsyncSession, user_id: UUID) -> bool:
    """Tell whether any usable membership of a person is in an internal organization.

    The shortest session lifetime follows the person, not the organization the
    session happens to act for: a staff member who picks a customer
    organization keeps the staff lifetime (RV-ID10). A membership is usable
    when it is active and its organization is active.

    Args:
        db_session: Current database session.
        user_id: The person.

    Returns:
        ``True`` when at least one usable membership is internal.
    """
    for (
        membership_record,
        organization_record,
    ) in await identity_repository.list_live_memberships_with_organizations(
        db_session, user_id
    ):
        if (
            membership_record.status == MembershipStatus.ACTIVE.value
            and organization_record.status == OrganizationStatus.ACTIVE.value
            and organization_record.deleted_at is None
            and organization_record.is_internal
        ):
            return True
    return False


def to_user_response(user_record: UserModel) -> UserResponse:
    """Map a user row to its response (pure mapping, no I/O)."""
    return UserResponse.model_validate(user_record)


async def reset_password(
    db_session: AsyncSession,
    reset_request: PasswordResetRequest,
    client_context: ClientContext | None,
) -> None:
    """Set a new password with a PASSWORD_RESET code (ACC-06, ID-30).

    The reset also clears the login lockout and ends every session.

    Args:
        db_session: Session owned by the entry boundary.
        reset_request: Phone, code and new password.
        client_context: IP address and user agent of the request.

    Raises:
        WeakPasswordError: The password breaks the policy or was used recently.
        InvalidOneTimeCodeError: The code is wrong, expired or used (also for
            an unknown phone number).
        AccountLockedError: An administrator locked the account.
    """
    identity_passwords.check_password_strength(reset_request.new_password)
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, reset_request.phone_number
    )
    if user_record is None:
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    await one_time_code_service.consume_one_time_code(
        db_session,
        phone_number=reset_request.phone_number,
        purpose=OneTimeCodePurpose.PASSWORD_RESET,
        code=reset_request.code,
        user_id=user_record.user_id,
    )
    if user_record.status != UserStatus.ACTIVE.value:
        raise AccountLockedError("This account is locked; contact support")
    await identity_passwords.set_password(
        db_session, user_record.user_id, reset_request.new_password
    )
    state_record = await identity_repository.get_user_state_for_update(
        db_session, user_record.user_id
    )
    if state_record is not None:
        state_record.failed_login_count = 0
        state_record.login_locked_until = None
    await identity_repository.delete_sessions_by_user(db_session, user_record.user_id)
    await audit_service.record_account_event(
        db_session,
        user_id=user_record.user_id,
        organization_id=None,
        action=AccessAuditAction.PASSWORD_CHANGED,
        details=None,
        client_context=client_context,
    )


async def change_password(
    db_session: AsyncSession,
    session_identity: SessionIdentity,
    change_request: PasswordChangeRequest,
    client_context: ClientContext | None,
) -> None:
    """Change the password of a logged-in person (ACC-06).

    Every other session of the person ends; the current one stays.

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        change_request: Current and new password.
        client_context: IP address and user agent of the request.

    Raises:
        CurrentPasswordIncorrectError: The current password is wrong (counted
            towards the login lockout).
        LoginLockedError: The account is in its temporary lockout.
        WeakPasswordError: The new password breaks the policy or was used
            recently.
    """
    await identity_passwords.require_current_password(
        db_session, session_identity, change_request.current_password, client_context
    )
    await identity_passwords.set_password(
        db_session, session_identity.user_id, change_request.new_password
    )
    await identity_repository.delete_sessions_by_user(
        db_session,
        session_identity.user_id,
        except_session_id=session_identity.session_id,
    )
    await audit_service.record_account_event(
        db_session,
        user_id=session_identity.user_id,
        organization_id=session_identity.organization_id,
        action=AccessAuditAction.PASSWORD_CHANGED,
        details=None,
        client_context=client_context,
    )


async def request_phone_change(
    db_session: AsyncSession,
    session_identity: SessionIdentity,
    change_request: PhoneChangeRequest,
    client_context: ClientContext | None,
) -> OneTimeCodeSendResponse:
    """Send a code to a new phone number to prove it is the person's (ACC-06).

    The current password is checked first (and a wrong one is counted towards
    the login lockout), so a stolen session cannot move the login number.

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        change_request: The new phone number and the current password.
        client_context: IP address and user agent of the request.

    Returns:
        When the code expires.

    Raises:
        CurrentPasswordIncorrectError: The current password is wrong.
        LoginLockedError: The account is in its temporary lockout.
        UserConflictError: The number belongs to a live account.
        OneTimeCodeRateLimitError: A send limit is reached.
    """
    await identity_passwords.require_current_password(
        db_session, session_identity, change_request.current_password, client_context
    )
    if (
        await identity_repository.find_live_user_by_phone(
            db_session, change_request.new_phone_number
        )
        is not None
    ):
        raise UserConflictError("This phone number already has an account")
    code_record = await one_time_code_service.issue_one_time_code(
        db_session,
        purpose=OneTimeCodePurpose.PHONE_CHANGE,
        phone_number=change_request.new_phone_number,
        user_id=session_identity.user_id,
        issued_by=session_identity.user_id,
    )
    return OneTimeCodeSendResponse(expires_at=code_record.expires_at)


async def confirm_phone_change(
    db_session: AsyncSession,
    session_identity: SessionIdentity,
    confirm_request: PhoneChangeConfirmRequest,
    client_context: ClientContext | None,
) -> UserResponse:
    """Change the phone number once the code sent to the new one is typed.

    Every other session of the person ends; the current one stays.

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        confirm_request: The code.
        client_context: IP address and user agent of the request.

    Returns:
        The updated account.

    Raises:
        InvalidOneTimeCodeError: The code is wrong, expired or used.
        UserConflictError: The number was taken in the meantime.
    """
    code_record = await identity_repository.find_latest_one_time_code_by_user_id(
        db_session,
        user_id=session_identity.user_id,
        purpose=OneTimeCodePurpose.PHONE_CHANGE.value,
    )
    if code_record is None:
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    await one_time_code_service.consume_one_time_code(
        db_session,
        phone_number=code_record.phone_number,
        purpose=OneTimeCodePurpose.PHONE_CHANGE,
        code=confirm_request.code,
        user_id=session_identity.user_id,
    )
    user_record = await identity_repository.get_user(
        db_session, session_identity.user_id
    )
    if user_record is None:
        raise UserNotFoundError("The user does not exist")
    await set_change_context(
        db_session,
        changed_by=session_identity.user_id,
        change_reason="Phone number changed by the person",
    )
    try:
        await identity_repository.apply_user_values(
            db_session, user_record, {"phone_number": code_record.phone_number}
        )
    except IntegrityError as error:
        raise UserConflictError("This phone number already has an account") from error
    # The login number is the account's key: every other device logs in again
    # (RV-ID6).
    await identity_repository.delete_sessions_by_user(
        db_session,
        session_identity.user_id,
        except_session_id=session_identity.session_id,
    )
    await audit_service.record_account_event(
        db_session,
        user_id=session_identity.user_id,
        organization_id=session_identity.organization_id,
        action=AccessAuditAction.PHONE_CHANGED,
        details=None,
        client_context=client_context,
    )
    return to_user_response(user_record)


async def update_profile(
    db_session: AsyncSession,
    session_identity: SessionIdentity,
    update_request: ProfileUpdateRequest,
) -> UserResponse:
    """Change the caller's own name or e-mail address.

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        update_request: New values; a null field is left unchanged.

    Returns:
        The updated account.

    Raises:
        UserConflictError: The e-mail belongs to another live account.
    """
    user_record = await identity_repository.get_user(
        db_session, session_identity.user_id
    )
    if user_record is None:
        raise UserNotFoundError("The user does not exist")
    values: dict[str, Any] = {}
    if update_request.full_name is not None:
        values["full_name"] = update_request.full_name
    if update_request.email is not None:
        other_user = await identity_repository.find_live_user_by_email(
            db_session, update_request.email
        )
        if other_user is not None and other_user.user_id != user_record.user_id:
            raise UserConflictError("This e-mail address already has an account")
        values["email"] = update_request.email
    if not values:
        return to_user_response(user_record)
    await set_change_context(
        db_session,
        changed_by=session_identity.user_id,
        change_reason="Profile edited by the person",
    )
    try:
        await identity_repository.apply_user_values(db_session, user_record, values)
    except IntegrityError as error:
        raise UserConflictError("This e-mail address already has an account") from error
    return to_user_response(user_record)


def _require_internal_admin(principal: Principal) -> None:
    """Allow only an internal HEAD_ADMIN or CO_ADMIN.

    Raises:
        AccessDeniedError: For anyone else.
    """
    if not (
        principal.is_internal
        and principal.has_any_role(UserRole.HEAD_ADMIN, UserRole.CO_ADMIN)
    ):
        raise AccessDeniedError("Only our administrators may manage accounts")


async def list_users(
    db_session: AsyncSession,
    principal: Principal,
    *,
    page: int,
    page_size: int,
    search_text: str | None,
    status: UserStatus | None,
) -> UserListResponse:
    """List accounts across all organizations (ACC-03).

    Args:
        db_session: Current database session.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        page: Page number from 1.
        page_size: Rows per page.
        search_text: Substring of name, phone number or e-mail.
        status: Only this account status.

    Returns:
        A page of accounts, newest first.

    Raises:
        AccessDeniedError: The caller is not an internal administrator.
    """
    _require_internal_admin(principal)
    page_window = normalize_page_window(page, page_size)
    status_value = None if status is None else status.value
    user_records = await identity_repository.list_users(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        search_text=search_text,
        status=status_value,
    )
    total = await identity_repository.count_users(
        db_session, search_text=search_text, status=status_value
    )
    return UserListResponse(
        items=[to_user_response(user_record) for user_record in user_records],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_user(
    db_session: AsyncSession, principal: Principal, user_id: UUID
) -> UserResponse:
    """Read one account (ACC-03).

    Args:
        db_session: Current database session.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        user_id: The account.

    Returns:
        The account.

    Raises:
        AccessDeniedError: The caller is not an internal administrator.
        UserNotFoundError: The account does not exist.
    """
    _require_internal_admin(principal)
    user_record = await identity_repository.get_user(db_session, user_id)
    if user_record is None:
        raise UserNotFoundError("The user does not exist")
    return to_user_response(user_record)


async def lock_user(
    db_session: AsyncSession, principal: Principal, user_id: UUID, reason: str
) -> UserResponse:
    """Lock a whole account across all organizations and end its sessions (ID-08).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        user_id: The account to lock.
        reason: Why (kept in the history and `status_reason`).

    Returns:
        The locked account.

    Raises:
        AccessDeniedError: The caller is not an internal administrator, or a
            CO_ADMIN tries to lock a HEAD_ADMIN or another CO_ADMIN (ID-12).
        UserNotFoundError: The account does not exist.
        UserConflictError: The account is the caller's own or already locked,
            or it is the last usable HEAD_ADMIN (RV-ID7: two HEAD_ADMINs
            locking each other at the same time).
    """
    _require_internal_admin(principal)
    user_record = await identity_repository.get_user(db_session, user_id)
    if user_record is None or user_record.deleted_at is not None:
        raise UserNotFoundError("The user does not exist")
    if user_record.user_id == principal.user_id:
        raise UserConflictError("You cannot lock your own account")
    if user_record.status == UserStatus.LOCKED.value:
        raise UserConflictError("The account is already locked")
    await _require_may_manage_account(db_session, principal, user_id)
    if (
        await _holds_role_anywhere(db_session, user_id, UserRole.HEAD_ADMIN)
        and await count_other_head_admins_under_lock(
            db_session, excluding_user_id=user_id
        )
        == 0
    ):
        raise UserConflictError("The last HEAD_ADMIN cannot be locked")
    await set_change_context(
        db_session, changed_by=principal.user_id, change_reason=reason
    )
    await identity_repository.apply_user_values(
        db_session,
        user_record,
        {"status": UserStatus.LOCKED.value, "status_reason": reason},
    )
    await identity_repository.delete_sessions_by_user(db_session, user_id)
    # Imported here because `member_service` imports this module. The locked
    # person's driving sessions and trips end like for a locked membership
    # (DR-10, RV-ID11).
    import app.domains.identity.member_service as member_service

    await member_service.run_membership_end_hooks_for_user(
        db_session,
        user_id=user_id,
        acting_user_id=principal.user_id,
        reason=reason,
    )
    return to_user_response(user_record)


async def unlock_user(
    db_session: AsyncSession, principal: Principal, user_id: UUID, reason: str
) -> UserResponse:
    """Unlock an account; it returns to ACTIVE, or INVITED if it has no password.

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        user_id: The account to unlock.
        reason: Why (kept in the history).

    Returns:
        The unlocked account.

    Raises:
        AccessDeniedError: The caller is not an internal administrator.
        UserNotFoundError: The account does not exist.
        UserConflictError: The account is not locked.
    """
    _require_internal_admin(principal)
    user_record = await identity_repository.get_user(db_session, user_id)
    if user_record is None or user_record.deleted_at is not None:
        raise UserNotFoundError("The user does not exist")
    if user_record.status != UserStatus.LOCKED.value:
        raise UserConflictError("The account is not locked")
    await _require_may_manage_account(db_session, principal, user_id)
    has_password = (
        await identity_repository.find_active_credential(db_session, user_id)
        is not None
    )
    await set_change_context(
        db_session, changed_by=principal.user_id, change_reason=reason
    )
    await identity_repository.apply_user_values(
        db_session,
        user_record,
        {
            "status": (UserStatus.ACTIVE if has_password else UserStatus.INVITED).value,
            "status_reason": None,
        },
    )
    return to_user_response(user_record)


async def count_other_head_admins_under_lock(
    db_session: AsyncSession,
    *,
    excluding_membership_id: UUID | None = None,
    excluding_user_id: UUID | None = None,
) -> int:
    """Count the usable HEAD_ADMINs that remain, under the role-holder lock (RV-ID7).

    Every path that can remove a usable HEAD_ADMIN (ending or locking a
    membership, revoking the role, locking the account) asks this before the
    change and refuses on zero. The lock stays held until the transaction
    ends, so a concurrent removal of another HEAD_ADMIN waits and then counts
    this one's removal.

    Args:
        db_session: Session owned by the entry boundary.
        excluding_membership_id: The membership about to be removed.
        excluding_user_id: The account about to be locked.

    Returns:
        How many usable HEAD_ADMIN memberships remain besides the excluded
        ones.
    """
    await identity_repository.lock_role_holder_changes(
        db_session, UserRole.HEAD_ADMIN.value
    )
    return await identity_repository.count_active_role_holders(
        db_session,
        UserRole.HEAD_ADMIN.value,
        excluding_membership_id=excluding_membership_id,
        excluding_user_id=excluding_user_id,
    )


async def _require_may_manage_account(
    db_session: AsyncSession, principal: Principal, user_id: UUID
) -> None:
    """Refuse to lock or unlock an administrator's account unless HEAD_ADMIN (ID-12).

    Args:
        db_session: Current database session.
        principal: The caller.
        user_id: The account about to be locked or unlocked.

    Raises:
        AccessDeniedError: The account holds HEAD_ADMIN or CO_ADMIN anywhere
            and the caller is not a HEAD_ADMIN (RV-ID1).
    """
    if principal.has_any_role(UserRole.HEAD_ADMIN):
        return
    for role in (UserRole.HEAD_ADMIN, UserRole.CO_ADMIN):
        if await _holds_role_anywhere(db_session, user_id, role):
            raise AccessDeniedError("Only a HEAD_ADMIN may manage an administrator")


async def _holds_role_anywhere(
    db_session: AsyncSession, user_id: UUID, role: UserRole
) -> bool:
    """Tell whether a user holds a role through any active membership.

    Args:
        db_session: Current database session.
        user_id: The user.
        role: The role to look for.

    Returns:
        `True` if one of the user's live memberships holds it.
    """
    pairs = await identity_repository.list_live_memberships_with_organizations(
        db_session, user_id
    )
    for membership_record, _organization_record in pairs:
        role_values = await identity_repository.list_active_roles_by_membership(
            db_session, membership_record.membership_id
        )
        if role.value in role_values:
            return True
    return False
