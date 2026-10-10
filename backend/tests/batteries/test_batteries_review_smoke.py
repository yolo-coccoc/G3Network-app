"""Review smoke tests of the batteries service: data reach and the sale move.

The guard tests pin what holds today: a customer of one organization gets
"not found" for another organization's battery on get, update, soft delete
and transfer, and internal staff reach it. The test marked
``xfail(strict=True)`` asserts the correct behaviour for review finding RV-AS7
(the pack's ``acquired_at`` moving backwards when its truck is sold); it
fails today and the fix forces the marker off. The battery repository is
replaced by in-memory fakes; nothing touches a database.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.batteries.repository as battery_repository
import app.domains.batteries.service as battery_service
from app.domains.batteries.exceptions import BatteryNotFoundError
from app.domains.batteries.models import BatteryModel
from app.domains.batteries.schemas import (
    BatteryOwnershipTransferRequest,
    BatteryUpdateRequest,
)
from app.domains.batteries.types import BatteryStatus
from app.domains.identity.types import Principal
from tests.builders import fake_db_session
from tests.principals import (
    DEFAULT_ORGANIZATION_ID,
    OTHER_ORGANIZATION_ID,
    build_internal_principal,
    build_principal,
)

# A battery operation run as one caller: get, update, soft delete or transfer.
BatteryOperation = Callable[[UUID, Principal], Awaitable[object]]


def _build_battery_record(
    *, organization_id: UUID, acquired_at: datetime, vehicle_id: UUID | None = None
) -> BatteryModel:
    """Build a live, ACTIVE battery of ``organization_id``.

    Args:
        organization_id: The owner.
        acquired_at: When the owner took the pack.
        vehicle_id: The truck it is fitted to, if any.

    Returns:
        An in-memory ORM battery.
    """
    now = datetime.now(timezone.utc)
    return BatteryModel(
        battery_id=uuid4(),
        serial_number=f"BAT-{uuid4().hex[:8]}",
        battery_model_id=uuid4(),
        organization_id=organization_id,
        acquired_at=acquired_at,
        vehicle_id=vehicle_id,
        installed_at=acquired_at if vehicle_id else None,
        status=BatteryStatus.ACTIVE.value,
        created_at=now,
        updated_at=now,
    )


def _install_scoped_battery_repository(
    monkeypatch: pytest.MonkeyPatch, battery_record: BatteryModel
) -> dict[str, Any]:
    """Replace the battery repository with a one-row fake that honours scope.

    Like the real ``get_by_id`` / ``update_fields``, a battery of another
    organization than the ``organization_id`` filter is "not found"; ``None``
    means no restriction (internal staff).

    Args:
        monkeypatch: The test's monkeypatch fixture.
        battery_record: The only battery of the fake table.

    Returns:
        The values the last ``update_fields`` call wrote (empty if none).
    """
    written_values: dict[str, Any] = {}

    def _visible(battery_id: UUID, organization_id: UUID | None) -> bool:
        """Tell whether the fake row matches the ID and the scope."""
        return battery_id == battery_record.battery_id and (
            organization_id is None or organization_id == battery_record.organization_id
        )

    async def get_by_id(
        db_session: AsyncSession,
        battery_id: UUID,
        *,
        organization_id: UUID | None = None,
    ) -> BatteryModel | None:
        """Return the row when it is in scope."""
        return battery_record if _visible(battery_id, organization_id) else None

    async def update_fields(
        db_session: AsyncSession,
        battery_id: UUID,
        values: dict[str, Any],
        *,
        change_reason: str,
        changed_by: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> BatteryModel | None:
        """Record the values when the row is in scope."""
        if not _visible(battery_id, organization_id):
            return None
        written_values.update(values)
        return battery_record

    monkeypatch.setattr(battery_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(battery_repository, "update_fields", update_fields)
    return written_values


async def _get_battery(battery_id: UUID, principal: Principal) -> object:
    """Read a battery as the caller."""
    return await battery_service.get_battery(
        fake_db_session(), battery_id, principal=principal
    )


async def _update_battery(battery_id: UUID, principal: Principal) -> object:
    """Change a battery's manufacturing date as the caller."""
    return await battery_service.update_battery(
        fake_db_session(),
        battery_id,
        BatteryUpdateRequest(manufactured_on=datetime(2025, 1, 1).date()),
        principal=principal,
    )


async def _soft_delete_battery(battery_id: UUID, principal: Principal) -> object:
    """Soft-delete a battery as the caller."""
    await battery_service.soft_delete_battery(
        fake_db_session(), battery_id, principal=principal
    )
    return None


async def _transfer_battery(battery_id: UUID, principal: Principal) -> object:
    """Hand a battery to a third organization as the caller."""
    return await battery_service.transfer_battery_ownership(
        fake_db_session(),
        battery_id,
        BatteryOwnershipTransferRequest(organization_id=uuid4(), reason="Leased"),
        principal=principal,
    )


_BATTERY_OPERATIONS = [
    pytest.param(_get_battery, id="get"),
    pytest.param(_update_battery, id="update"),
    pytest.param(_soft_delete_battery, id="soft-delete"),
    pytest.param(_transfer_battery, id="transfer"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _BATTERY_OPERATIONS)
async def test_customer_gets_not_found_for_another_organizations_battery(
    monkeypatch: pytest.MonkeyPatch, operation: BatteryOperation
) -> None:
    """A customer of organization A never reaches organization B's pack."""
    battery_record = _build_battery_record(
        organization_id=OTHER_ORGANIZATION_ID,
        acquired_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    written_values = _install_scoped_battery_repository(monkeypatch, battery_record)

    with pytest.raises(BatteryNotFoundError):
        await operation(
            battery_record.battery_id,
            build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        )
    assert written_values == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _BATTERY_OPERATIONS)
async def test_internal_staff_reach_any_organizations_battery(
    monkeypatch: pytest.MonkeyPatch,
    own_organization_for_new_records: None,
    operation: BatteryOperation,
) -> None:
    """Internal staff (no data scope) reach a customer's pack."""
    battery_record = _build_battery_record(
        organization_id=OTHER_ORGANIZATION_ID,
        acquired_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    _install_scoped_battery_repository(monkeypatch, battery_record)

    await operation(battery_record.battery_id, build_internal_principal())


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RV-AS7: a backdated sale moves the pack's acquired_at backwards",
)
async def test_sold_vehicle_battery_acquired_at_never_moves_backwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a truck sale is dated before the seller took its pack, the pack's
    new ``acquired_at`` is not earlier than the old one (DM-22: every owner
    period has a positive length), or the pack does not move."""
    seller_id = uuid4()
    battery_record = _build_battery_record(
        organization_id=seller_id,
        acquired_at=datetime.now(timezone.utc),
        vehicle_id=uuid4(),
    )
    original_acquired_at = battery_record.acquired_at
    written_values = _install_scoped_battery_repository(monkeypatch, battery_record)

    async def battery_in_truck(
        db_session: AsyncSession, vehicle_id: UUID
    ) -> BatteryModel:
        """Return the seller's pack fitted to the truck."""
        return battery_record

    monkeypatch.setattr(battery_repository, "find_by_vehicle_id", battery_in_truck)

    assert battery_record.vehicle_id is not None
    await battery_service.transfer_installed_battery_with_vehicle(
        fake_db_session(),
        battery_record.vehicle_id,
        from_organization_id=seller_id,
        to_organization_id=uuid4(),
        acquired_at=original_acquired_at - timedelta(days=30),
        changed_by=None,
    )

    if written_values:
        assert written_values["acquired_at"] >= original_acquired_at
