"""Smoke tests for the fleet service: fleets and vehicle membership (F-E1)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.repository as fleet_repository
import app.domains.fleet.service as fleet_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.fleet.exceptions import (
    FleetConflictError,
    FleetHasSubFleetsError,
    FleetHierarchyLoopError,
    FleetMembershipConflictError,
    FleetMembershipNotFoundError,
    FleetNotFoundError,
    FleetParentNotFoundError,
    FleetVehicleNotFoundError,
    GeofenceNotFoundError,
)
from app.domains.fleet.models import (
    FleetModel,
    FleetVehicleMembershipModel,
    GeofenceModel,
)
from app.domains.fleet.schemas import (
    FleetCreateRequest,
    FleetUpdateRequest,
    FleetVehicleAddRequest,
    GeofencePolygonGeoJson,
)
from app.domains.vehicles.types import (
    VehicleReference,
    VehicleStatus,
    VehicleSummary,
)
from tests.builders import build_fleet_record, build_membership_record, fake_db_session


@pytest.mark.asyncio
async def test_create_fleet_creates_fleet_with_zero_vehicle_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_fleet() returns a fresh fleet with vehicle_count 0 (F-E1)."""
    inserted = build_fleet_record()

    async def no_existing(db: AsyncSession, fleet_code: str) -> None:
        return None

    async def insert(db: AsyncSession, values: dict[str, object]) -> FleetModel:
        return inserted

    async def zero_count(db: AsyncSession, fleet_id: UUID) -> int:
        return 0

    monkeypatch.setattr(fleet_repository, "find_by_fleet_code", no_existing)
    monkeypatch.setattr(fleet_repository, "insert", insert)
    monkeypatch.setattr(
        fleet_repository, "count_active_memberships_by_fleet", zero_count
    )

    response = await fleet_service.create_fleet(
        fake_db_session(),
        FleetCreateRequest(fleet_code=inserted.fleet_code, name=inserted.name),
    )

    assert response.fleet_id == inserted.fleet_id
    assert response.vehicle_count == 0


@pytest.mark.asyncio
async def test_create_fleet_rejects_duplicate_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_fleet() raises when the fleet code already exists (F-E1)."""
    existing = build_fleet_record()

    async def find_existing(db: AsyncSession, fleet_code: str) -> FleetModel:
        return existing

    monkeypatch.setattr(fleet_repository, "find_by_fleet_code", find_existing)

    with pytest.raises(FleetConflictError):
        await fleet_service.create_fleet(
            fake_db_session(),
            FleetCreateRequest(fleet_code=existing.fleet_code, name="Another Name"),
        )


@pytest.mark.asyncio
async def test_add_vehicle_to_fleet_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """add_vehicle_to_fleet() opens a new membership and enriches the VIN (F-E1)."""
    fleet_record = build_fleet_record()
    vehicle_id = uuid4()
    vehicle_reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    inserted = build_membership_record(
        fleet_id=fleet_record.fleet_id, vehicle_id=vehicle_id
    )

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return vehicle_reference

    async def no_active_membership(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    async def insert_membership(
        db: AsyncSession, **kwargs: object
    ) -> FleetVehicleMembershipModel:
        return inserted

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(
        fleet_repository, "find_active_membership_by_vehicle", no_active_membership
    )
    monkeypatch.setattr(fleet_repository, "insert_membership", insert_membership)

    response = await fleet_service.add_vehicle_to_fleet(
        fake_db_session(),
        fleet_record.fleet_id,
        FleetVehicleAddRequest(vehicle_vin=vehicle_reference.vin),
    )

    assert response.fleet_vehicle_membership_id == inserted.fleet_vehicle_membership_id
    assert response.vehicle_vin == vehicle_reference.vin
    assert response.removed_at is None


@pytest.mark.asyncio
async def test_add_vehicle_to_fleet_rejects_unknown_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_vehicle_to_fleet() raises when the VIN doesn't resolve to a vehicle (F-E1)."""
    fleet_record = build_fleet_record()

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def no_vehicle(db: AsyncSession, vin: str) -> None:
        return None

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", no_vehicle
    )

    with pytest.raises(FleetVehicleNotFoundError):
        await fleet_service.add_vehicle_to_fleet(
            fake_db_session(),
            fleet_record.fleet_id,
            FleetVehicleAddRequest(vehicle_vin="1HGBH41JXMN109186"),
        )


