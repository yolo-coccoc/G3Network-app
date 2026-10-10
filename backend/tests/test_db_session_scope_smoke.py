"""Every endpoint must commit its transaction before the response is sent.

``get_db`` commits in the code after its ``yield``. FastAPI runs that code
*after the response is sent* unless the dependency is declared with
``scope="function"``, so with the default a client can receive 201 and
immediately get 404 for the row it just created (observed: the 2.0.1 seed
script creating a station then its EVSE), and a commit that fails is
reported to the client as a success. This test fails if any route depends on
``get_db`` without ``scope="function"``.
"""

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.libs.db.session import get_db

DOMAINS_ROOT = Path(__file__).resolve().parents[1] / "app" / "domains"
# Every module that holds routers: `router.py` and the `*_router.py` files of
# a domain with several (identity).
# `app.api.vehicle_transfer` (VH-12) and `app.api.charging_session_flow` (CHG-01)
# are the cross-domain orchestration routers.
ROUTER_MODULES = sorted(
    [
        f"app.domains.{path.parent.name}.{path.stem}"
        for path in DOMAINS_ROOT.glob("*/*router.py")
    ]
    + ["app.api.vehicle_transfer", "app.api.charging_session_flow"]
)


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    """Yield every sub-dependency of a route, depth first.

    Args:
        dependant: The route's (or a dependency's) dependant tree.

    Yields:
        Each nested dependant.
    """
    for sub_dependant in dependant.dependencies:
        yield sub_dependant
        yield from _walk(sub_dependant)


@pytest.mark.parametrize("module_name", ROUTER_MODULES)
def test_get_db_is_function_scoped_on_every_route(module_name: str) -> None:
    """Each route using get_db declares scope="function" (commit before response)."""
    module = importlib.import_module(module_name)
    routers = [value for value in vars(module).values() if isinstance(value, APIRouter)]
    offenders = [
        f"{', '.join(sorted(route.methods or ()))} {route.path}"
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute)
        for dependency in _walk(route.dependant)
        if dependency.call is get_db and dependency.scope != "function"
    ]

    assert not offenders, (
        f'Use Depends(get_db, scope="function") in {module_name}: {offenders}'
    )
