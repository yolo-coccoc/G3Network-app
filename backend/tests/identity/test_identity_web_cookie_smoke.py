"""Smoke tests for the browser's refresh-token cookie (IS-13).

A ``WEB`` session never receives its refresh token in a response body: the
service hands it over for an ``HttpOnly`` cookie, the router sets it, reads it
back on refresh and clears it on logout. Phone-app sessions keep the token in
the body. Endpoint functions are called directly, like the other smoke tests.
"""

from datetime import timedelta
from http.cookies import SimpleCookie
from uuid import uuid4

import pytest
from fastapi import Response

import app.domains.identity.router as identity_router
import app.domains.identity.session_service as login_session_service
from app.domains.identity.exceptions import SessionInvalidError
from app.domains.identity.models import UserSessionModel
from app.domains.identity.schemas import LogoutRequest, RefreshRequest, TokenResponse
from app.domains.identity.types import (
    ClientContext,
    SessionIdentity,
    SessionPlatform,
)
from app.libs.common.clock import utc_now
from tests.builders import fake_db_session


def _session_record(platform: SessionPlatform) -> UserSessionModel:
    """Build a live session of the given platform that expires in 7 days."""
    now = utc_now()
    return UserSessionModel(
        user_session_id=uuid4(),
        user_id=uuid4(),
        organization_id=None,
        platform=platform.value,
        refresh_token_hash="unused",
        created_at=now,
        last_used_at=now,
        expires_at=now + timedelta(days=7),
    )


def _issued_tokens(
    *, cookie_refresh_token: str | None
) -> login_session_service.IssuedSessionTokens:
    """Build what the service would issue, with or without a browser cookie."""
    token_response = TokenResponse(
        access_token="access",
        expires_in=900,
        refresh_token=None if cookie_refresh_token else "body-refresh",
        session_id=uuid4(),
        memberships=[],
    )
    return login_session_service.IssuedSessionTokens(
        token_response,
        cookie_refresh_token,
        3600 if cookie_refresh_token else None,
    )


def _set_cookie(response: Response) -> SimpleCookie:
    """Parse the response's ``Set-Cookie`` header."""
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie


@pytest.fixture(autouse=True)
def _no_memberships(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the membership lookup the token answer includes."""

    async def build_membership_summaries(*_args: object) -> list[object]:
        """Return no memberships."""
        return []

    monkeypatch.setattr(
        login_session_service, "build_membership_summaries", build_membership_summaries
    )


@pytest.mark.asyncio
async def test_browser_session_gets_its_refresh_token_for_the_cookie_only() -> None:
    """A WEB session: no refresh token in the body; cookie lives as the session."""
    session_record = _session_record(SessionPlatform.WEB)

    issued_tokens = await login_session_service._build_issued_tokens(
        fake_db_session(), session_record, "plain-refresh"
    )

    assert issued_tokens.token_response.refresh_token is None
    assert issued_tokens.cookie_refresh_token == "plain-refresh"
    assert issued_tokens.cookie_max_age_seconds is not None
    assert 0 < issued_tokens.cookie_max_age_seconds <= 7 * 24 * 3600


@pytest.mark.asyncio
async def test_phone_session_keeps_its_refresh_token_in_the_body() -> None:
    """An ANDROID session: the token stays in the body and no cookie is issued."""
    session_record = _session_record(SessionPlatform.ANDROID)

    issued_tokens = await login_session_service._build_issued_tokens(
        fake_db_session(), session_record, "plain-refresh"
    )

    assert issued_tokens.token_response.refresh_token == "plain-refresh"
    assert issued_tokens.cookie_refresh_token is None


@pytest.mark.asyncio
async def test_refresh_without_any_token_is_unauthenticated() -> None:
    """No body token and no cookie: 401, before any lookup."""
    with pytest.raises(SessionInvalidError):
        await login_session_service.refresh_session(fake_db_session(), None)


@pytest.mark.asyncio
async def test_refresh_endpoint_uses_the_cookie_and_sets_a_strict_http_only_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A browser sends no body: the cookie is used and replaced with a new one."""
    received_tokens: list[str | None] = []

    async def refresh_session(
        _db: object, refresh_token: str | None
    ) -> login_session_service.IssuedSessionTokens:
        """Record the token the endpoint passed and issue a browser cookie."""
        received_tokens.append(refresh_token)
        return _issued_tokens(cookie_refresh_token="new-cookie-token")

    monkeypatch.setattr(login_session_service, "refresh_session", refresh_session)
    response = Response()

    body = await identity_router.refresh_session_endpoint(
        response, None, "old-cookie-token", fake_db_session()
    )

    assert received_tokens == ["old-cookie-token"]
    assert body.refresh_token is None
    morsel = _set_cookie(response)[identity_router.REFRESH_COOKIE_NAME]
    assert morsel.value == "new-cookie-token"
    assert morsel["httponly"] is True
    assert morsel["secure"] is True
    assert morsel["samesite"] == "strict"
    assert morsel["path"] == identity_router.REFRESH_COOKIE_PATH
    assert morsel["max-age"] == "3600"


@pytest.mark.asyncio
async def test_refresh_endpoint_prefers_the_phone_apps_body_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body token wins over a cookie; a phone session sets no cookie."""
    received_tokens: list[str | None] = []

    async def refresh_session(
        _db: object, refresh_token: str | None
    ) -> login_session_service.IssuedSessionTokens:
        """Record the token the endpoint passed and issue a phone answer."""
        received_tokens.append(refresh_token)
        return _issued_tokens(cookie_refresh_token=None)

    monkeypatch.setattr(login_session_service, "refresh_session", refresh_session)
    response = Response()

    body = await identity_router.refresh_session_endpoint(
        response,
        RefreshRequest(refresh_token="body-token"),
        "stray-cookie",
        fake_db_session(),
    )

    assert received_tokens == ["body-token"]
    assert body.refresh_token == "body-refresh"
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_logout_clears_the_refresh_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logout answers 204 and expires the cookie on the auth path."""

    async def logout(*_args: object, **_kwargs: object) -> None:
        """End nothing; the session table is not under test here."""

    monkeypatch.setattr(login_session_service, "logout", logout)
    session_identity = SessionIdentity(
        session_id=uuid4(), user_id=uuid4(), organization_id=None
    )

    response = await identity_router.logout_endpoint(
        LogoutRequest(),
        session_identity,
        ClientContext(ip_address=None, user_agent=None),
        fake_db_session(),
    )

    assert response.status_code == 204
    morsel = _set_cookie(response)[identity_router.REFRESH_COOKIE_NAME]
    assert morsel.value == ""
    assert morsel["max-age"] == "0"
    assert morsel["path"] == identity_router.REFRESH_COOKIE_PATH
