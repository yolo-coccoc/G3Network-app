"""Route-level security guard: every route is authenticated and role-gated.

Walks the dependency tree of every `APIRoute` of `app.api.main.app` (FastAPI
0.139 keeps included routers as `_IncludedRouter` wrappers, so the walk
recurses through ``original_router`` and accumulates each include prefix).

- Every route depends on `get_session_identity` (directly or through
  `get_current_principal` / `require_roles`), except the explicit public
  allowlist below. A new unauthenticated route fails the test until it is
  added to the allowlist on purpose.
- Every authenticated route outside the identity domain also passes a role
  gate built by `require_roles`, except the shared catalogs any logged-in
  user may read. Identity endpoints apply their rules in the services (the
  caller's own account, or an ORG_ADMIN / internal-admin check).
"""

from collections.abc import Callable, Iterator, Sequence
from typing import Any

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.api.main import app
from app.domains.identity.dependencies import get_session_identity

# Routes reachable without a login, as (method, path). Each one is public by
# design: logging in or recovering an account, reading the legal texts before
# sign-up, the liveness probe and the bank's webhook (shared secret).
PUBLIC_ROUTES = frozenset(
    {
        ("GET", "/health"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/sign-up"),
        ("POST", "/api/v1/auth/otp/send"),
        ("POST", "/api/v1/auth/refresh"),
        ("POST", "/api/v1/auth/password/reset"),
        ("POST", "/api/v1/auth/invitations/accept"),
        ("GET", "/api/v1/legal-documents/current"),
        ("GET", "/api/v1/legal-documents/{legal_document_id}"),
        ("POST", "/api/v1/payments/vietqr/notifications"),
    }
)

# Authenticated routes outside identity with no role gate: the shared truck
# and battery model catalogs, which every logged-in user may read.
ANY_LOGGED_IN_USER_ROUTES = frozenset(
    {
        ("GET", "/api/v1/vehicle-models/"),
        ("GET", "/api/v1/vehicle-models/{vehicle_model_id}"),
        ("GET", "/api/v1/battery-models/"),
        ("GET", "/api/v1/battery-models/{battery_model_id}"),
    }
)

# Name of the inner dependency `require_roles(...)` returns.
_ROLE_GATE_NAME = "_require_roles"


def _iter_routes(
    routes: Sequence[Any], prefix: str = ""
) -> Iterator[tuple[str, APIRoute]]:
    """Yield every API route with its full path, recursing into included routers.

    Args:
        routes: Routes of an application or router.
        prefix: Path prefix accumulated from the enclosing includes.

    Yields:
        ``(full_path, route)`` for each `APIRoute`.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router"):
            yield from _iter_routes(
                route.original_router.routes, prefix + route.include_context.prefix
            )


def _depends_on(dependant: Dependant, predicate: Callable[[Any], bool]) -> bool:
    """Tell whether any dependency in the tree satisfies the predicate.

    Args:
        dependant: Root of a route's dependency tree.
        predicate: Called with each dependency's callable.

    Returns:
        `True` if the predicate holds for the root or any sub-dependency.
    """
    return bool(predicate(dependant.call)) or any(
        _depends_on(sub_dependant, predicate)
        for sub_dependant in dependant.dependencies
    )


def _route_keys() -> list[tuple[str, str, APIRoute]]:
    """List ``(method, path, route)`` for every method of every API route."""
    return [
        (method, path, route)
        for path, route in _iter_routes(app.routes)
        for method in sorted(route.methods or ())
    ]


def _is_authenticated(route: APIRoute) -> bool:
    """Tell whether the route validates the bearer token."""
    return _depends_on(route.dependant, lambda call: call is get_session_identity)


def _is_role_gated(route: APIRoute) -> bool:
    """Tell whether the route passes a `require_roles` gate."""
    return _depends_on(
        route.dependant, lambda call: getattr(call, "__name__", "") == _ROLE_GATE_NAME
    )


def test_route_walk_finds_the_whole_api() -> None:
    """The walk reaches the included routers, not just the top-level routes."""
    paths = {path for _method, path, _route in _route_keys()}

    assert len(paths) > 100
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/vehicles/" in paths


def test_every_route_but_the_public_allowlist_requires_a_login() -> None:
    """A route without the bearer check is a failure unless allowlisted."""
    unauthenticated = {
        (method, path)
        for method, path, route in _route_keys()
        if not _is_authenticated(route)
    }

    assert unauthenticated - PUBLIC_ROUTES == set()


def test_every_public_allowlist_entry_still_exists_and_is_public() -> None:
    """The allowlist holds no stale entry and nothing that now needs a login."""
    routes = {(method, path): route for method, path, route in _route_keys()}

    for route_key in PUBLIC_ROUTES:
        assert route_key in routes, route_key
        assert not _is_authenticated(routes[route_key]), route_key


def test_every_authenticated_route_outside_identity_has_a_role_gate() -> None:
    """Outside identity, a logged-in caller always passes a role check too."""
    ungated = {
        (method, path)
        for method, path, route in _route_keys()
        if _is_authenticated(route)
        and not route.endpoint.__module__.startswith("app.domains.identity.")
        and not _is_role_gated(route)
    }

    assert ungated - ANY_LOGGED_IN_USER_ROUTES == set()
