"""Shared pytest fixtures for the smoke tests."""

import pytest

import app.domains.batteries.service as battery_service
import app.domains.charging_sessions.service as charging_session_service
import app.domains.identity.service as identity_service
from app.domains.identity.types import Principal


@pytest.fixture
def own_organization_for_new_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the organization lookup of `resolve_organization_for_new_record`.

    A smoke test of a create service uses a fake session, so the identity
    lookup that validates an organization named by internal staff cannot run.
    The replacement returns the organization named in the request, else the
    caller's own, which is what the real function does after its checks.
    """

    async def resolve_organization_for_new_record(
        _db: object, principal: Principal, requested_organization_id: object
    ) -> object:
        return requested_organization_id or principal.organization_id

    monkeypatch.setattr(
        identity_service,
        "resolve_organization_for_new_record",
        resolve_organization_for_new_record,
    )


@pytest.fixture
def no_installed_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every truck look like it has no battery fitted (WP3, VH-16).

    Telemetry asks the batteries service for the installed pack's capacity
    before it falls back to the model's nominal figure; a smoke test with a
    fake session cannot run that query, so it declares there is no pack.
    """

    async def resolve_installed_battery_capacity_kwh(
        _db: object, _vehicle_id: object
    ) -> None:
        return None

    monkeypatch.setattr(
        battery_service,
        "resolve_installed_battery_capacity_kwh",
        resolve_installed_battery_capacity_kwh,
    )


@pytest.fixture(autouse=True)
def no_session_ended_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no session-ended hook registered (BL-19).

    Importing ``app.api.main`` registers the billing hook in the test process.
    A smoke test that completes or abandons a session with a fake database
    session must not run it; a test that wants billing registers the hook
    itself.
    """
    monkeypatch.setattr(charging_session_service, "_session_ended_hooks", [])
