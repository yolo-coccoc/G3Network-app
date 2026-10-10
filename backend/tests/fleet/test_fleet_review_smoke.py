"""Review smoke tests of the fleet service: data reach, tree loops, FL-10.

The guard tests pin what holds today: a customer of one organization gets
"not found" for another organization's fleet on get, update and soft delete,
internal staff reach it, and a fleet cannot be moved under one of its own
descendants. The test marked ``xfail(strict=True)`` asserts the correct
behaviour for review finding RV-AS1 (a fleet-limited manager widening their
own reach); it fails today and the fix forces the marker off. The fleet
repository is replaced by in-memory fakes; nothing touches a database.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.repository as fleet_repository
import app.domains.fleet.service as fleet_service
import app.domains.vehicles.service as vehicle_service
from app.domains.fleet.exceptions import (
    FleetHierarchyLoopError,
    FleetNotFoundError,
    FleetVehicleNotFoundError,
)
from app.domains.fleet.models import (
    FleetModel,
    FleetUserAssignmentModel,
    FleetVehicleMembershipModel,
)
from app.domains.fleet.schemas import FleetUpdateRequest, FleetVehicleAddRequest
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import Principal, UserRole
from app.domains.vehicles.types import VehicleReference
from tests.builders import build_fleet_record, fake_db_session
from tests.principals import (
    DEFAULT_ORGANIZATION_ID,
    OTHER_ORGANIZATION_ID,
    build_internal_principal,
    build_principal,
)

# A fleet operation run as one caller: get, update or soft delete.
FleetOperation = Callable[[UUID, Principal], Awaitable[object]]


def _install_scoped_fleet_repository(
    monkeypatch: pytest.MonkeyPatch, fleets: list[FleetModel]
) -> dict[str, Any]:
    """Replace the fleet repository with fakes over ``fleets`` that honour scope.

    ``get_by_id`` behaves like the real one: a fleet of another organization
    than the ``organization_id`` filter is "not found"; ``None`` means no
    restriction. The fleets hold no trucks, sub-fleets or assignments unless
    a fleet names another as its parent.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        fleets: The live fleets of the fake table.

    Returns:
        A record of writes: ``"updated"`` and ``"deleted"`` fleet IDs.
    """
    fleets_by_id = {fleet.fleet_id: fleet for fleet in fleets}
    writes: dict[str, Any] = {"updated": [], "deleted": []}

    async def get_by_id(
        db_session: AsyncSession,
        fleet_id: UUID,
        *,
        organization_id: UUID | None = None,
    ) -> FleetModel | None:
        """Return the live fleet when it is in scope."""
        fleet_record = fleets_by_id.get(fleet_id)
        if fleet_record is None or (
            organization_id is not None
            and fleet_record.organization_id != organization_id
        ):
            return None
        return fleet_record

    async def count_zero(db_session: AsyncSession, fleet_id: UUID) -> int:
        """Report no trucks."""
        return 0

    async def count_children(db_session: AsyncSession, fleet_id: UUID) -> int:
        """Count the fake fleets whose parent is ``fleet_id``."""
        return sum(1 for fleet in fleets if fleet.parent_fleet_id == fleet_id)

    async def no_rows(db_session: AsyncSession, fleet_id: UUID) -> list[object]:
        """Report no open memberships or assignments."""
        return []

    async def update_fields(
        db_session: AsyncSession,
        fleet_id: UUID,
        values: dict[str, Any],
        *,
        changed_by: UUID | None = None,
        change_reason: str = "",
    ) -> FleetModel | None:
        """Record the update and return the fleet."""
        writes["updated"].append(fleet_id)
        return fleets_by_id.get(fleet_id)

    async def soft_delete(
        db_session: AsyncSession,
        fleet_id: UUID,
        *,
        changed_by: UUID | None = None,
        change_reason: str = "",
    ) -> FleetModel | None:
        """Record the soft delete and return the fleet."""
        writes["deleted"].append(fleet_id)
        return fleets_by_id.get(fleet_id)

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        fleet_repository, "count_active_memberships_by_fleet", count_zero
    )
    monkeypatch.setattr(fleet_repository, "count_child_fleets", count_children)
    monkeypatch.setattr(fleet_repository, "list_open_assignments_by_fleet", no_rows)
    monkeypatch.setattr(
        fleet_repository, "list_all_active_memberships_by_fleet", no_rows
    )
    monkeypatch.setattr(fleet_repository, "update_fields", update_fields)
    monkeypatch.setattr(fleet_repository, "soft_delete", soft_delete)
    return writes


async def _get_fleet(fleet_id: UUID, principal: Principal) -> object:
    """Read a fleet as the caller."""
    return await fleet_service.get_fleet(
        fake_db_session(), fleet_id, principal=principal
    )


async def _rename_fleet(fleet_id: UUID, principal: Principal) -> object:
    """Rename a fleet as the caller."""
    return await fleet_service.update_fleet(
        fake_db_session(),
        fleet_id,
        FleetUpdateRequest(name="Renamed"),
        principal=principal,
    )


async def _soft_delete_fleet(fleet_id: UUID, principal: Principal) -> object:
    """Soft-delete a fleet as the caller."""
    return await fleet_service.soft_delete_fleet(
        fake_db_session(), fleet_id, principal=principal
    )


_FLEET_OPERATIONS = [
    pytest.param(_get_fleet, id="get"),
    pytest.param(_rename_fleet, id="update"),
    pytest.param(_soft_delete_fleet, id="soft-delete"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _FLEET_OPERATIONS)
async def test_customer_gets_not_found_for_another_organizations_fleet(
    monkeypatch: pytest.MonkeyPatch, operation: FleetOperation
) -> None:
    """A customer administrator of organization A never reaches B's fleet."""
    fleet_record = build_fleet_record(organization_id=OTHER_ORGANIZATION_ID)
    writes = _install_scoped_fleet_repository(monkeypatch, [fleet_record])

    with pytest.raises(FleetNotFoundError):
        await operation(
            fleet_record.fleet_id,
            build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        )
    assert writes == {"updated": [], "deleted": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _FLEET_OPERATIONS)
