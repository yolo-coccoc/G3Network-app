"""FastAPI routers of the identity domain: authentication and user accounts.

`auth_router` (mounted at ``/api/v1/auth``) serves sign-up, invitation
acceptance, login, refresh, logout, the organization picker, password and
phone-number changes, "my devices" and push-token registration. `users_router`
(``/api/v1/users``) is the account administration for our own staff.

A browser (``WEB``) session receives its refresh token in an ``HttpOnly``
cookie, never in a body (IS-13): the service says when (`IssuedSessionTokens`),
these endpoints set, read and clear the cookie.

Domain exceptions are not caught here (`app/api/main.py` maps the shared bases
to HTTP codes) except for the commit described at `persist_recorded_failure`.
Status codes chosen: 401 wrong credentials or ended session, 403 locked
account or inactive organization, 423 temporary lockout after repeated wrong
passwords, 429 code cooldown or daily limit.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.account_service as account_service
import app.domains.identity.one_time_code_service as one_time_code_service
import app.domains.identity.session_service as login_session_service
from app.domains.identity.dependencies import (
    get_client_context,
    get_current_principal,
    get_session_identity,
)
from app.domains.identity.exceptions import FailureRecordedError
from app.domains.identity.schemas import (
    InvitationAcceptRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    OneTimeCodeSendRequest,
    OneTimeCodeSendResponse,
    PasswordChangeRequest,
    PasswordResetRequest,
    PhoneChangeConfirmRequest,
    PhoneChangeRequest,
    ProfileUpdateRequest,
    PushTokenRequest,
    RefreshRequest,
    SessionResponse,
    SignUpRequest,
    SwitchOrganizationRequest,
    TokenResponse,
    UserListResponse,
    UserLockRequest,
    UserResponse,
)
from app.domains.identity.types import (
    ClientContext,
    Principal,
    SessionIdentity,
    UserStatus,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

auth_router = APIRouter(tags=["identity-auth"])
users_router = APIRouter(tags=["identity-users"])

# The browser's refresh-token cookie (IS-13, web-portal-scaffold.md D4).
# HttpOnly: page script can never read it, so an injected script cannot steal
# the session. Secure: HTTPS only; Chrome and Firefox also accept it on
# http://localhost, Safari does not, so develop the portal in Chrome or
# Firefox. SameSite=Strict: a page of another site cannot make the
# browser send it. The path is where `auth_router` is mounted
# (`app/api/main.py`), so the cookie travels only to the auth routes.
REFRESH_COOKIE_NAME = "g3_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _build_token_response_with_cookie(
    response: Response, issued_tokens: login_session_service.IssuedSessionTokens
) -> TokenResponse:
    """Set the browser's refresh cookie when the service issued one.

    Args:
        response: The response FastAPI sends; the cookie is added to it.
        issued_tokens: What the service issued.

    Returns:
        The response body (without a refresh token for a browser session).
    """
    if issued_tokens.cookie_refresh_token is not None:
        response.set_cookie(
            REFRESH_COOKIE_NAME,
            issued_tokens.cookie_refresh_token,
            max_age=issued_tokens.cookie_max_age_seconds,
            path=REFRESH_COOKIE_PATH,
            secure=True,
            httponly=True,
            samesite="strict",
        )
    return issued_tokens.token_response


@asynccontextmanager
async def persist_recorded_failure(db_session: AsyncSession) -> AsyncIterator[None]:
    """Commit what a failing request recorded, then let the error propagate.

    `get_db` rolls back when an endpoint raises, which would discard a
    failed-login counter, a lockout, a wrong-code attempt and their audit rows.
    Services flush those before raising a `FailureRecordedError`; this
    boundary-level helper commits them, so the counters survive the 4xx.

    Args:
        db_session: The session owned by the HTTP boundary.

    Yields:
        Control to the endpoint body.

    Raises:
        FailureRecordedError: Re-raised unchanged after the commit.
    """
    try:
        yield
    except FailureRecordedError:
        await db_session.commit()
        raise


@auth_router.post(
    "/otp/send",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OneTimeCodeSendResponse,
    summary="Send an SMS one-time code",
    description=(
        "Sign-up, invitation or password-reset code. Rate limited per phone "
        "(429). The answer is the same for an unknown phone on a reset or "
        "invitation."
    ),
)
async def send_one_time_code_endpoint(
    send_request: OneTimeCodeSendRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OneTimeCodeSendResponse:
    """Send a one-time code.

    Args:
        send_request: Phone number and purpose.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        When the code expires.
    """
    return await one_time_code_service.send_one_time_code(db_session, send_request)


@auth_router.post(
    "/sign-up",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenResponse,
    summary="Register an individual customer",
)
async def sign_up_endpoint(
    sign_up_request: SignUpRequest,
    response: Response,
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TokenResponse:
    """Register an individual with a phone code and open a session.

    Args:
        sign_up_request: Phone, code, name, password and device.
        response: The outgoing response (a browser's refresh cookie is set on it).
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Tokens of the new session.
    """
    async with persist_recorded_failure(db_session):
        issued_tokens = await login_session_service.sign_up(
            db_session, sign_up_request, client_context
        )
    return _build_token_response_with_cookie(response, issued_tokens)


@auth_router.post(
    "/invitations/accept",
    response_model=TokenResponse,
    summary="Accept an invitation and set a password",
)
async def accept_invitation_endpoint(
    accept_request: InvitationAcceptRequest,
    response: Response,
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TokenResponse:
    """Accept an invitation with the SMS code and choose a password.

    Args:
        accept_request: Phone, code, new password and device.
        response: The outgoing response (a browser's refresh cookie is set on it).
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Tokens of the new session.
    """
    async with persist_recorded_failure(db_session):
        issued_tokens = await login_session_service.accept_invitation(
            db_session, accept_request, client_context
        )
    return _build_token_response_with_cookie(response, issued_tokens)


@auth_router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in with phone number and password",
    description=(
        "401 wrong phone or password; 423 temporary lockout after repeated "
        "failures; 403 locked account or suspended/closed organization."
    ),
)
async def login_endpoint(
    login_request: LoginRequest,
    response: Response,
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TokenResponse:
    """Log in and open a session.

    Args:
        login_request: Credentials and device.
        response: The outgoing response (a browser's refresh cookie is set on it).
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The access token, the refresh token (phone apps only) and the
        person's memberships.
    """
    async with persist_recorded_failure(db_session):
        issued_tokens = await login_session_service.login(
            db_session, login_request, client_context
        )
    return _build_token_response_with_cookie(response, issued_tokens)


@auth_router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new pair",
    description=(
        "A phone app sends its refresh token in the body; a browser sends no "
        "body and its HttpOnly refresh cookie is used and replaced. 401 when "
        "neither is present or the token is no longer valid."
    ),
)
async def refresh_session_endpoint(
    response: Response,
    refresh_request: RefreshRequest | None = None,
    refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TokenResponse:
    """Refresh a session from the body's token, or else the browser's cookie.

    Args:
        response: The outgoing response (a browser's new cookie is set on it).
        refresh_request: The phone app's refresh token; absent for a browser.
        refresh_cookie: The browser's refresh cookie, if it sent one.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A new token pair; the old refresh token stops working.
    """
    body_token = None if refresh_request is None else refresh_request.refresh_token
    issued_tokens = await login_session_service.refresh_session(
        db_session, body_token if body_token is not None else refresh_cookie
    )
    return _build_token_response_with_cookie(response, issued_tokens)


@auth_router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out this device or all devices",
)
async def logout_endpoint(
    logout_request: LogoutRequest,
    session_identity: SessionIdentity = Depends(get_session_identity),
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """End the session(s) of the caller.

    Args:
        logout_request: Whether to end every session.
        session_identity: The validated session.
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        An empty 204 response that also clears a browser's refresh cookie
        (harmless for a phone app, which has none).
    """
    await login_session_service.logout(
        db_session,
        session_identity,
        all_devices=logout_request.all_devices,
        client_context=client_context,
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@auth_router.get("/me", response_model=MeResponse, summary="Who am I")
async def get_me_endpoint(
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MeResponse:
    """Describe the caller: account, memberships, roles and features.

    Args:
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The caller's description.
    """
    return await login_session_service.get_me(db_session, session_identity)


@auth_router.patch("/me", response_model=UserResponse, summary="Edit my profile")
async def update_profile_endpoint(
    update_request: ProfileUpdateRequest,
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> UserResponse:
    """Change the caller's own name or e-mail.

    Args:
        update_request: New values; a null field is left unchanged.
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated account.
    """
    return await account_service.update_profile(
        db_session, session_identity, update_request
    )


@auth_router.post(
    "/organization",
    response_model=MeResponse,
    summary="Pick the organization this session acts for",
)
async def switch_organization_endpoint(
    switch_request: SwitchOrganizationRequest,
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> MeResponse:
    """Switch the session to another organization without a new login.

    Args:
        switch_request: The organization.
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The caller's description in that organization.
    """
    return await login_session_service.switch_organization(
        db_session, session_identity, switch_request.organization_id
    )


@auth_router.get(
    "/sessions", response_model=list[SessionResponse], summary="My logged-in devices"
)
async def list_sessions_endpoint(
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> list[SessionResponse]:
    """List the caller's live sessions.

    Args:
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The sessions, most recently used first.
    """
    return await login_session_service.list_sessions(db_session, session_identity)


@auth_router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out one of my devices",
)
async def end_session_endpoint(
    session_id: UUID,
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """End one of the caller's own sessions.

    Args:
        session_id: The session to end.
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        An empty 204 response.
    """
    await login_session_service.end_session(db_session, session_identity, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.put(
    "/session/push-token",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Register this device's push token",
)
async def set_push_token_endpoint(
    token_request: PushTokenRequest,
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Register or replace the Firebase token of this session.

    Args:
        token_request: The token.
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        An empty 204 response.
    """
    await login_session_service.set_push_token(
        db_session, session_identity, token_request.push_token
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.delete(
    "/session/push-token",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove this device's push token",
)
async def clear_push_token_endpoint(
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Stop push notifications to this session.

    Args:
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        An empty 204 response.
    """
    await login_session_service.set_push_token(db_session, session_identity, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.post(
    "/password/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password with an SMS code",
)
async def reset_password_endpoint(
    reset_request: PasswordResetRequest,
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Reset a forgotten password; clears the lockout and ends all sessions.

    Args:
        reset_request: Phone, code and new password.
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        An empty 204 response.
    """
    async with persist_recorded_failure(db_session):
        await account_service.reset_password(db_session, reset_request, client_context)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.post(
    "/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change my password",
)
async def change_password_endpoint(
    change_request: PasswordChangeRequest,
    session_identity: SessionIdentity = Depends(get_session_identity),
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Change the caller's password; the caller's other sessions end.

    Args:
        change_request: Current and new password.
        session_identity: The validated session.
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        An empty 204 response.
    """
    async with persist_recorded_failure(db_session):
        await account_service.change_password(
            db_session, session_identity, change_request, client_context
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.post(
    "/phone/change",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OneTimeCodeSendResponse,
    summary="Start a phone-number change",
)
async def request_phone_change_endpoint(
    change_request: PhoneChangeRequest,
    session_identity: SessionIdentity = Depends(get_session_identity),
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> OneTimeCodeSendResponse:
    """Send a code to the new phone number, after checking the password.

    Args:
        change_request: The new phone number and the current password.
        session_identity: The validated session.
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        When the code expires.
    """
    async with persist_recorded_failure(db_session):
        return await account_service.request_phone_change(
            db_session, session_identity, change_request, client_context
        )


@auth_router.post(
    "/phone/change/confirm",
    response_model=UserResponse,
    summary="Finish a phone-number change",
)
async def confirm_phone_change_endpoint(
    confirm_request: PhoneChangeConfirmRequest,
    session_identity: SessionIdentity = Depends(get_session_identity),
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> UserResponse:
    """Change the phone number with the code sent to the new one.

    Args:
        confirm_request: The code.
        session_identity: The validated session.
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated account.
    """
    async with persist_recorded_failure(db_session):
        return await account_service.confirm_phone_change(
            db_session, session_identity, confirm_request, client_context
        )


@users_router.get(
    "/",
    response_model=UserListResponse,
    summary="List user accounts (our administrators)",
)
async def list_users_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    search_text: str | None = Query(
        None,
        alias="q",
        min_length=1,
        max_length=100,
        description="Name, phone or e-mail",
    ),
    user_status: UserStatus | None = Query(None, alias="status"),
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> UserListResponse:
    """List accounts across all organizations.

    Args:
        page: Page number.
        page_size: Rows per page.
        search_text: Name, phone number or e-mail substring.
        user_status: Only this account status.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of accounts.
    """
    return await account_service.list_users(
        db_session,
        principal,
        page=page,
        page_size=page_size,
        search_text=search_text,
        status=user_status,
    )


@users_router.get("/{user_id}", response_model=UserResponse, summary="Get an account")
async def get_user_endpoint(
    user_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> UserResponse:
    """Read one account.

    Args:
        user_id: The account.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The account.
    """
    return await account_service.get_user(db_session, principal, user_id)


@users_router.post(
    "/{user_id}/lock",
    response_model=UserResponse,
    summary="Lock a whole account across every organization",
)
async def lock_user_endpoint(
    user_id: UUID,
    lock_request: UserLockRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> UserResponse:
    """Lock an account and end its sessions.

    Args:
        user_id: The account.
        lock_request: The reason.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The locked account.
    """
    return await account_service.lock_user(
        db_session, principal, user_id, lock_request.reason
    )


@users_router.post(
    "/{user_id}/unlock", response_model=UserResponse, summary="Unlock an account"
)
async def unlock_user_endpoint(
    user_id: UUID,
    unlock_request: UserLockRequest,
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> UserResponse:
    """Unlock an account.

    Args:
        user_id: The account.
        unlock_request: The reason.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The unlocked account.
    """
    return await account_service.unlock_user(
        db_session, principal, user_id, unlock_request.reason
    )
