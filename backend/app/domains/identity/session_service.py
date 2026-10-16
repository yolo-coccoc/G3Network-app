"""Login sessions and tokens (ACC-03, ACC-04, ACC-05, ID-36).

Sign-up, invitation acceptance and login create a session and its access
and refresh tokens; refresh rotates them; every request authenticates its
token and resolves the caller's `Principal` for the organization they chose
(switch organization, `me`); logout, the session list and ending one
session; the device's push token. Internal module: the domain's routers and
``dependencies.py`` call it; other domains use ``service.py``.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.account_service as account_service
import app.domains.identity.audit_service as audit_service
import app.domains.identity.one_time_code_service as one_time_code_service
import app.domains.identity.passwords as identity_passwords
import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import (
    AccessDeniedError,
    AccountLockedError,
    InvalidCredentialsError,
    InvalidOneTimeCodeError,
    LoginLockedError,
    MembershipNotFoundError,
    OrganizationInactiveError,
    OrganizationNotSelectedError,
    SessionInvalidError,
    SessionNotFoundError,
    UserConflictError,
    UserNotFoundError,
)
from app.domains.identity.models import (
    OrganizationModel,
    UserModel,
    UserSessionModel,
)
from app.domains.identity.providers import get_sms_sender
from app.domains.identity.schemas import (
    DeviceFields,
    InvitationAcceptRequest,
    LoginRequest,
    MembershipSummaryResponse,
    MeResponse,
    SessionResponse,
    SignUpRequest,
    TokenResponse,
)
from app.domains.identity.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    normalize_phone_number,
    read_access_token,
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
from app.libs.db.history import set_change_context

# ID-24: `last_used_at` and `last_active_at` are written at most this often.

logger = logging.getLogger(__name__)

ACTIVITY_WRITE_INTERVAL = timedelta(minutes=5)


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


# Lives here, not in `types.py`: it carries the Pydantic `TokenResponse`, and
# `types.py` must not depend on Pydantic.
@dataclass(frozen=True)
class IssuedSessionTokens:
    """What a login, sign-up, invitation acceptance or refresh hands out (IS-13).

    A phone-app session gets both tokens in the response body. A browser
    (``WEB``) session never does: its refresh token goes into an ``HttpOnly``
    cookie that page script cannot read, so an injected script cannot steal
    it; the router sets that cookie from the two ``cookie_*`` fields.

    Attributes:
        token_response: The response body; ``refresh_token`` is ``None`` for a
            ``WEB`` session.
        cookie_refresh_token: The plain refresh token for the browser's cookie;
            ``None`` for a phone-app session.
        cookie_max_age_seconds: How long the cookie lives: until the session
            expires (its idle lifetime, pushed forward by every refresh);
            ``None`` when there is no cookie.
    """

    token_response: TokenResponse
    cookie_refresh_token: str | None
    cookie_max_age_seconds: int | None


async def _build_issued_tokens(
    db_session: AsyncSession,
    session_record: UserSessionModel,
    refresh_token: str,
) -> IssuedSessionTokens:
    """Sign an access token for a session and assemble what the client receives.

    The session's stored platform decides where the refresh token goes, so a
    browser session can never get it in a body, whatever the request says.

    Args:
        db_session: Current database session.
        session_record: The session.
        refresh_token: The plain refresh token to hand to the client.

    Returns:
        The response body (access token, session ID, current organization,
        memberships, and the refresh token for a phone app) and, for a
        browser session, the refresh token and lifetime of its cookie.
    """
    now = utc_now()
    ttl = timedelta(seconds=settings.IDENTITY_ACCESS_TOKEN_TTL_SECONDS)
    access_token = create_access_token(session_record.user_session_id, now + ttl)
    is_browser = session_record.platform == SessionPlatform.WEB.value
    token_response = TokenResponse(
        access_token=access_token,
        expires_in=int(ttl.total_seconds()),
        refresh_token=None if is_browser else refresh_token,
        session_id=session_record.user_session_id,
        organization_id=session_record.organization_id,
        memberships=await build_membership_summaries(
            db_session, session_record.user_id
        ),
    )
    if not is_browser:
        return IssuedSessionTokens(token_response, None, None)
    max_age_seconds = int((session_record.expires_at - now).total_seconds())
    return IssuedSessionTokens(token_response, refresh_token, max(max_age_seconds, 0))


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
) -> IssuedSessionTokens:
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
        The issued tokens: the response body and, for a browser session,
        the refresh cookie (IS-13).

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
    return await _build_issued_tokens(db_session, session_record, refresh_token)


async def login(
    db_session: AsyncSession,
    login_request: LoginRequest,
    client_context: ClientContext | None,
) -> IssuedSessionTokens:
    """Log a person in with phone number and password (ACC-04).

    A wrong phone number and a wrong password give the same answer and cost
    the same time. After `IDENTITY_LOGIN_MAX_FAILED_ATTEMPTS` wrong passwords
    the account refuses logins for `IDENTITY_LOGIN_LOCKOUT_MINUTES` (ID-23).

    Args:
        db_session: Session owned by the entry boundary.
        login_request: Credentials and device.
        client_context: IP address and user agent of the request.

    Returns:
        The issued tokens and the person's memberships (the refresh token
        goes to a cookie for a browser session, IS-13).

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
        await identity_passwords.verify_password_async(
            login_request.password, DUMMY_PASSWORD_HASH
        )
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
    password_ok = await identity_passwords.verify_password_async(
        login_request.password,
        DUMMY_PASSWORD_HASH
        if credential_record is None
        else credential_record.secret_hash,
    )
    if credential_record is None or not password_ok:
        await identity_passwords.count_failed_password(
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
) -> IssuedSessionTokens:
    """Register an individual customer after phone verification (ACC-07, ID-15).

    Creates the user, the password, a personal INDIVIDUAL organization whose
    member holds ORG_ADMIN and DRIVER, and opens a session in it. A number that
    was only invited (an account with no password) is taken over by its owner.

    Args:
        db_session: Session owned by the entry boundary.
        sign_up_request: Phone, code, name, password and device.
        client_context: IP address and user agent of the request.

    Returns:
        The issued tokens of the new session (the refresh token goes to a
        cookie for a browser session, IS-13).

    Raises:
        UserConflictError: The phone number or e-mail is registered.
        WeakPasswordError: The password breaks the policy.
        InvalidOneTimeCodeError: The code is wrong, expired or used.
    """
    identity_passwords.check_password_strength(sign_up_request.password)
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
    await one_time_code_service.consume_one_time_code(
        db_session,
        phone_number=sign_up_request.phone_number,
        purpose=OneTimeCodePurpose.SIGN_UP,
        code=sign_up_request.code,
    )
    now = utc_now()
    secret_hash = await identity_passwords.hash_password_async(sign_up_request.password)
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
) -> IssuedSessionTokens:
    """Let an invited person choose a password and join (ACC-09, ACC-10).

    Args:
        db_session: Session owned by the entry boundary.
        accept_request: Phone, invitation code, new password and device.
        client_context: IP address and user agent of the request.

    Returns:
        The issued tokens of the new session (the refresh token goes to a
        cookie for a browser session, IS-13).

    Raises:
        WeakPasswordError: The password breaks the policy.
        InvalidOneTimeCodeError: The code is wrong, expired or used; an
            unknown phone number gets the same answer as a wrong code.

    Side Effects:
        Activates the user and the pending membership of the organization the
        code was sent for; invitations from other organizations stay pending
        until the person accepts each one (RV-ID3).
    """
    identity_passwords.check_password_strength(accept_request.password)
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, accept_request.phone_number
    )
    if user_record is None or user_record.status != UserStatus.INVITED.value:
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    code_record = await one_time_code_service.consume_one_time_code(
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
    secret_hash = await identity_passwords.hash_password_async(accept_request.password)
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
    db_session: AsyncSession, refresh_token: str | None
) -> IssuedSessionTokens:
    """Replace a refresh token with a new pair and extend the session (ACC-04).

    Args:
        db_session: Session owned by the entry boundary.
        refresh_token: The token from the phone app's body or the browser's
            cookie (the router picks it); ``None`` when the client sent none.

    Returns:
        A new access token and a new refresh token (in the body for a phone
        app, for the cookie for a browser); the old one stops working.

    Raises:
        SessionInvalidError: No token was sent, the token is unknown, was
            replaced, or the session expired, or the account is no longer
            active.
    """
    if refresh_token is None:
        raise SessionInvalidError("No refresh token was sent; log in again")
    now = utc_now()
    session_record = await identity_repository.find_session_by_refresh_hash(
        db_session, hash_refresh_token(refresh_token)
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
    is_internal = await account_service.holds_internal_membership(
        db_session, user_record.user_id
    )
    new_refresh_token = generate_refresh_token()
    session_record.refresh_token_hash = hash_refresh_token(new_refresh_token)
    session_record.last_used_at = now
    session_record.expires_at = now + session_lifetime(
        session_record.platform, is_internal
    )
    await db_session.flush()
    return await _build_issued_tokens(db_session, session_record, new_refresh_token)


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
            await account_service.holds_internal_membership(
                db_session, session_record.user_id
            ),
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
        user=account_service.to_user_response(user_record),
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
        await account_service.holds_internal_membership(
            db_session, session_identity.user_id
        ),
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
