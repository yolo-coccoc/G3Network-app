"""Review smoke tests of the warranties service: data reach and finite limits.

The guard tests pin what holds today: a warranty is visible only to the
organization owning its covered object, so a customer of another
organization gets "not found" on get, update, void and soft delete, and
internal staff reach it. The tests marked ``xfail(strict=True)`` assert the
correct behaviour for review finding RV-AS9 (non-finite limit readings and
consumption figures reaching a JSONB column, HTTP 500); they fail today and
the fix forces the marker off. The warranty repository is replaced by an
in-memory fake; nothing touches a database.
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.warranties.repository as warranty_repository
import app.domains.warranties.service as warranty_service
from app.domains.identity.types import Principal
from app.domains.vehicles.schemas import VehicleModelCreateRequest
from app.domains.warranties.exceptions import (
    WarrantyLimitsInvalidError,
    WarrantyNotFoundError,
)
from app.domains.warranties.models import WarrantyModel
from app.domains.warranties.schemas import (
    WarrantyCreateRequest,
    WarrantyUpdateRequest,
)
from app.domains.warranties.types import (
    WarrantyObjectKind,
    WarrantyStatus,
    WarrantyType,
)
from tests.builders import fake_db_session
from tests.principals import (
    DEFAULT_ORGANIZATION_ID,
    OTHER_ORGANIZATION_ID,
    build_internal_principal,
    build_principal,
)

# A warranty operation run as one caller: get, update, void or soft delete.
WarrantyOperation = Callable[[UUID, Principal], Awaitable[object]]


def _build_truck_warranty() -> WarrantyModel:
    """Build a live, ACTIVE truck warranty with a random truck."""
    now = datetime.now(timezone.utc)
    return WarrantyModel(
        warranty_id=uuid4(),
        vehicle_id=uuid4(),
        warranty_type=WarrantyType.STANDARD.value,
        starts_on=date(2026, 1, 1),
        ends_on=date(2031, 1, 1),
        status=WarrantyStatus.ACTIVE.value,
        created_at=now,
        updated_at=now,
    )


def _install_scoped_warranty_repository(
    monkeypatch: pytest.MonkeyPatch,
    warranty_record: WarrantyModel,
    owner_organization_id: UUID,
) -> dict[str, Any]:
    """Replace the warranty repository with a one-row fake that honours scope.

    The real repository keeps a warranty visible only to the organization
    owning its covered object (DM-24); the fake applies the same rule with a
    fixed owner.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        warranty_record: The only warranty of the fake table.
        owner_organization_id: Who owns the covered truck.

    Returns:
        The values the last ``update_fields`` call wrote (empty if none).
    """
    written_values: dict[str, Any] = {}

    def _visible(warranty_id: UUID, organization_id: UUID | None) -> bool:
        """Tell whether the fake row matches the ID and the scope."""
        return warranty_id == warranty_record.warranty_id and (
            organization_id is None or organization_id == owner_organization_id
        )

    async def get_by_id(
        db_session: AsyncSession,
        warranty_id: UUID,
        *,
        organization_id: UUID | None = None,
    ) -> WarrantyModel | None:
        """Return the row when it is in scope."""
        return warranty_record if _visible(warranty_id, organization_id) else None

    async def update_fields(
        db_session: AsyncSession,
        warranty_id: UUID,
        values: dict[str, Any],
        *,
        change_reason: str,
        changed_by: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> WarrantyModel | None:
        """Record the values when the row is in scope."""
        if not _visible(warranty_id, organization_id):
            return None
        written_values.update(values)
        return warranty_record

    monkeypatch.setattr(warranty_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(warranty_repository, "update_fields", update_fields)
    return written_values


async def _get_warranty(warranty_id: UUID, principal: Principal) -> object:
    """Read a warranty as the caller."""
    return await warranty_service.get_warranty(
        fake_db_session(), warranty_id, principal=principal
    )


async def _update_warranty(warranty_id: UUID, principal: Principal) -> object:
    """Change a warranty's contract reference as the caller."""
    return await warranty_service.update_warranty(
        fake_db_session(),
        warranty_id,
        WarrantyUpdateRequest(contract_reference="WC-2026-0001"),
        principal=principal,
    )


async def _void_warranty(warranty_id: UUID, principal: Principal) -> object:
    """Void a warranty as the caller."""
    return await warranty_service.void_warranty(
        fake_db_session(), warranty_id, principal=principal, reason="Policy breach"
    )


async def _soft_delete_warranty(warranty_id: UUID, principal: Principal) -> object:
    """Soft-delete a warranty as the caller."""
    await warranty_service.soft_delete_warranty(
        fake_db_session(), warranty_id, principal=principal
    )
    return None


_WARRANTY_OPERATIONS = [
    pytest.param(_get_warranty, id="get"),
    pytest.param(_update_warranty, id="update"),
    pytest.param(_void_warranty, id="void"),
    pytest.param(_soft_delete_warranty, id="soft-delete"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _WARRANTY_OPERATIONS)
async def test_customer_gets_not_found_for_another_organizations_warranty(
    monkeypatch: pytest.MonkeyPatch, operation: WarrantyOperation
) -> None:
    """A customer of organization A never reaches a warranty of B's truck."""
    warranty_record = _build_truck_warranty()
    written_values = _install_scoped_warranty_repository(
        monkeypatch, warranty_record, OTHER_ORGANIZATION_ID
    )

    with pytest.raises(WarrantyNotFoundError):
        await operation(
            warranty_record.warranty_id,
            build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        )
    assert written_values == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _WARRANTY_OPERATIONS)
async def test_internal_staff_reach_any_organizations_warranty(
    monkeypatch: pytest.MonkeyPatch, operation: WarrantyOperation
) -> None:
    """Internal staff (no data scope) reach a warranty of a customer's truck."""
    warranty_record = _build_truck_warranty()
    _install_scoped_warranty_repository(
        monkeypatch, warranty_record, OTHER_ORGANIZATION_ID
    )

    await operation(warranty_record.warranty_id, build_internal_principal())


@pytest.mark.parametrize("raw_reading", ["NaN", "Infinity", "1e400"])
def test_warranty_limits_reject_non_finite_readings(raw_reading: str) -> None:
    """A limit reading that is not a finite number is refused, by the request
    schema or by ``validate_limits``, before it reaches the JSONB column."""
    request_json = (
        '{"vehicle_id": "7a4c1e9b-3d2f-4b8a-a6c5-1e0d9f8b7a44",'
        ' "warranty_type": "STANDARD", "starts_on": "2026-01-01",'
        ' "ends_on": "2031-01-01", "limits": {"distance_km": ' + raw_reading + "}}"
    )
    try:
        warranty_create_request = WarrantyCreateRequest.model_validate_json(
            request_json
        )
    except ValidationError:
        return
    try:
        warranty_service.validate_limits(
            WarrantyObjectKind.VEHICLE, warranty_create_request.limits
        )
    except WarrantyLimitsInvalidError:
        return
    raise AssertionError(f"limit reading {raw_reading} was accepted")


def test_vehicle_model_consumption_curve_rejects_infinite_figures() -> None:
    """An infinite consumption figure is refused by the request schema before
    it reaches the JSONB ``consumption_curve`` column."""
    request_json = (
        '{"make": "Tri-Ring", "model_name": "EVT-400",'
        ' "consumption_curve": [{"load_percent": 50, "kwh_per_km": 1e400}]}'
    )
    try:
        VehicleModelCreateRequest.model_validate_json(request_json)
    except ValidationError:
        return
    raise AssertionError("an infinite kwh_per_km was accepted")
