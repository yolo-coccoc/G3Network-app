"""Accounts, credentials, one-time codes and login sessions (ACC-03..07, 10, 16).

Business rules for a person's account: self-registration, invitation
acceptance, login with a temporary lockout, token refresh, logout, password
reset and change, phone-number change, the organization picker and the
administrators' account lock. Authorization of a request (`authenticate_session`,
`resolve_principal`) lives here too; `dependencies.py` exposes it to FastAPI.

Transactions: nothing here commits. A failure that must leave a trace
(a failed-attempt counter, a lockout, an audit row) is flushed and then
signalled with an exception derived from `FailureRecordedError`; the router
commits before it lets such an exception propagate.

Limitations: a refresh token that was already replaced is simply unknown (no
reuse detection); recycled-phone protection (ID-27) is not built; SMS goes
through the logging fake of `providers.py`.
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.audit_service as audit_service
import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import (
    AccessDeniedError,
    AccountLockedError,
    CurrentPasswordIncorrectError,
    InvalidCredentialsError,
    InvalidOneTimeCodeError,
    LoginLockedError,
    MembershipNotFoundError,
    OneTimeCodePurposeError,
    OneTimeCodeRateLimitError,
    OrganizationInactiveError,
    OrganizationNotSelectedError,
    SessionInvalidError,
    SessionNotFoundError,
    UserConflictError,
    UserNotFoundError,
    WeakPasswordError,
)
from app.domains.identity.models import (
    OneTimeCodeModel,
    OrganizationModel,
    UserModel,
    UserSessionModel,
    UserStateModel,
)
from app.domains.identity.providers import get_sms_sender
from app.domains.identity.schemas import (
    DeviceFields,
    InvitationAcceptRequest,
    LoginRequest,
    MembershipSummaryResponse,
    MeResponse,
    OneTimeCodeSendRequest,
    OneTimeCodeSendResponse,
    PasswordChangeRequest,
    PasswordResetRequest,
    PhoneChangeConfirmRequest,
    PhoneChangeRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    SessionResponse,
    SignUpRequest,
    TokenResponse,
    UserListResponse,
    UserResponse,
)
from app.domains.identity.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    generate_one_time_code,
    generate_refresh_token,
    hash_one_time_code,
    hash_password,
    hash_refresh_token,
    normalize_phone_number,
    read_access_token,
    verify_one_time_code,
    verify_password,
)
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    MembershipStatus,
    OneTimeCodePurpose,
    OrganizationLegalForm,
    OrganizationStatus,
    Principal,
    SessionIdentity,
    SessionPlatform,
    UserRole,
    UserStatus,
    features_of_roles,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window
from app.libs.db.history import set_change_context

logger = logging.getLogger(__name__)

# ID-31: this many revoked password hashes are kept for the reuse check.
PASSWORD_HISTORY_REVOKED_KEPT = 5

# ID-24: `last_used_at` and `last_active_at` are written at most this often.
ACTIVITY_WRITE_INTERVAL = timedelta(minutes=5)

# Codes older than this past their expiry are deleted (ID-37).
STALE_CODE_RETENTION = timedelta(days=1)

# Purposes a person may request without being logged in.
_PUBLIC_CODE_PURPOSES = frozenset(
    {
        OneTimeCodePurpose.SIGN_UP,
        OneTimeCodePurpose.INVITE,
        OneTimeCodePurpose.PASSWORD_RESET,
    }
)

# Fixed text for an invitation to an existing account: nothing the inviter typed
# or chose (an organization name, free text) reaches the recipient (RV-ID2).
_INVITE_NOTICE_MESSAGE = (
    "G3 Network: ban co mot loi moi vao mot to chuc. "
    "Dang nhap ung dung de xem va chap nhan."
)

_CODE_MESSAGES = {
    OneTimeCodePurpose.SIGN_UP: "Ma dang ky G3 Network: {code}. Het han sau {minutes} phut.",
    OneTimeCodePurpose.INVITE: (
        "Ban duoc moi vao G3 Network. Ma kich hoat: {code}. Het han sau {minutes} phut."
    ),
    OneTimeCodePurpose.PASSWORD_RESET: (
        "Ma dat lai mat khau G3 Network: {code}. Het han sau {minutes} phut."
    ),
    OneTimeCodePurpose.PHONE_CHANGE: (
        "Ma doi so dien thoai G3 Network: {code}. Het han sau {minutes} phut."
    ),
}


async def _holds_internal_membership(db_session: AsyncSession, user_id: UUID) -> bool:
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


def session_lifetime(platform: str, is_internal: bool) -> timedelta:
    """Return the idle lifetime of a session (ID-36).

    Args:
        platform: `SessionPlatform` value of the session.
        is_internal: Whether the person belongs to an internal organization.

    Returns:
        1 day for internal staff, 90 days for the phone apps, 7 days for a
        browser (the lifetimes are settings).
    """
    if is_internal:
        return timedelta(days=settings.IDENTITY_SESSION_INTERNAL_DAYS)
    if platform == SessionPlatform.WEB.value:
        return timedelta(days=settings.IDENTITY_SESSION_PORTAL_DAYS)
    return timedelta(days=settings.IDENTITY_SESSION_DRIVER_APP_DAYS)


def to_user_response(user_record: UserModel) -> UserResponse:
    """Map a user row to its response (pure mapping, no I/O)."""
    return UserResponse.model_validate(user_record)


async def _hash_password_async(password: str) -> str:
    """Hash a password on a worker thread so the event loop keeps serving."""
    return await asyncio.to_thread(hash_password, password)


async def _verify_password_async(password: str, stored_hash: str) -> bool:
    """Verify a password on a worker thread so the event loop keeps serving."""
    return await asyncio.to_thread(verify_password, password, stored_hash)


# ---------------------------------------------------------------------------
# One-time codes
# ---------------------------------------------------------------------------


async def _enforce_send_limits(
    db_session: AsyncSession, *, phone_number: str, purpose: OneTimeCodePurpose
) -> None:
    """Refuse a code that comes too soon or too often for a phone number.

    Args:
        db_session: Current database session.
        phone_number: Destination of the code.
        purpose: What the code is for.

    Raises:
        OneTimeCodeRateLimitError: If the resend cooldown has not passed or
            the daily limit of the number is reached (SMS-pumping guard).
    """
    now = utc_now()
    last_sent_at = await identity_repository.find_latest_one_time_code_time(
        db_session, phone_number=phone_number, purpose=purpose.value
    )
    cooldown = timedelta(seconds=settings.IDENTITY_OTP_RESEND_COOLDOWN_SECONDS)
    if last_sent_at is not None and now - last_sent_at < cooldown:
        raise OneTimeCodeRateLimitError(
            "A code was just sent; wait before asking again"
        )
    sent_today = await identity_repository.count_one_time_codes_since(
        db_session, phone_number, now - timedelta(hours=24)
    )
    if sent_today >= settings.IDENTITY_OTP_MAX_PER_PHONE_PER_DAY:
        raise OneTimeCodeRateLimitError("Too many codes for this phone number today")


async def _enforce_inviter_quota(db_session: AsyncSession, issued_by: UUID) -> None:
    """Refuse an invitation SMS when the inviter sent too many today (RV-ID2).

    Args:
        db_session: Current database session.
        issued_by: The person who invites.

    Raises:
        OneTimeCodeRateLimitError: If `IDENTITY_INVITES_PER_USER_PER_DAY` is
            reached.
    """
    sent_today = await identity_repository.count_invitation_codes_by_issuer_since(
        db_session, issued_by=issued_by, since=utc_now() - timedelta(hours=24)
    )
    if sent_today >= settings.IDENTITY_INVITES_PER_USER_PER_DAY:
        raise OneTimeCodeRateLimitError("Too many invitations sent today")


async def issue_one_time_code(
    db_session: AsyncSession,
    *,
    purpose: OneTimeCodePurpose,
    phone_number: str,
    user_id: UUID | None,
    issued_by: UUID | None,
    organization_id: UUID | None = None,
) -> OneTimeCodeModel:
    """Create a code, store its hash and send it by SMS (ID-16).

    Also applies the send limits and deletes codes that expired long ago. An
    `INVITE_NOTICE` row (an invitation to an existing account) carries a code
    nobody is told: it only counts towards the limits, and its SMS is a fixed
    text with no organization name (RV-ID2).

    Args:
        db_session: Session owned by the entry boundary.
        purpose: What the code is for; decides its lifetime (invitations last
            `IDENTITY_INVITE_TTL_HOURS`, the others `IDENTITY_OTP_TTL_MINUTES`).
        phone_number: Destination, E.164.
        user_id: The user the code is for; `None` for a sign-up.
        issued_by: The user who triggered it; `None` when self-requested.
        organization_id: For an invitation, the organization it is for.

    Returns:
        The stored code row (the plain code is only in the SMS).

    Raises:
        OneTimeCodeRateLimitError: If a send limit is reached.

    Side Effects:
        Inserts a `one_time_codes` row and sends an SMS through the provider.
    """
    now = utc_now()
    await identity_repository.delete_stale_one_time_codes(
        db_session, now - STALE_CODE_RETENTION
    )
    await _enforce_send_limits(db_session, phone_number=phone_number, purpose=purpose)
    if issued_by is not None and purpose in (
        OneTimeCodePurpose.INVITE,
        OneTimeCodePurpose.INVITE_NOTICE,
    ):
        await _enforce_inviter_quota(db_session, issued_by)
    ttl = (
        timedelta(hours=settings.IDENTITY_INVITE_TTL_HOURS)
        if purpose in (OneTimeCodePurpose.INVITE, OneTimeCodePurpose.INVITE_NOTICE)
        else timedelta(minutes=settings.IDENTITY_OTP_TTL_MINUTES)
    )
    one_time_code_id = uuid4()
    code = generate_one_time_code()
    code_record = await identity_repository.insert_one_time_code(
        db_session,
        {
            "one_time_code_id": one_time_code_id,
            "purpose": purpose.value,
            "user_id": user_id,
            "phone_number": phone_number,
            "code_hash": hash_one_time_code(one_time_code_id, code),
            "issued_by": issued_by,
            "organization_id": organization_id,
            "created_at": now,
            "expires_at": now + ttl,
            "failed_attempt_count": 0,
        },
    )
    await get_sms_sender().send_sms(
        phone_number,
        _INVITE_NOTICE_MESSAGE
        if purpose is OneTimeCodePurpose.INVITE_NOTICE
        else _CODE_MESSAGES[purpose].format(
            code=code, minutes=int(ttl.total_seconds() // 60)
        ),
    )
    return code_record


async def _consume_one_time_code(
    db_session: AsyncSession,
    *,
    phone_number: str,
    purpose: OneTimeCodePurpose,
    code: str,
    user_id: UUID | None = None,
) -> OneTimeCodeModel:
    """Check a typed code against the newest code and mark it used.

    Only the newest code of the phone number and purpose is checked; a wrong
    guess counts against it and the code dies at
    `IDENTITY_OTP_MAX_FAILED_ATTEMPTS` (ID-37).

    Args:
        db_session: Session owned by the entry boundary.
        phone_number: Number the code was sent to.
        purpose: What the code is for.
        code: The code the person typed.
        user_id: Only a code issued for this user, if given.

    Returns:
        The code row, now used.

    Raises:
        InvalidOneTimeCodeError: If there is no live code, it is wrong,
            expired, used, or dead; a wrong guess is counted and flushed.
    """
    now = utc_now()
    code_record = await identity_repository.find_latest_one_time_code(
        db_session, phone_number=phone_number, purpose=purpose.value, user_id=user_id
    )
    if (
        code_record is None
        or code_record.used_at is not None
        or code_record.expires_at <= now
        or code_record.failed_attempt_count >= settings.IDENTITY_OTP_MAX_FAILED_ATTEMPTS
    ):
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    if not verify_one_time_code(
        code_record.one_time_code_id, code, code_record.code_hash
    ):
        code_record.failed_attempt_count += 1
        await db_session.flush()
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    code_record.used_at = now
    await db_session.flush()
    return code_record


async def send_one_time_code(
    db_session: AsyncSession, send_request: OneTimeCodeSendRequest
) -> OneTimeCodeSendResponse:
    """Send a sign-up, invitation or password-reset code (ACC-05).

    For a password reset or an invitation the answer is the same whether or
    not the phone number has an account, and nothing is sent when it has not,
    so the endpoint cannot be used to find out who is registered. A sign-up
    for a number that already has an account is reported (`409`, the app then
    goes to login).

    Args:
        db_session: Session owned by the entry boundary.
        send_request: Phone number and purpose.

    Returns:
        When the code (would) expire.

    Raises:
        OneTimeCodePurposeError: If the purpose needs a login (phone change).
        UserConflictError: For a sign-up when the number is registered.
        OneTimeCodeRateLimitError: If a send limit is reached.

    Side Effects:
        May insert a code row and send an SMS.
    """
    purpose = send_request.purpose
    if purpose not in _PUBLIC_CODE_PURPOSES:
        raise OneTimeCodePurposeError("This code needs a login")
    phone_number = send_request.phone_number
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, phone_number
    )
    ttl = (
        timedelta(hours=settings.IDENTITY_INVITE_TTL_HOURS)
        if purpose is OneTimeCodePurpose.INVITE
        else timedelta(minutes=settings.IDENTITY_OTP_TTL_MINUTES)
    )
    silent_response = OneTimeCodeSendResponse(expires_at=utc_now() + ttl)

    if purpose is OneTimeCodePurpose.SIGN_UP:
        # An account that only exists because someone invited the number has
        # no password: the number's real owner may still sign up (RV-ID3).
        if user_record is not None and user_record.status != UserStatus.INVITED.value:
            raise UserConflictError("This phone number already has an account")
        user_id = None
    elif purpose is OneTimeCodePurpose.PASSWORD_RESET:
        if user_record is None or user_record.status != UserStatus.ACTIVE.value:
            return silent_response
        user_id = user_record.user_id
    else:
        if user_record is None or user_record.status != UserStatus.INVITED.value:
            return silent_response
        user_id = user_record.user_id

    code_record = await issue_one_time_code(
        db_session,
        purpose=purpose,
        phone_number=phone_number,
        user_id=user_id,
        issued_by=None,
    )
    return OneTimeCodeSendResponse(expires_at=code_record.expires_at)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def _check_password_strength(password: str) -> None:
    """Refuse a password that is too short or only spaces.

    Raises:
        WeakPasswordError: If the password breaks the policy.
    """
    if len(password) < settings.IDENTITY_PASSWORD_MIN_LENGTH or not password.strip():
        raise WeakPasswordError(
            f"The password needs at least {settings.IDENTITY_PASSWORD_MIN_LENGTH} "
            "characters"
        )


async def set_password(
    db_session: AsyncSession, user_id: UUID, new_password: str
) -> None:
    """Replace a user's password: revoke the old credential, add the new (ID-17).

    Refuses a password equal to one of the last ones (ID-31) and prunes revoked
    credentials beyond the last 5.

    Args:
        db_session: Session owned by the entry boundary.
        user_id: The user.
        new_password: The plain new password.

    Raises:
        WeakPasswordError: If the password breaks the policy or was used
            recently.

    Side Effects:
        Revokes and inserts `user_credentials` rows.
    """
    _check_password_strength(new_password)
    recent_hashes = await identity_repository.list_recent_password_hashes(
        db_session, user_id, PASSWORD_HISTORY_REVOKED_KEPT + 1
    )
    for recent_hash in recent_hashes:
        if await _verify_password_async(new_password, recent_hash):
            raise WeakPasswordError("This password was used recently; choose another")
    new_hash = await _hash_password_async(new_password)
    await identity_repository.revoke_active_credential(db_session, user_id, utc_now())
    await identity_repository.insert_credential(
        db_session, user_id=user_id, secret_hash=new_hash
    )
    await identity_repository.prune_revoked_credentials(
        db_session, user_id, PASSWORD_HISTORY_REVOKED_KEPT
    )


# ---------------------------------------------------------------------------
# Sessions and tokens
# ---------------------------------------------------------------------------


async def build_membership_summaries(
    db_session: AsyncSession, user_id: UUID
) -> list[MembershipSummaryResponse]:
    """List a person's memberships with their organizations and roles.

    Args:
        db_session: Current database session.
        user_id: The person.

    Returns:
        One summary per membership that has not ended (pending invitations
        included), oldest first.
    """
    summaries = []
    pairs = await identity_repository.list_live_memberships_with_organizations(
        db_session, user_id
    )
    for membership_record, organization_record in pairs:
        role_values = await identity_repository.list_active_roles_by_membership(
            db_session, membership_record.membership_id
        )
        summaries.append(
            MembershipSummaryResponse(
                membership_id=membership_record.membership_id,
                organization_id=organization_record.organization_id,
                organization_name=organization_record.display_name,
                organization_status=organization_record.status,
                is_internal=organization_record.is_internal,
                status=membership_record.status,
                roles=[UserRole(role_value) for role_value in role_values],
            )
        )
    return summaries


async def _create_session(
    db_session: AsyncSession,
    *,
    user_record: UserModel,
    device: DeviceFields,
    organization_id: UUID | None,
    is_internal: bool,
) -> tuple[UserSessionModel, str]:
    """Insert a login session and return it with its plain refresh token.

    Args:
        db_session: Session owned by the entry boundary.
        user_record: The person logging in.
        device: Platform, app version, device label and push token.
        organization_id: Organization shown on the device, if known.
        is_internal: Whether the person belongs to an internal organization
            (shorter lifetime).

    Returns:
        The session row and the refresh token (never stored in plain).

    Side Effects:
        Removes the push token from any other session that holds it, and
        deletes sessions that expired.
    """
    now = utc_now()
    await identity_repository.delete_expired_sessions(db_session, now)
    if device.push_token is not None:
        await identity_repository.clear_push_token(db_session, device.push_token)
    refresh_token = generate_refresh_token()
    session_record = await identity_repository.insert_session(
        db_session,
        {
            "user_id": user_record.user_id,
            "organization_id": organization_id,
            "platform": device.platform.value,
            "app_version": device.app_version,
            "device_label": device.device_label,
            "refresh_token_hash": hash_refresh_token(refresh_token),
            "push_token": device.push_token,
            "created_at": now,
            "last_used_at": now,
            "expires_at": now + session_lifetime(device.platform.value, is_internal),
        },
    )
    return session_record, refresh_token


async def _build_token_response(
    db_session: AsyncSession,
    session_record: UserSessionModel,
    refresh_token: str,
) -> TokenResponse:
    """Sign an access token for a session and assemble the login answer.

    Args:
        db_session: Current database session.
        session_record: The session.
        refresh_token: The plain refresh token to hand to the client.

    Returns:
        Both tokens, the session ID, the current organization and the
        person's memberships.
    """
    ttl = timedelta(seconds=settings.IDENTITY_ACCESS_TOKEN_TTL_SECONDS)
    access_token = create_access_token(session_record.user_session_id, utc_now() + ttl)
    return TokenResponse(
        access_token=access_token,
        expires_in=int(ttl.total_seconds()),
        refresh_token=refresh_token,
        session_id=session_record.user_session_id,
        organization_id=session_record.organization_id,
        memberships=await build_membership_summaries(
            db_session, session_record.user_id
        ),
    )


def _choose_organization(
    usable: list[OrganizationModel], last_organization_id: UUID | None
) -> UUID | None:
    """Pick the organization a new session opens in (ID-09).

    Args:
        usable: Organizations the person has an active membership in and that
            are active.
        last_organization_id: The organization the person used last.

    Returns:
        The only organization, else the remembered one when still usable,
        else `None` (the app shows the picker).
    """
    if len(usable) == 1:
        return usable[0].organization_id
    for organization_record in usable:
        if organization_record.organization_id == last_organization_id:
            return organization_record.organization_id
    return None


async def _alert_emergency_login(
    db_session: AsyncSession, user_record: UserModel
) -> None:
    """Tell the other head administrators the sealed account logged in (ACC-20).

    The account is the one whose phone number is `IDENTITY_EMERGENCY_ADMIN_PHONE`.
    The login itself is already in the audit log as `LOGIN_SUCCESS`.

    Args:
        db_session: Current database session.
        user_record: The person who just logged in.

    Side Effects:
        Logs a warning and sends an SMS to every other active HEAD_ADMIN.
    """
    configured_phone = settings.IDENTITY_EMERGENCY_ADMIN_PHONE
    if not configured_phone:
        return
    try:
        emergency_phone = normalize_phone_number(configured_phone)
    except ValueError:
        logger.error("emergency_admin_phone_invalid")
        return
    if user_record.phone_number != emergency_phone:
        return
    logger.warning("emergency_admin_login", extra={"user_id": str(user_record.user_id)})
    sms_sender = get_sms_sender()
    head_admins = await identity_repository.list_users_holding_role(
        db_session, UserRole.HEAD_ADMIN.value
    )
    for head_admin in head_admins:
        if head_admin.user_id != user_record.user_id:
            await sms_sender.send_sms(
                head_admin.phone_number,
                "G3 Network canh bao: tai khoan quan tri khan cap vua dang nhap.",
            )


async def _start_session_for_login(
    db_session: AsyncSession,
    *,
    user_record: UserModel,
    device: DeviceFields,
    client_context: ClientContext | None,
) -> TokenResponse:
    """Open a session for a person who proved who they are.

    Checks the organizations they can enter, picks the one to open, creates
    the session, updates `user_state`, writes `LOGIN_SUCCESS` and raises the
    emergency alert when it applies.

    Args:
        db_session: Session owned by the entry boundary.
        user_record: The authenticated, active person.
        device: Platform, app version, device label and push token.
        client_context: IP address and user agent of the request.

    Returns:
        The token response.

    Raises:
        OrganizationInactiveError: If the person has active memberships but
            every one is in a suspended or closed organization.
    """
    now = utc_now()
    pairs = await identity_repository.list_live_memberships_with_organizations(
        db_session, user_record.user_id
    )
    active_pairs = [
        (membership_record, organization_record)
        for membership_record, organization_record in pairs
        if membership_record.status == MembershipStatus.ACTIVE.value
    ]
    usable = [
        organization_record
        for _membership, organization_record in active_pairs
        if organization_record.status == OrganizationStatus.ACTIVE.value
        and organization_record.deleted_at is None
    ]
    state_record = await identity_repository.get_user_state_for_update(
        db_session, user_record.user_id
    )
    if active_pairs and not usable:
        await audit_service.record_account_event(
            db_session,
            user_id=user_record.user_id,
            organization_id=None,
            action=AccessAuditAction.LOGIN_FAILED,
            details={"failure": "ACCOUNT_LOCKED"},
            client_context=client_context,
        )
        raise OrganizationInactiveError(
            "Your organization is suspended or closed; contact your administrator"
        )
    chosen_organization_id = _choose_organization(
        usable, None if state_record is None else state_record.last_organization_id
    )
    session_record, refresh_token = await _create_session(
        db_session,
        user_record=user_record,
        device=device,
        organization_id=chosen_organization_id,
        is_internal=any(organization.is_internal for organization in usable),
    )
    if state_record is not None:
        state_record.failed_login_count = 0
        state_record.login_locked_until = None
        state_record.last_login_at = now
        state_record.last_active_at = now
        if chosen_organization_id is not None:
            state_record.last_organization_id = chosen_organization_id
    await audit_service.record_account_event(
        db_session,
        user_id=user_record.user_id,
        organization_id=chosen_organization_id,
        action=AccessAuditAction.LOGIN_SUCCESS,
        details=None,
        client_context=client_context,
    )
    await _alert_emergency_login(db_session, user_record)
    await db_session.flush()
    return await _build_token_response(db_session, session_record, refresh_token)


async def _count_failed_password(
    db_session: AsyncSession,
    *,
    user_id: UUID,
    state_record: UserStateModel | None,
    client_context: ClientContext | None,
) -> None:
    """Count a wrong password towards the login lockout (ID-23).

    Shared by the login and by the checks that ask for the current password
    (password change, phone change), so guessing through a stolen session is
    limited like guessing at the login screen (RV-ID6). The caller flushes.

    Args:
        db_session: Current database session.
        user_id: The person whose password was guessed.
        state_record: The person's locked `user_state` row, if it exists.
        client_context: IP address and user agent of the request.

    Side Effects:
        Raises the failure counter; at the limit the counter restarts and the
        lockout window opens (with an audit row).
    """
    if state_record is None:
        return
    state_record.failed_login_count += 1
    if state_record.failed_login_count >= settings.IDENTITY_LOGIN_MAX_FAILED_ATTEMPTS:
        # The counter restarts so the next burst of guesses after the lockout
        # needs the full number of attempts again.
        state_record.failed_login_count = 0
        state_record.login_locked_until = utc_now() + timedelta(
            minutes=settings.IDENTITY_LOGIN_LOCKOUT_MINUTES
        )
        await audit_service.record_account_event(
            db_session,
            user_id=user_id,
            organization_id=None,
            action=AccessAuditAction.LOGIN_LOCKED,
            details={"locked_minutes": settings.IDENTITY_LOGIN_LOCKOUT_MINUTES},
            client_context=client_context,
        )


async def _require_current_password(
    db_session: AsyncSession,
    session_identity: SessionIdentity,
    current_password: str,
    client_context: ClientContext | None,
) -> None:
    """Check the current password of a logged-in person, counting a wrong one.

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        current_password: The password the person typed.
        client_context: IP address and user agent of the request.

    Raises:
        LoginLockedError: The account is in its temporary lockout.
        CurrentPasswordIncorrectError: The password is wrong; the guess is
            counted and flushed, and the router commits it.
    """
    state_record = await identity_repository.get_user_state_for_update(
        db_session, session_identity.user_id
    )
    if (
        state_record is not None
        and state_record.login_locked_until is not None
        and state_record.login_locked_until > utc_now()
    ):
        raise LoginLockedError("Too many wrong passwords; try again later")
    credential_record = await identity_repository.find_active_credential(
        db_session, session_identity.user_id
    )
    if credential_record is None or not await _verify_password_async(
        current_password, credential_record.secret_hash
    ):
        await _count_failed_password(
            db_session,
            user_id=session_identity.user_id,
            state_record=state_record,
            client_context=client_context,
        )
        await db_session.flush()
        raise CurrentPasswordIncorrectError("The current password is wrong")


async def login(
    db_session: AsyncSession,
    login_request: LoginRequest,
    client_context: ClientContext | None,
) -> TokenResponse:
    """Log a person in with phone number and password (ACC-04).

    A wrong phone number and a wrong password give the same answer and cost
    the same time. After `IDENTITY_LOGIN_MAX_FAILED_ATTEMPTS` wrong passwords
    the account refuses logins for `IDENTITY_LOGIN_LOCKOUT_MINUTES` (ID-23).

    Args:
        db_session: Session owned by the entry boundary.
        login_request: Credentials and device.
        client_context: IP address and user agent of the request.

    Returns:
        Access and refresh tokens and the person's memberships.

    Raises:
        InvalidCredentialsError: Wrong phone number or password (401).
        LoginLockedError: The account is in its temporary lockout (423).
        AccountLockedError: An administrator locked the account (403).
        OrganizationInactiveError: Every organization of the person is
            suspended or closed (403).

    Side Effects:
        Updates `user_state`, writes audit rows, creates a session. Failures
        flush their counters and audit rows before raising; the router
        commits them.
    """
    now = utc_now()
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, login_request.phone_number
    )
    if user_record is None:
        await _verify_password_async(login_request.password, DUMMY_PASSWORD_HASH)
        await audit_service.record_account_event(
            db_session,
            user_id=None,
            organization_id=None,
            action=AccessAuditAction.LOGIN_FAILED,
            details={"failure": "UNKNOWN_PHONE"},
            client_context=client_context,
        )
        raise InvalidCredentialsError("Wrong phone number or password")

    state_record = await identity_repository.get_user_state_for_update(
        db_session, user_record.user_id
    )
    if (
        state_record is not None
        and state_record.login_locked_until is not None
        and state_record.login_locked_until > now
    ):
        await audit_service.record_account_event(
            db_session,
            user_id=user_record.user_id,
            organization_id=None,
            action=AccessAuditAction.LOGIN_FAILED,
            details={"failure": "ACCOUNT_LOCKED"},
            client_context=client_context,
        )
        raise LoginLockedError("Too many wrong passwords; try again later")

    credential_record = await identity_repository.find_active_credential(
        db_session, user_record.user_id
    )
    password_ok = await _verify_password_async(
        login_request.password,
        DUMMY_PASSWORD_HASH
        if credential_record is None
        else credential_record.secret_hash,
    )
    if credential_record is None or not password_ok:
        await _count_failed_password(
            db_session,
            user_id=user_record.user_id,
            state_record=state_record,
            client_context=client_context,
        )
        await audit_service.record_account_event(
            db_session,
            user_id=user_record.user_id,
            organization_id=None,
            action=AccessAuditAction.LOGIN_FAILED,
            details={"failure": "WRONG_PASSWORD"},
            client_context=client_context,
        )
        await db_session.flush()
        raise InvalidCredentialsError("Wrong phone number or password")

    if user_record.status != UserStatus.ACTIVE.value:
        await audit_service.record_account_event(
            db_session,
            user_id=user_record.user_id,
            organization_id=None,
            action=AccessAuditAction.LOGIN_FAILED,
            details={"failure": "ACCOUNT_LOCKED"},
            client_context=client_context,
        )
        raise AccountLockedError("This account is locked; contact support")

    return await _start_session_for_login(
        db_session,
        user_record=user_record,
        device=login_request,
        client_context=client_context,
    )


async def sign_up(
    db_session: AsyncSession,
    sign_up_request: SignUpRequest,
    client_context: ClientContext | None,
) -> TokenResponse:
    """Register an individual customer after phone verification (ACC-07, ID-15).

    Creates the user, the password, a personal INDIVIDUAL organization whose
    member holds ORG_ADMIN and DRIVER, and opens a session in it. A number that
    was only invited (an account with no password) is taken over by its owner.

    Args:
        db_session: Session owned by the entry boundary.
        sign_up_request: Phone, code, name, password and device.
        client_context: IP address and user agent of the request.

    Returns:
        Tokens of the new session.

    Raises:
        UserConflictError: The phone number or e-mail is registered.
        WeakPasswordError: The password breaks the policy.
        InvalidOneTimeCodeError: The code is wrong, expired or used.
    """
    _check_password_strength(sign_up_request.password)
    invited_user_record = await identity_repository.find_live_user_by_phone(
        db_session, sign_up_request.phone_number
    )
    if (
        invited_user_record is not None
        and invited_user_record.status != UserStatus.INVITED.value
    ):
        raise UserConflictError("This phone number already has an account")
    if (
        sign_up_request.email is not None
        and await identity_repository.find_live_user_by_email(
            db_session, sign_up_request.email
        )
        is not None
    ):
        raise UserConflictError("This e-mail address already has an account")
    await _consume_one_time_code(
        db_session,
        phone_number=sign_up_request.phone_number,
        purpose=OneTimeCodePurpose.SIGN_UP,
        code=sign_up_request.code,
    )
    now = utc_now()
    secret_hash = await _hash_password_async(sign_up_request.password)
    if invited_user_record is not None:
        # The number was only invited: its owner, who just proved the phone,
        # takes the row over with their own name and e-mail. Their pending
        # invitations stay pending until they accept each one (RV-ID3).
        user_record = invited_user_record
        await set_change_context(
            db_session,
            changed_by=user_record.user_id,
            change_reason="Invited number signed up",
        )
        await identity_repository.apply_user_values(
            db_session,
            user_record,
            {
                "email": sign_up_request.email,
                "full_name": sign_up_request.full_name,
                "status": UserStatus.ACTIVE.value,
            },
        )
    else:
        try:
            user_record = await identity_repository.insert_user(
                db_session,
                {
                    "phone_number": sign_up_request.phone_number,
                    "email": sign_up_request.email,
                    "full_name": sign_up_request.full_name,
                    "status": UserStatus.ACTIVE.value,
                    "created_by": None,
                },
            )
        except IntegrityError as error:
            raise UserConflictError(
                "This phone number already has an account"
            ) from error
    await identity_repository.insert_credential(
        db_session, user_id=user_record.user_id, secret_hash=secret_hash
    )
    organization_record = await identity_repository.insert_organization(
        db_session,
        {
            "is_internal": False,
            "legal_form": OrganizationLegalForm.INDIVIDUAL.value,
            "display_name": sign_up_request.full_name,
            "legal_name": sign_up_request.full_name,
            "status": OrganizationStatus.ACTIVE.value,
        },
    )
    await identity_repository.insert_organization_settings(
        db_session, organization_record.organization_id
    )
    membership_record = await identity_repository.insert_membership(
        db_session,
        {
            "organization_id": organization_record.organization_id,
            "user_id": user_record.user_id,
            "status": MembershipStatus.ACTIVE.value,
            "joined_at": now,
            "created_by": None,
        },
    )
    for role in (UserRole.ORG_ADMIN, UserRole.DRIVER):
        await identity_repository.insert_role_assignment(
            db_session,
            membership_record=membership_record,
            role=role.value,
            granted_by=None,
        )
    return await _start_session_for_login(
        db_session,
        user_record=user_record,
        device=sign_up_request,
        client_context=client_context,
    )


async def accept_invitation(
    db_session: AsyncSession,
    accept_request: InvitationAcceptRequest,
    client_context: ClientContext | None,
) -> TokenResponse:
    """Let an invited person choose a password and join (ACC-09, ACC-10).

    Args:
        db_session: Session owned by the entry boundary.
        accept_request: Phone, invitation code, new password and device.
        client_context: IP address and user agent of the request.

    Returns:
        Tokens of the new session.

    Raises:
        WeakPasswordError: The password breaks the policy.
        InvalidOneTimeCodeError: The code is wrong, expired or used; an
            unknown phone number gets the same answer as a wrong code.

    Side Effects:
        Activates the user and the pending membership of the organization the
        code was sent for; invitations from other organizations stay pending
        until the person accepts each one (RV-ID3).
    """
    _check_password_strength(accept_request.password)
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, accept_request.phone_number
    )
    if user_record is None or user_record.status != UserStatus.INVITED.value:
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    code_record = await _consume_one_time_code(
        db_session,
        phone_number=accept_request.phone_number,
        purpose=OneTimeCodePurpose.INVITE,
        code=accept_request.code,
        user_id=user_record.user_id,
    )
    invited_memberships = [
        membership_record
        for membership_record in await identity_repository.list_invited_memberships_by_user(
            db_session, user_record.user_id
        )
        if membership_record.organization_id == code_record.organization_id
    ]
    if not invited_memberships:
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    now = utc_now()
    secret_hash = await _hash_password_async(accept_request.password)
    await identity_repository.insert_credential(
        db_session, user_id=user_record.user_id, secret_hash=secret_hash
    )
    await set_change_context(
        db_session,
        changed_by=user_record.user_id,
        change_reason="Invitation accepted",
    )
    await identity_repository.apply_user_values(
        db_session, user_record, {"status": UserStatus.ACTIVE.value}
    )
    for membership_record in invited_memberships:
        await set_change_context(
            db_session,
            changed_by=user_record.user_id,
            change_reason="Invitation accepted",
        )
        await identity_repository.apply_membership_values(
            db_session,
            membership_record,
            {"status": MembershipStatus.ACTIVE.value, "joined_at": now},
        )
    return await _start_session_for_login(
        db_session,
        user_record=user_record,
        device=accept_request,
        client_context=client_context,
    )


async def refresh_session(
    db_session: AsyncSession, refresh_request: RefreshRequest
) -> TokenResponse:
    """Replace a refresh token with a new pair and extend the session (ACC-04).

    Args:
        db_session: Session owned by the entry boundary.
        refresh_request: The refresh token.

    Returns:
        A new access token and a new refresh token; the old one stops working.

    Raises:
        SessionInvalidError: The token is unknown, was replaced, or the
            session expired, or the account is no longer active.
    """
    now = utc_now()
    session_record = await identity_repository.find_session_by_refresh_hash(
        db_session, hash_refresh_token(refresh_request.refresh_token)
    )
    if session_record is None or session_record.expires_at <= now:
        raise SessionInvalidError("The session is no longer valid; log in again")
    user_record = await identity_repository.get_user(db_session, session_record.user_id)
    if (
        user_record is None
        or user_record.deleted_at is not None
        or user_record.status != UserStatus.ACTIVE.value
    ):
        raise SessionInvalidError("The session is no longer valid; log in again")
    is_internal = await _holds_internal_membership(db_session, user_record.user_id)
    new_refresh_token = generate_refresh_token()
    session_record.refresh_token_hash = hash_refresh_token(new_refresh_token)
    session_record.last_used_at = now
    session_record.expires_at = now + session_lifetime(
        session_record.platform, is_internal
    )
    await db_session.flush()
    return await _build_token_response(db_session, session_record, new_refresh_token)


async def authenticate_session(
    db_session: AsyncSession, access_token: str | None
) -> SessionIdentity:
    """Validate a bearer token and the session behind it (ACC-14).

    The token is checked for signature and expiry, then the session row must
    exist (so logout is immediate) and the account must be active. Activity
    times are refreshed at most every 5 minutes (ID-24).

    Args:
        db_session: Session owned by the entry boundary.
        access_token: The bearer token of the request, if any.

    Returns:
        The session identity (organization may still be unset).

    Raises:
        SessionInvalidError: If anything is missing, forged, expired or ended.
    """
    if not access_token:
        raise SessionInvalidError("Authentication is required")
    session_id = read_access_token(access_token)
    if session_id is None:
        raise SessionInvalidError("The access token is not valid")
    now = utc_now()
    session_record = await identity_repository.get_session(db_session, session_id)
    if session_record is None or session_record.expires_at <= now:
        raise SessionInvalidError("The session has ended; log in again")
    user_record = await identity_repository.get_user(db_session, session_record.user_id)
    if (
        user_record is None
        or user_record.deleted_at is not None
        or user_record.status != UserStatus.ACTIVE.value
    ):
        raise SessionInvalidError("The session has ended; log in again")
    if now - session_record.last_used_at >= ACTIVITY_WRITE_INTERVAL:
        session_record.last_used_at = now
        session_record.expires_at = now + session_lifetime(
            session_record.platform,
            await _holds_internal_membership(db_session, session_record.user_id),
        )
        state_record = await identity_repository.get_user_state(
            db_session, session_record.user_id
        )
        if state_record is not None:
            state_record.last_active_at = now
        await db_session.flush()
    return SessionIdentity(
        user_id=session_record.user_id,
        session_id=session_record.user_session_id,
        organization_id=session_record.organization_id,
    )


async def resolve_principal(
    db_session: AsyncSession, session_identity: SessionIdentity
) -> Principal:
    """Build the `Principal` of a request from its session (ACC-14, ACC-15).

    Args:
        db_session: Current database session.
        session_identity: The validated session.

    Returns:
        The caller with the roles of their active membership in the session's
        organization.

    Raises:
        OrganizationNotSelectedError: The session has no organization yet.
        AccessDeniedError: The person has no active membership there.
        OrganizationInactiveError: The organization is suspended or closed.
    """
    if session_identity.organization_id is None:
        raise OrganizationNotSelectedError("Pick an organization first")
    membership_record = await identity_repository.find_live_membership(
        db_session,
        organization_id=session_identity.organization_id,
        user_id=session_identity.user_id,
    )
    if (
        membership_record is None
        or membership_record.status != MembershipStatus.ACTIVE.value
    ):
        raise AccessDeniedError("You are not an active member of this organization")
    organization_record = await identity_repository.get_organization(
        db_session, session_identity.organization_id
    )
    if (
        organization_record is None
        or organization_record.status != OrganizationStatus.ACTIVE.value
        or organization_record.deleted_at is not None
    ):
        raise OrganizationInactiveError("This organization is suspended or closed")
    role_values = await identity_repository.list_active_roles_by_membership(
        db_session, membership_record.membership_id
    )
    return Principal(
        user_id=session_identity.user_id,
        membership_id=membership_record.membership_id,
        organization_id=organization_record.organization_id,
        session_id=session_identity.session_id,
        roles=frozenset(UserRole(role_value) for role_value in role_values),
        is_internal=organization_record.is_internal,
    )


async def get_me(
    db_session: AsyncSession, session_identity: SessionIdentity
) -> MeResponse:
    """Describe the caller: account, current organization, roles, features.

    Args:
        db_session: Current database session.
        session_identity: The validated session.

    Returns:
        The account, the memberships and, when an organization is picked and
        the membership is active, the roles and features there.
    """
    user_record = await identity_repository.get_user(
        db_session, session_identity.user_id
    )
    if user_record is None:
        raise UserNotFoundError("The user does not exist")
    summaries = await build_membership_summaries(db_session, user_record.user_id)
    roles: list[UserRole] = []
    is_internal = False
    for summary in summaries:
        if (
            summary.organization_id == session_identity.organization_id
            and summary.status == MembershipStatus.ACTIVE.value
        ):
            roles = summary.roles
            is_internal = summary.is_internal
    return MeResponse(
        user=to_user_response(user_record),
        session_id=session_identity.session_id,
        organization_id=session_identity.organization_id,
        is_internal=is_internal,
        roles=roles,
        features=sorted(features_of_roles(frozenset(roles))),
        memberships=summaries,
    )


async def switch_organization(
    db_session: AsyncSession, session_identity: SessionIdentity, organization_id: UUID
) -> MeResponse:
    """Make the session act for another organization of the person (ID-09).

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        organization_id: The organization to switch to.

    Returns:
        The caller's description in the new organization.

    Raises:
        MembershipNotFoundError: The person is not a member there.
        AccessDeniedError: The membership is not active.
        OrganizationInactiveError: The organization is suspended or closed.
    """
    membership_record = await identity_repository.find_live_membership(
        db_session, organization_id=organization_id, user_id=session_identity.user_id
    )
    if membership_record is None:
        raise MembershipNotFoundError("You are not a member of this organization")
    if membership_record.status != MembershipStatus.ACTIVE.value:
        raise AccessDeniedError("Your membership in this organization is not active")
    organization_record = await identity_repository.get_organization(
        db_session, organization_id
    )
    if (
        organization_record is None
        or organization_record.status != OrganizationStatus.ACTIVE.value
        or organization_record.deleted_at is not None
    ):
        raise OrganizationInactiveError("This organization is suspended or closed")
    session_record = await identity_repository.get_session(
        db_session, session_identity.session_id
    )
    if session_record is None:
        raise SessionInvalidError("The session has ended; log in again")
    session_record.organization_id = organization_id
    # Recomputed from all the person's memberships, so a switch never stretches
    # a staff session (RV-ID10).
    session_record.expires_at = utc_now() + session_lifetime(
        session_record.platform,
        await _holds_internal_membership(db_session, session_identity.user_id),
    )
    state_record = await identity_repository.get_user_state(
        db_session, session_identity.user_id
    )
    if state_record is not None:
        state_record.last_organization_id = organization_id
    await db_session.flush()
    return await get_me(
        db_session,
        SessionIdentity(
            user_id=session_identity.user_id,
            session_id=session_identity.session_id,
            organization_id=organization_id,
        ),
    )


async def logout(
    db_session: AsyncSession,
    session_identity: SessionIdentity,
    *,
    all_devices: bool,
    client_context: ClientContext | None,
) -> None:
    """End this session, or every session of the person (ACC-04).

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        all_devices: End every session of the person.
        client_context: IP address and user agent of the request.

    Side Effects:
        Deletes the session row(s) (so their push tokens go too) and writes
        `LOGOUT`.
    """
    if all_devices:
        await identity_repository.delete_sessions_by_user(
            db_session, session_identity.user_id
        )
    else:
        session_record = await identity_repository.get_session(
            db_session, session_identity.session_id
        )
        if session_record is not None:
            await identity_repository.delete_session(db_session, session_record)
    await audit_service.record_account_event(
        db_session,
        user_id=session_identity.user_id,
        organization_id=session_identity.organization_id,
        action=AccessAuditAction.LOGOUT,
        details=None,
        client_context=client_context,
    )


async def list_sessions(
    db_session: AsyncSession, session_identity: SessionIdentity
) -> list[SessionResponse]:
    """List the caller's live sessions, the "my devices" screen (ACC-16).

    Args:
        db_session: Current database session.
        session_identity: The validated session.

    Returns:
        The caller's unexpired sessions, most recently used first.
    """
    now = utc_now()
    session_records = await identity_repository.list_sessions_by_user(
        db_session, session_identity.user_id
    )
    return [
        SessionResponse(
            user_session_id=session_record.user_session_id,
            platform=session_record.platform,
            app_version=session_record.app_version,
            device_label=session_record.device_label,
            has_push_token=session_record.push_token is not None,
            organization_id=session_record.organization_id,
            created_at=session_record.created_at,
            last_used_at=session_record.last_used_at,
            expires_at=session_record.expires_at,
            is_current=session_record.user_session_id == session_identity.session_id,
        )
        for session_record in session_records
        if session_record.expires_at > now
    ]


async def end_session(
    db_session: AsyncSession, session_identity: SessionIdentity, session_id: UUID
) -> None:
    """Log out one of the caller's own devices (ACC-16).

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        session_id: The session to end.

    Raises:
        SessionNotFoundError: The session does not exist or is not the
            caller's.
    """
    session_record = await identity_repository.get_session(db_session, session_id)
    if session_record is None or session_record.user_id != session_identity.user_id:
        raise SessionNotFoundError("The session does not exist")
    await identity_repository.delete_session(db_session, session_record)


async def set_push_token(
    db_session: AsyncSession, session_identity: SessionIdentity, push_token: str | None
) -> None:
    """Register, replace or remove the Firebase token of this device (ACC-16).

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        push_token: The new token, or `None` to remove it.

    Raises:
        SessionNotFoundError: The session ended.

    Side Effects:
        A token held by another session (a shared phone) is taken from it.
    """
    session_record = await identity_repository.get_session(
        db_session, session_identity.session_id
    )
    if session_record is None:
        raise SessionNotFoundError("The session does not exist")
    if push_token is not None:
        await identity_repository.clear_push_token(db_session, push_token)
    session_record.push_token = push_token
    await db_session.flush()


# ---------------------------------------------------------------------------
# Password and phone-number changes, profile
# ---------------------------------------------------------------------------


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
    _check_password_strength(reset_request.new_password)
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, reset_request.phone_number
    )
    if user_record is None:
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    await _consume_one_time_code(
        db_session,
        phone_number=reset_request.phone_number,
        purpose=OneTimeCodePurpose.PASSWORD_RESET,
        code=reset_request.code,
        user_id=user_record.user_id,
    )
    if user_record.status != UserStatus.ACTIVE.value:
        raise AccountLockedError("This account is locked; contact support")
    await set_password(db_session, user_record.user_id, reset_request.new_password)
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
    await _require_current_password(
        db_session, session_identity, change_request.current_password, client_context
    )
    await set_password(
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
    await _require_current_password(
        db_session, session_identity, change_request.current_password, client_context
    )
    if (
        await identity_repository.find_live_user_by_phone(
            db_session, change_request.new_phone_number
        )
        is not None
    ):
        raise UserConflictError("This phone number already has an account")
    code_record = await issue_one_time_code(
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
    code_record = await identity_repository.find_latest_one_time_code_for_user(
        db_session,
        user_id=session_identity.user_id,
        purpose=OneTimeCodePurpose.PHONE_CHANGE.value,
    )
    if code_record is None:
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    await _consume_one_time_code(
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


# ---------------------------------------------------------------------------
# Account administration (internal administrators)
# ---------------------------------------------------------------------------


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
        UserConflictError: The account is the caller's own or already locked.
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