@pytest.mark.asyncio
async def test_add_vehicle_to_fleet_rejects_vehicle_in_another_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_vehicle_to_fleet() never silently steals a vehicle from another fleet (F-E1)."""
    fleet_record = build_fleet_record()
    other_fleet_id = uuid4()
    vehicle_id = uuid4()
    vehicle_reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    membership_elsewhere = build_membership_record(
        fleet_id=other_fleet_id, vehicle_id=vehicle_id
    )

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return vehicle_reference

    async def active_membership(
        db: AsyncSession, vehicle_id: UUID
    ) -> FleetVehicleMembershipModel:
        return membership_elsewhere

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("insert_membership must not run on a conflict")

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(
        fleet_repository, "find_active_membership_by_vehicle", active_membership
    )
    monkeypatch.setattr(fleet_repository, "insert_membership", fail_if_called)

    with pytest.raises(FleetMembershipConflictError):
        await fleet_service.add_vehicle_to_fleet(
            fake_db_session(),
            fleet_record.fleet_id,
            FleetVehicleAddRequest(vehicle_vin=vehicle_reference.vin),
        )


@pytest.mark.asyncio
async def test_add_vehicle_to_fleet_is_idempotent_for_same_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding a vehicle already in this fleet is a no-op returning the existing row (F-E1)."""
    fleet_record = build_fleet_record()
    vehicle_id = uuid4()
    vehicle_reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    existing_membership = build_membership_record(
        fleet_id=fleet_record.fleet_id, vehicle_id=vehicle_id
    )

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return vehicle_reference

    async def active_membership(
        db: AsyncSession, vehicle_id: UUID
    ) -> FleetVehicleMembershipModel:
        return existing_membership

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("insert_membership must not run when already a member")

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(
        fleet_repository, "find_active_membership_by_vehicle", active_membership
    )
    monkeypatch.setattr(fleet_repository, "insert_membership", fail_if_called)

    response = await fleet_service.add_vehicle_to_fleet(
        fake_db_session(),
        fleet_record.fleet_id,
        FleetVehicleAddRequest(vehicle_vin=vehicle_reference.vin),
    )

    assert (
        response.fleet_vehicle_membership_id
        == existing_membership.fleet_vehicle_membership_id
    )


@pytest.mark.asyncio
async def test_remove_vehicle_from_fleet_closes_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remove_vehicle_from_fleet() closes the vehicle's active membership (F-E1)."""
    fleet_record = build_fleet_record()
    vehicle_id = uuid4()
    active_membership = build_membership_record(
        fleet_id=fleet_record.fleet_id, vehicle_id=vehicle_id
    )
    closed: list[FleetVehicleMembershipModel] = []

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def find_active(
        db: AsyncSession, vehicle_id: UUID
    ) -> FleetVehicleMembershipModel:
        return active_membership

    async def close_membership(
        db: AsyncSession,
        membership_record: FleetVehicleMembershipModel,
        **kwargs: object,
    ) -> FleetVehicleMembershipModel:
        closed.append(membership_record)
        return membership_record

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        fleet_repository, "find_active_membership_by_vehicle", find_active
    )
    monkeypatch.setattr(fleet_repository, "close_membership", close_membership)

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return VehicleReference(
            vehicle_id=vehicle_id, vin=vin, battery_capacity_kwh=None
        )

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )

    await fleet_service.remove_vehicle_from_fleet(
        fake_db_session(), fleet_record.fleet_id, "1HGBH41JXMN109186"
    )

    assert closed == [active_membership]


@pytest.mark.asyncio
async def test_remove_vehicle_from_fleet_rejects_when_no_active_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remove_vehicle_from_fleet() raises when the vehicle has no active membership (F-E1)."""
    fleet_record = build_fleet_record()

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def no_active(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        fleet_repository, "find_active_membership_by_vehicle", no_active
    )

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return VehicleReference(vehicle_id=uuid4(), vin=vin, battery_capacity_kwh=None)

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )

    with pytest.raises(FleetMembershipNotFoundError):
        await fleet_service.remove_vehicle_from_fleet(
            fake_db_session(), fleet_record.fleet_id, "1HGBH41JXMN109186"
        )


@pytest.mark.asyncio
async def test_remove_vehicle_from_fleet_rejects_unknown_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remove_vehicle_from_fleet() raises when the VIN resolves to no vehicle (F-E1)."""
    fleet_record = build_fleet_record()

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def no_vehicle(db: AsyncSession, vin: str) -> None:
        return None

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", no_vehicle
    )

    with pytest.raises(FleetVehicleNotFoundError):
        await fleet_service.remove_vehicle_from_fleet(
            fake_db_session(), fleet_record.fleet_id, "1HGBH41JXMN109186"
        )


