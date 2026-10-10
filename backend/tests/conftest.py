"""Shared pytest fixtures for the smoke tests."""

import pytest

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
