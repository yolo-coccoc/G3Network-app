"""FastAPI routers of the identity domain: authentication and user accounts.

`auth_router` (mounted at ``/api/v1/auth``) serves sign-up, invitation
acceptance, login, refresh, logout, the organization picker, password and
phone-number changes, "my devices" and push-token registration. `users_router`
(``/api/v1/users``) is the account administration for our own staff.

Domain exceptions are not caught here (`app/api/main.py` maps the shared bases
to HTTP codes) except for the commit described at `persist_recorded_failure`.
Status codes chosen: 401 wrong credentials or ended session, 403 locked
account or inactive organization, 423 temporary lockout after repeated wrong
passwords, 429 code cooldown or daily limit.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.account_service as account_service
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
    return await account_service.send_one_time_code(db_session, send_request)


@auth_router.post(
    "/sign-up",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenResponse,
    summary="Register an individual customer",
)
async def sign_up_endpoint(
    sign_up_request: SignUpRequest,
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TokenResponse:
    """Register an individual with a phone code and open a session.

    Args:
        sign_up_request: Phone, code, name, password and device.
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Tokens of the new session.
    """
    async with persist_recorded_failure(db_session):
        return await account_service.sign_up(
            db_session, sign_up_request, client_context
        )


@auth_router.post(
    "/invitations/accept",
    response_model=TokenResponse,
    summary="Accept an invitation and set a password",
)
async def accept_invitation_endpoint(
    accept_request: InvitationAcceptRequest,
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TokenResponse:
    """Accept an invitation with the SMS code and choose a password.

    Args:
        accept_request: Phone, code, new password and device.
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Tokens of the new session.
    """
    async with persist_recorded_failure(db_session):
        return await account_service.accept_invitation(
            db_session, accept_request, client_context
        )


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
    client_context: ClientContext = Depends(get_client_context),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TokenResponse:
    """Log in and open a session.

    Args:
        login_request: Credentials and device.
        client_context: IP address and user agent of the request.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Access and refresh tokens and the person's memberships.
    """
    async with persist_recorded_failure(db_session):
        return await account_service.login(db_session, login_request, client_context)


@auth_router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new pair",
)
async def refresh_session_endpoint(
    refresh_request: RefreshRequest,
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TokenResponse:
    """Refresh a session.

    Args:
        refresh_request: The refresh token.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A new token pair; the old refresh token stops working.
    """
    return await account_service.refresh_session(db_session, refresh_request)


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
        An empty 204 response.
    """
    await account_service.logout(
        db_session,
        session_identity,
        all_devices=logout_request.all_devices,
        client_context=client_context,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    return await account_service.get_me(db_session, session_identity)


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
    return await account_service.switch_organization(
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
    return await account_service.list_sessions(db_session, session_identity)


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
    await account_service.end_session(db_session, session_identity, session_id)
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
    await account_service.set_push_token(
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
    await account_service.set_push_token(db_session, session_identity, None)
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