@pytest.mark.asyncio
async def test_list_fleet_vehicles_enriches_each_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_fleet_vehicles() enriches each membership with the vehicle summary (F-E1)."""
    fleet_record = build_fleet_record()
    vehicle_id = uuid4()
    membership = build_membership_record(
        fleet_id=fleet_record.fleet_id, vehicle_id=vehicle_id
    )
    vehicle_summary = VehicleSummary(
        vehicle_id=vehicle_id,
        vin="1HGBH41JXMN109186",
        license_plate="TEST-001",
        status=VehicleStatus.ACTIVE,
    )

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def list_active(
        db: AsyncSession, fleet_id: UUID, *, offset: int, limit: int
    ) -> list[FleetVehicleMembershipModel]:
        return [membership]

    async def count_active(db: AsyncSession, fleet_id: UUID) -> int:
        return 1

    async def resolve_summary(db: AsyncSession, vehicle_id: UUID) -> VehicleSummary:
        return vehicle_summary

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        fleet_repository, "list_active_memberships_by_fleet", list_active
    )
    monkeypatch.setattr(
        fleet_repository, "count_active_memberships_by_fleet", count_active
    )
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_summary_by_id", resolve_summary
    )

    response = await fleet_service.list_fleet_vehicles(
        fake_db_session(), fleet_record.fleet_id
    )

    assert response.total == 1
    assert response.items[0].vin == vehicle_summary.vin
    assert response.items[0].license_plate == vehicle_summary.license_plate


@pytest.mark.asyncio
async def test_create_fleet_rejects_unknown_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parent that is not a live fleet is a 404, before any insert (FL-02)."""

    async def no_fleet(db: AsyncSession, fleet_id: UUID) -> None:
        return None

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("insert must not run for an unknown parent")

    monkeypatch.setattr(fleet_repository, "get_by_id", no_fleet)
    monkeypatch.setattr(fleet_repository, "insert", fail_if_called)

    with pytest.raises(FleetParentNotFoundError):
        await fleet_service.create_fleet(
            fake_db_session(),
            FleetCreateRequest(name="Depot 1", parent_fleet_id=uuid4()),
        )


@pytest.mark.asyncio
async def test_update_fleet_refuses_a_move_under_its_own_sub_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving region under its grandchild depot would close a loop (FL-02)."""
    region = build_fleet_record()
    branch = build_fleet_record()
    branch.parent_fleet_id = region.fleet_id
    depot = build_fleet_record()
    depot.parent_fleet_id = branch.fleet_id
    fleets_by_id = {fleet.fleet_id: fleet for fleet in (region, branch, depot)}

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel | None:
        return fleets_by_id.get(fleet_id)

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("update must not run for a loop")

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(fleet_repository, "update_fields", fail_if_called)

    for new_parent in (depot, region):
        with pytest.raises(FleetHierarchyLoopError):
            await fleet_service.update_fleet(
                fake_db_session(),
                region.fleet_id,
                FleetUpdateRequest(parent_fleet_id=new_parent.fleet_id),
            )


@pytest.mark.asyncio
async def test_soft_delete_fleet_refuses_a_fleet_with_sub_fleets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fleet with live sub-fleets stays, and its members stay too (FL-08)."""
    fleet_record = build_fleet_record()

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def count_child_fleets(db: AsyncSession, fleet_id: UUID) -> int:
        return 1

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("nothing may be closed or deleted")

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(fleet_repository, "count_child_fleets", count_child_fleets)
    monkeypatch.setattr(
        fleet_repository, "list_all_active_memberships_by_fleet", fail_if_called
    )
    monkeypatch.setattr(fleet_repository, "soft_delete", fail_if_called)

    with pytest.raises(FleetHasSubFleetsError):
        await fleet_service.soft_delete_fleet(fake_db_session(), fleet_record.fleet_id)


