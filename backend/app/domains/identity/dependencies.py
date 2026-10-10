"""FastAPI dependencies that authenticate a request and apply the access rule.

This module is part of the identity domain's public surface: every router
that needs a logged-in caller depends on `get_current_principal` (who is
calling, for which organization, with which roles) and, for a role check, on
`require_roles(...)`. Data reach is then `principal.can_access_organization`
(ACC-15): internal principals see every organization, everyone else only
their own. Features are role only until plans exist (BL-16).

The dependencies share one database session per request with the endpoint
(`get_db` with function scope), so the activity timestamps they refresh are
committed with the request.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.account_service as account_service
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import (
    ClientContext,
    Principal,
    SessionIdentity,
    UserRole,
)
from app.libs.db.session import get_db

# `auto_error=False`: a missing header is reported by the service as the same
# 401 as a bad token, with the shared `WWW-Authenticate` header.
bearer_scheme = HTTPBearer(
    auto_error=False, description="Access token from /auth/login"
)


async def get_client_context(request: Request) -> ClientContext:
    """Read where the request came from, for audit rows and consent proof.

    Args:
        request: The incoming request.

    Returns:
        The client's IP address (as seen by the server; behind a proxy this is
        the proxy unless the server trusts forwarded headers) and user agent.
    """
    user_agent = request.headers.get("user-agent")
    return ClientContext(
        ip_address=None if request.client is None else request.client.host,
        user_agent=None if user_agent is None else user_agent[:255],
    )


async def get_session_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> SessionIdentity:
    """Authenticate the bearer token without requiring a picked organization.

    Used by the endpoints a person with several organizations calls before
    picking one (`/auth/me`, `/auth/organization`, invitation acceptance).

    Args:
        credentials: The `Authorization: Bearer` header, if sent.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The validated session.

    Raises:
        SessionInvalidError: 401 when the token is missing, forged, expired or
            its session ended.
    """
    return await account_service.authenticate_session(
        db_session, None if credentials is None else credentials.credentials
    )


async def get_current_principal(
    session_identity: SessionIdentity = Depends(get_session_identity),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> Principal:
    """Authenticate the request and build the caller's `Principal` (ACC-14).

    Args:
        session_identity: The validated session.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The person, their active membership in the session's organization,
        its roles and whether the organization is internal.

    Raises:
        SessionInvalidError: 401, see `get_session_identity`.
        OrganizationNotSelectedError: 403, the session has no organization yet.
        AccessDeniedError: 403, no active membership in the organization.
        OrganizationInactiveError: 403, the organization is suspended/closed.
    """
    return await account_service.resolve_principal(db_session, session_identity)


# Resolver of the trucks a fleet-limited caller may see (FL-10), registered at
# start-up by `app/api/fleet_visibility.py`. `identity` depends on no domain
# and `vehicles` cannot call `fleet` (FL-01), so the fleet function reaches
# the vehicle endpoints through this one slot.
VisibleVehicleResolver = Callable[
    [AsyncSession, Principal], Awaitable[frozenset[UUID] | None]
]
_visible_vehicle_resolver: VisibleVehicleResolver | None = None


def register_visible_vehicle_resolver(resolver: VisibleVehicleResolver) -> None:
    """Register the function that tells which trucks a limited caller may see.

    Args:
        resolver: Called with the request's session and principal; returns
            the visible vehicle IDs, or `None` when the caller is not limited.

    Side Effects:
        Replaces any resolver registered before; calling it again with the
        same function changes nothing.
    """
    global _visible_vehicle_resolver
    _visible_vehicle_resolver = resolver


async def get_visible_vehicle_ids(
    principal: Principal = Depends(get_current_principal),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> frozenset[UUID] | None:
    """Resolve once per request the trucks the caller's fleet limit allows.

    Args:
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        `None` when the caller is not limited (internal staff, the
        organization administrator, a member with no fleet assignment, or any
        role that is not fleet-level); otherwise the IDs of the trucks
        currently in the assigned fleets and every fleet below them. If no
        resolver was registered, a limited caller gets an empty set (fail
        closed) rather than the whole organization.
    """
    if not principal.is_fleet_limited:
        return None
    if _visible_vehicle_resolver is None:
        return frozenset()
    return await _visible_vehicle_resolver(db_session, principal)


def require_roles(
    *roles: UserRole, internal_only: bool = False
) -> Callable[..., Awaitable[Principal]]:
    """Build a dependency that admits only callers holding one of the roles.

    Args:
        *roles: Roles that are allowed; holding any one is enough.
        internal_only: Also require that the caller's organization is
            internal (our own staff).

    Returns:
        A FastAPI dependency returning the `Principal`.

    Raises:
        AccessDeniedError: 403 from the dependency when the caller holds none
            of the roles, or is not internal when `internal_only` is set.
    """

    async def _require_roles(
        principal: Principal = Depends(get_current_principal),
    ) -> Principal:
        """Check the caller's roles and return the caller."""
        if internal_only and not principal.is_internal:
            raise AccessDeniedError("This action is for our own staff")
        if not principal.has_any_role(*roles):
            raise AccessDeniedError("Your role does not allow this action")
        return principal

    return _require_roles
