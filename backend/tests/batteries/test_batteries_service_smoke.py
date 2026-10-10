"""Smoke tests for the batteries service: fitting rules, the soft delete, capacity."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.batteries.repository as battery_repository
import app.domains.batteries.service as battery_service
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.domains.batteries.exceptions import (
    BatteryConflictError,
    BatteryVehicleNotFoundError,
)
from app.domains.batteries.models import BatteryModel, BatteryModelModel
from app.domains.batteries.schemas import BatteryInstallRequest
from app.domains.batteries.types import BatteryStatus
from app.domains.vehicles.types import VehicleReference
from tests.builders import fake_db_session
from tests.principals import build_internal_principal


def build_battery_record(
    *, vehicle_id: UUID | None = None, status: BatteryStatus = BatteryStatus.ACTIVE
) -> BatteryModel:
    """Create a minimal ORM battery."""
    now = datetime.now(timezone.utc)
    return BatteryModel(
        battery_id=uuid4(),
        serial_number="BAT-0001",
        battery_model_id=uuid4(),
        organization_id=uuid4(),
        acquired_at=now,
        vehicle_id=vehicle_id,
        installed_at=now if vehicle_id else None,
        status=status.value,
        created_at=now,
        updated_at=now,
    )


def _vehicle_reference(vehicle_id: UUID) -> VehicleReference:
    """Create a vehicle reference with a model capacity of 200 kWh."""
    return VehicleReference(
        vehicle_id=vehicle_id,
        vin="1HGBH41JXMN109186",
        organization_id=uuid4(),
        battery_capacity_kwh=200.0,
    )


@pytest.mark.asyncio
async def test_install_battery_sets_truck_and_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fitting writes the truck and the fitting time, with a fixed history reason."""
    record = build_battery_record()
    vehicle_id = uuid4()
    written: dict[str, Any] = {}

    async def get_by_id(
        db: AsyncSession, battery_id: UUID, *, organization_id: UUID | None = None
    ) -> BatteryModel:
        return record

    async def resolve(db: AsyncSession, requested_id: UUID) -> VehicleReference:
        return _vehicle_reference(requested_id)

    async def no_battery_in_truck(db: AsyncSession, requested_id: UUID) -> None:
        return None

    async def update_fields(
        db: AsyncSession,
        battery_id: UUID,
        values: dict[str, Any],
        *,
        change_reason: str,
        changed_by: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> BatteryModel:
        written.update(values, change_reason=change_reason)
        return record

    monkeypatch.setattr(battery_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(battery_repository, "find_by_vehicle_id", no_battery_in_truck)
    monkeypatch.setattr(battery_repository, "update_fields", update_fields)
    monkeypatch.setattr(vehicle_service, "resolve_vehicle_reference_by_id", resolve)

    await battery_service.install_battery(
        fake_db_session(),
        record.battery_id,
        BatteryInstallRequest(vehicle_id=vehicle_id),
        principal=build_internal_principal(),
    )

    assert written["vehicle_id"] == vehicle_id
    assert written["installed_at"].tzinfo is not None
    assert written["change_reason"] == battery_service.BATTERY_INSTALLED_REASON


@pytest.mark.asyncio
async def test_install_battery_refuses_a_second_pack_a_fitted_pack_and_an_unknown_truck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One battery per truck; a fitted or INACTIVE pack and an unknown truck are refused."""
    free_record = build_battery_record()
    truck_id = uuid4()
    records = {"current": free_record}

    async def get_by_id(
        db: AsyncSession, battery_id: UUID, *, organization_id: UUID | None = None
    ) -> BatteryModel:
        return records["current"]

    async def other_battery_in_truck(
        db: AsyncSession, requested_id: UUID
    ) -> BatteryModel:
        return build_battery_record(vehicle_id=requested_id)

    async def no_vehicle(db: AsyncSession, requested_id: UUID) -> None:
        return None

    async def resolve(db: AsyncSession, requested_id: UUID) -> VehicleReference:
        return _vehicle_reference(requested_id)

    monkeypatch.setattr(battery_repository, "get_by_id", get_by_id)
    internal = build_internal_principal()
    request = BatteryInstallRequest(vehicle_id=truck_id)

    records["current"] = build_battery_record(vehicle_id=uuid4())
    with pytest.raises(BatteryConflictError):
        await battery_service.install_battery(
            fake_db_session(), uuid4(), request, principal=internal
        )

    records["current"] = build_battery_record(status=BatteryStatus.INACTIVE)
    with pytest.raises(BatteryConflictError):
        await battery_service.install_battery(
            fake_db_session(), uuid4(), request, principal=internal
        )

    records["current"] = free_record
    monkeypatch.setattr(vehicle_service, "resolve_vehicle_reference_by_id", no_vehicle)
    with pytest.raises(BatteryVehicleNotFoundError):
        await battery_service.install_battery(
            fake_db_session(), uuid4(), request, principal=internal
        )

    monkeypatch.setattr(vehicle_service, "resolve_vehicle_reference_by_id", resolve)
    monkeypatch.setattr(
        battery_repository, "find_by_vehicle_id", other_battery_in_truck
    )
    with pytest.raises(BatteryConflictError):
        await battery_service.install_battery(
            fake_db_session(), uuid4(), request, principal=internal
        )


@pytest.mark.asyncio
async def test_soft_delete_battery_takes_it_out_of_its_truck_and_sets_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted battery is INACTIVE with a reason and no longer fitted (DM-25)."""
    record = build_battery_record(vehicle_id=uuid4())
    written: dict[str, Any] = {}

    async def update_fields(
        db: AsyncSession,
        battery_id: UUID,
        values: dict[str, Any],
        *,
        change_reason: str,
        changed_by: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> BatteryModel:
        written.update(values)
        return record

    monkeypatch.setattr(battery_repository, "update_fields", update_fields)

    await battery_service.soft_delete_battery(
        fake_db_session(),
        record.battery_id,
        principal=build_internal_principal(),
        reason="Entered by mistake",
    )

    assert written["status"] == "INACTIVE"
    assert written["status_reason"] == "Entered by mistake"
    assert written["vehicle_id"] is None and written["installed_at"] is None
    assert written["deleted_at"].tzinfo is not None


@pytest.mark.asyncio
async def test_pack_capacity_prefers_the_installed_battery_over_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry's vehicle reference carries the fitted pack's design capacity (VH-16)."""
    vehicle_id = uuid4()
    battery_model_record = BatteryModelModel(
        battery_model_id=uuid4(),
        manufacturer="CATL",
        model_name="LFP-300",
        chemistry="LFP",
        design_capacity_kwh=Decimal("300.0"),
    )

    async def resolve(db: AsyncSession, requested_id: UUID) -> VehicleReference:
        return _vehicle_reference(requested_id)

    async def battery_in_truck(db: AsyncSession, requested_id: UUID) -> BatteryModel:
        return build_battery_record(vehicle_id=requested_id)

    async def get_battery_model_by_id(
        db: AsyncSession, battery_model_id: UUID, *, include_deleted: bool = False
    ) -> BatteryModelModel:
        return battery_model_record

    monkeypatch.setattr(vehicle_service, "resolve_vehicle_reference_by_id", resolve)
    monkeypatch.setattr(battery_repository, "find_by_vehicle_id", battery_in_truck)
    monkeypatch.setattr(
        battery_repository, "get_battery_model_by_id", get_battery_model_by_id
    )

    reference = await telemetry_service._resolve_vehicle_reference_with_pack_capacity(
        fake_db_session(), vehicle_id
    )

    assert reference is not None
    assert reference.battery_capacity_kwh == 300.0

    battery_model_record.design_capacity_kwh = None
    fallback = await telemetry_service._resolve_vehicle_reference_with_pack_capacity(
        fake_db_session(), vehicle_id
    )
    assert fallback is not None
    assert fallback.battery_capacity_kwh == 200.0