@pytest.mark.asyncio
async def test_soft_delete_fleet_closes_active_memberships_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """soft_delete_fleet() closes every open membership before deleting (F-E1)."""
    fleet_record = build_fleet_record()
    memberships = [
        build_membership_record(fleet_id=fleet_record.fleet_id, vehicle_id=uuid4())
        for _ in range(2)
    ]
    closed: list[FleetVehicleMembershipModel] = []

    async def list_all_active(
        db: AsyncSession, fleet_id: UUID
    ) -> list[FleetVehicleMembershipModel]:
        return memberships

    async def close_membership(
        db: AsyncSession,
        membership_record: FleetVehicleMembershipModel,
        **kwargs: object,
    ) -> FleetVehicleMembershipModel:
        closed.append(membership_record)
        return membership_record

    async def soft_delete(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def count_child_fleets(db: AsyncSession, fleet_id: UUID) -> int:
        return 0

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(fleet_repository, "count_child_fleets", count_child_fleets)
    monkeypatch.setattr(
        fleet_repository, "list_all_active_memberships_by_fleet", list_all_active
    )
    monkeypatch.setattr(fleet_repository, "close_membership", close_membership)
    monkeypatch.setattr(fleet_repository, "soft_delete", soft_delete)

    deletion_response = await fleet_service.soft_delete_fleet(
        fake_db_session(), fleet_record.fleet_id
    )

    assert len(closed) == 2
    assert deletion_response == {"message": "Fleet deleted successfully"}


@pytest.mark.asyncio
async def test_get_fleet_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_fleet() raises for an unknown fleet ID (F-E1)."""

    async def no_fleet(db: AsyncSession, fleet_id: UUID) -> None:
        return None

    monkeypatch.setattr(fleet_repository, "get_by_id", no_fleet)

    with pytest.raises(FleetNotFoundError):
        await fleet_service.get_fleet(fake_db_session(), uuid4())


@pytest.mark.asyncio
async def test_list_fleet_vehicles_keeps_member_whose_vehicle_was_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member whose vehicle no longer resolves is listed, so total matches (F-E1).

    Regression: such members were skipped while `total` still counted them,
    giving short pages and an overstated total.
    """
    fleet_record = build_fleet_record()
    memberships = [
        build_membership_record(fleet_id=fleet_record.fleet_id, vehicle_id=uuid4())
        for _ in range(2)
    ]
    deleted_vehicle_id = memberships[1].vehicle_id

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def list_active(
        db: AsyncSession, fleet_id: UUID, *, offset: int, limit: int
    ) -> list[FleetVehicleMembershipModel]:
        return memberships

    async def count_active(db: AsyncSession, fleet_id: UUID) -> int:
        return 2

    async def resolve_summary(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleSummary | None:
        if vehicle_id == deleted_vehicle_id:
            return None
        return VehicleSummary(
            vehicle_id=vehicle_id,
            vin="1HGBH41JXMN109186",
            license_plate="TEST-001",
            status=VehicleStatus.ACTIVE,
        )

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        fleet_repository, "list_active_memberships_by_fleet", list_active
    )
    monkeypatch.setattr(
        fleet_repository, "count_active_memberships_by_fleet", count_active
    )
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_summary_by_id", resolve_summary
    )

    response = await fleet_service.list_fleet_vehicles(
        fake_db_session(), fleet_record.fleet_id
    )

    assert response.total == len(response.items) == 2
    assert response.items[1].vehicle_id == deleted_vehicle_id
    assert response.items[1].vin is None
    assert response.items[1].status is None


@pytest.mark.asyncio
async def test_list_fleets_by_unknown_vehicle_vin_returns_an_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /fleets?vehicle_vin= with an unknown VIN is an empty page, not a 404 (F-E1)."""

    async def no_vehicle(db: AsyncSession, vin: str) -> None:
        return None

    async def unexpected_query(*args: object, **kwargs: object) -> None:
        raise AssertionError("no fleet query expected for an unknown VIN")

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", no_vehicle
    )
    monkeypatch.setattr(fleet_repository, "list_all", unexpected_query)
    monkeypatch.setattr(fleet_repository, "count", unexpected_query)

    response = await fleet_service.list_fleets(
        fake_db_session(), vehicle_vin="1HGBH41JXMN109186"
    )

    assert response.items == []
    assert response.total == 0


@pytest.mark.asyncio
async def test_list_fleets_passes_search_and_vehicle_filters_to_both_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The q and vehicle_vin filters reach both the page and the count (F-E1)."""
    vehicle_id = uuid4()
    seen: dict[str, dict[str, object]] = {}

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return VehicleReference(
            vehicle_id=vehicle_id, vin=vin, battery_capacity_kwh=None
        )

    async def list_all(db: AsyncSession, **kwargs: object) -> list[FleetModel]:
        seen["list"] = kwargs
        return []

    async def count(db: AsyncSession, **kwargs: object) -> int:
        seen["count"] = kwargs
        return 0

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(fleet_repository, "list_all", list_all)
    monkeypatch.setattr(fleet_repository, "count", count)

    await fleet_service.list_fleets(
        fake_db_session(), search_text="hanoi", vehicle_vin="1HGBH41JXMN109186"
    )

    for query_kwargs in (seen["list"], seen["count"]):
        assert query_kwargs["search_text"] == "hanoi"
        assert query_kwargs["vehicle_id"] == vehicle_id