async def test_internal_staff_reach_any_organizations_fleet(
    monkeypatch: pytest.MonkeyPatch, operation: FleetOperation
) -> None:
    """Internal staff (no data scope) reach a customer's fleet."""
    fleet_record = build_fleet_record(organization_id=OTHER_ORGANIZATION_ID)
    _install_scoped_fleet_repository(monkeypatch, [fleet_record])

    await operation(fleet_record.fleet_id, build_internal_principal())


@pytest.mark.asyncio
async def test_customer_cannot_move_a_fleet_under_its_own_great_grandchild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A move under any descendant, three levels down, is a loop (FL-02) and
    writes nothing, also for a customer administrator acting in scope."""
    region = build_fleet_record(organization_id=DEFAULT_ORGANIZATION_ID)
    branch = build_fleet_record(organization_id=DEFAULT_ORGANIZATION_ID)
    depot = build_fleet_record(organization_id=DEFAULT_ORGANIZATION_ID)
    yard = build_fleet_record(organization_id=DEFAULT_ORGANIZATION_ID)
    branch.parent_fleet_id = region.fleet_id
    depot.parent_fleet_id = branch.fleet_id
    yard.parent_fleet_id = depot.fleet_id
    writes = _install_scoped_fleet_repository(
        monkeypatch, [region, branch, depot, yard]
    )

    with pytest.raises(FleetHierarchyLoopError):
        await fleet_service.update_fleet(
            fake_db_session(),
            region.fleet_id,
            FleetUpdateRequest(parent_fleet_id=yard.fleet_id),
            principal=build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        )
    assert writes["updated"] == []


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-AS1: a fleet-limited manager pulls an unassigned truck in",
)
async def test_limited_manager_cannot_add_a_truck_outside_their_visible_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FLEET_MANAGER limited to fleet F (FL-10) cannot see a truck that is in
    no fleet, so adding it to F by VIN is refused and nothing is inserted;
    otherwise the manager would widen their own limit."""
    fleet_record = build_fleet_record(organization_id=DEFAULT_ORGANIZATION_ID)
    _install_scoped_fleet_repository(monkeypatch, [fleet_record])
    limited_manager = build_principal(
        roles=frozenset({UserRole.FLEET_MANAGER}),
        organization_id=DEFAULT_ORGANIZATION_ID,
    )
    unassigned_vehicle_id = uuid4()
    inserted: list[UUID] = []

    async def assignments_of_manager(
        db_session: AsyncSession, membership_id: UUID
    ) -> list[FleetUserAssignmentModel]:
        """Limit the manager to the one fleet."""
        return [
            FleetUserAssignmentModel(
                fleet_user_assignment_id=uuid4(),
                fleet_id=fleet_record.fleet_id,
                membership_id=membership_id,
            )
        ]

    async def no_children(db_session: AsyncSession, fleet_id: UUID) -> list[UUID]:
        """Report no sub-fleets."""
        return []

    async def no_trucks(
        db_session: AsyncSession, fleet_ids: frozenset[UUID]
    ) -> list[UUID]:
        """Report the visible fleets as empty."""
        return []

    async def vehicle_by_vin(db_session: AsyncSession, vin: str) -> VehicleReference:
        """Resolve the VIN to a truck of the same organization."""
        return VehicleReference(
            vehicle_id=unassigned_vehicle_id,
            vin=vin,
            organization_id=DEFAULT_ORGANIZATION_ID,
            battery_capacity_kwh=None,
        )

    async def no_open_membership(db_session: AsyncSession, vehicle_id: UUID) -> None:
        """Report the truck as in no fleet."""
        return None

    async def insert_membership(
        db_session: AsyncSession,
        *,
        fleet_id: UUID,
        vehicle_id: UUID,
        added_at: datetime,
        added_by: UUID | None = None,
    ) -> FleetVehicleMembershipModel:
        """Record the insert and return the new open membership."""
        inserted.append(vehicle_id)
        return FleetVehicleMembershipModel(
            fleet_vehicle_membership_id=uuid4(),
            fleet_id=fleet_id,
            vehicle_id=vehicle_id,
            added_at=added_at,
        )

    monkeypatch.setattr(
        fleet_repository, "list_open_assignments_by_membership", assignments_of_manager
    )
    monkeypatch.setattr(fleet_repository, "list_child_fleet_ids", no_children)
    monkeypatch.setattr(
        fleet_repository, "list_active_vehicle_ids_by_fleet_ids", no_trucks
    )
    monkeypatch.setattr(
        fleet_repository, "find_active_membership_by_vehicle", no_open_membership
    )
    monkeypatch.setattr(fleet_repository, "insert_membership", insert_membership)
    monkeypatch.setattr(
        vehicle_service, "resolve_vehicle_reference_by_vin", vehicle_by_vin
    )

    with pytest.raises((FleetVehicleNotFoundError, AccessDeniedError)):
        await fleet_service.add_vehicle_to_fleet(
            fake_db_session(),
            fleet_record.fleet_id,
            FleetVehicleAddRequest(vehicle_vin="LJ1EKABR3N0000001"),
            principal=limited_manager,
        )
    assert inserted == []