@pytest.mark.asyncio
async def test_list_fleet_vehicles_filters_by_status_and_text_then_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status/q filter every resolved member, then page in memory (F-E1)."""
    fleet_record = build_fleet_record()
    summaries = {
        uuid4(): ("1HGBH41JXMN100001", "51C-111.11", VehicleStatus.ACTIVE),
        uuid4(): ("1HGBH41JXMN100002", "51C-222.22", VehicleStatus.MAINTENANCE),
        uuid4(): ("1HGBH41JXMN100003", "30A-333.33", VehicleStatus.ACTIVE),
        uuid4(): ("1HGBH41JXMN100004", "51C-444.44", VehicleStatus.ACTIVE),
    }
    memberships = [
        build_membership_record(fleet_id=fleet_record.fleet_id, vehicle_id=vehicle_id)
        for vehicle_id in summaries
    ]

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def list_all_active(
        db: AsyncSession, fleet_id: UUID
    ) -> list[FleetVehicleMembershipModel]:
        return list(memberships)

    async def resolve_summary(db: AsyncSession, vehicle_id: UUID) -> VehicleSummary:
        vin, license_plate, vehicle_status = summaries[vehicle_id]
        return VehicleSummary(
            vehicle_id=vehicle_id,
            vin=vin,
            license_plate=license_plate,
            status=vehicle_status,
        )

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        fleet_repository, "list_all_active_memberships_by_fleet", list_all_active
    )
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_summary_by_id", resolve_summary
    )

    response = await fleet_service.list_fleet_vehicles(
        fake_db_session(),
        fleet_record.fleet_id,
        page=1,
        page_size=1,
        status_filter=VehicleStatus.ACTIVE,
        search_text="51c",
    )

    # ACTIVE and plate containing "51c" (case-insensitive): vehicles 1 and 4.
    assert response.total == 2
    assert len(response.items) == 1
    assert response.items[0].license_plate in {"51C-111.11", "51C-444.44"}


@pytest.mark.asyncio
async def test_close_fleet_membership_closes_an_open_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE /fleets/{id}/memberships/{id} closes an orphaned member (#84, D8)."""
    fleet_record = build_fleet_record()
    membership = build_membership_record(
        fleet_id=fleet_record.fleet_id, vehicle_id=uuid4()
    )
    closed: list[FleetVehicleMembershipModel] = []

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def get_membership(
        db: AsyncSession, membership_id: UUID
    ) -> FleetVehicleMembershipModel:
        return membership

    async def close_membership(
        db: AsyncSession,
        membership_record: FleetVehicleMembershipModel,
        **kwargs: object,
    ) -> FleetVehicleMembershipModel:
        closed.append(membership_record)
        return membership_record

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(fleet_repository, "get_membership_by_id", get_membership)
    monkeypatch.setattr(fleet_repository, "close_membership", close_membership)

    await fleet_service.close_fleet_membership(
        fake_db_session(), fleet_record.fleet_id, membership.fleet_vehicle_membership_id
    )

    assert closed == [membership]


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["other_fleet", "already_closed", "unknown"])
async def test_close_fleet_membership_rejects_a_membership_not_open_in_fleet(
    monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    """Another fleet's, a closed or an unknown membership is a 404 (#84, D8)."""
    fleet_record = build_fleet_record()
    membership: FleetVehicleMembershipModel | None = build_membership_record(
        fleet_id=uuid4() if problem == "other_fleet" else fleet_record.fleet_id,
        vehicle_id=uuid4(),
        removed_at=datetime.now(timezone.utc) if problem == "already_closed" else None,
    )
    if problem == "unknown":
        membership = None

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def get_membership(
        db: AsyncSession, membership_id: UUID
    ) -> FleetVehicleMembershipModel | None:
        return membership

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(fleet_repository, "get_membership_by_id", get_membership)

    with pytest.raises(FleetMembershipNotFoundError):
        await fleet_service.close_fleet_membership(
            fake_db_session(), fleet_record.fleet_id, uuid4()
        )


def test_geofence_boundary_round_trips_through_postgis_shape() -> None:
    """A ring converted to WKB and back keeps its positions and order (F-A5)."""
    ring = [
        (106.70, 10.77),
        (106.71, 10.77),
        (106.71, 10.78),
        (106.70, 10.78),
        (106.70, 10.77),
    ]
    boundary = fleet_service.to_geofence_boundary(
        GeofencePolygonGeoJson(coordinates=[ring])
    )

    round_tripped = fleet_service.to_geofence_polygon_geojson(boundary)

    assert round_tripped.type == "Polygon"
    assert round_tripped.coordinates == [ring]


@pytest.mark.asyncio
async def test_get_geofence_rejects_a_geofence_of_another_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A geofence is only reachable under its own fleet (F-A5)."""
    fleet_record = build_fleet_record()
    other_fleet_geofence = GeofenceModel(
        geofence_id=uuid4(),
        fleet_id=uuid4(),
        name="Depot",
        boundary=None,
    )

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def get_geofence(db: AsyncSession, geofence_id: UUID) -> GeofenceModel:
        return other_fleet_geofence

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(fleet_repository, "get_geofence_by_id", get_geofence)

    with pytest.raises(GeofenceNotFoundError):
        await fleet_service.get_geofence(
            fake_db_session(), fleet_record.fleet_id, other_fleet_geofence.geofence_id
        )


@pytest.mark.asyncio
async def test_list_active_member_vehicle_ids_rejects_unknown_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public member-ID lookup raises for an unknown fleet (F-E1)."""

    async def no_fleet(db: AsyncSession, fleet_id: UUID) -> None:
        return None

    monkeypatch.setattr(fleet_repository, "get_by_id", no_fleet)

    with pytest.raises(FleetNotFoundError):
        await fleet_service.list_active_member_vehicle_ids(fake_db_session(), uuid4())


@pytest.mark.asyncio
async def test_list_active_member_vehicle_ids_returns_oldest_member_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Member vehicle IDs come back in joining order (F-E1)."""
    fleet_record = build_fleet_record()
    older = build_membership_record(fleet_id=fleet_record.fleet_id, vehicle_id=uuid4())
    newer = build_membership_record(fleet_id=fleet_record.fleet_id, vehicle_id=uuid4())
    older.added_at = newer.added_at - timedelta(days=1)

    async def get_by_id(db: AsyncSession, fleet_id: UUID) -> FleetModel:
        return fleet_record

    async def list_all_active(
        db: AsyncSession, fleet_id: UUID
    ) -> list[FleetVehicleMembershipModel]:
        return [newer, older]

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        fleet_repository, "list_all_active_memberships_by_fleet", list_all_active
    )

    vehicle_ids = await fleet_service.list_active_member_vehicle_ids(
        fake_db_session(), fleet_record.fleet_id
    )

    assert vehicle_ids == [older.vehicle_id, newer.vehicle_id]


@pytest.mark.asyncio
async def test_find_current_fleet_id_by_vehicle_follows_the_open_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vehicle's current fleet is its open membership's fleet, else None (F-E1)."""
    fleet_id = uuid4()
    vehicle_in_fleet = uuid4()
    membership = build_membership_record(fleet_id=fleet_id, vehicle_id=vehicle_in_fleet)

    async def find_active(
        db: AsyncSession, vehicle_id: UUID
    ) -> FleetVehicleMembershipModel | None:
        return membership if vehicle_id == vehicle_in_fleet else None

    monkeypatch.setattr(
        fleet_repository, "find_active_membership_by_vehicle", find_active
    )

    assert (
        await fleet_service.find_current_fleet_id_by_vehicle(
            fake_db_session(), vehicle_in_fleet
        )
        == fleet_id
    )
    assert (
        await fleet_service.find_current_fleet_id_by_vehicle(fake_db_session(), uuid4())
        is None
    )
