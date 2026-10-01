"""Smoke tests for the vehicles service: CRUD and the activation state machine (F-F2)."""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.repository as vehicle_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.schemas import VehicleCreateRequest
from app.domains.vehicles.types import (
    VehicleActivationStatus,
)
from tests.builders import build_vehicle_record, fake_db_session


@pytest.mark.asyncio
async def test_vehicle_service_creates_vehicle_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vehicle service creates a response when there's no unique conflict."""
    record = build_vehicle_record()

    async def no_existing_plate(db: AsyncSession, value: str) -> None:
        return None

    async def no_existing_vin(db: AsyncSession, value: str) -> None:
        return None

    async def insert_vehicle(db: AsyncSession, values: dict[str, Any]) -> VehicleModel:
        return record

    monkeypatch.setattr(vehicle_repository, "find_by_license_plate", no_existing_plate)
    monkeypatch.setattr(vehicle_repository, "find_by_vin", no_existing_vin)
    monkeypatch.setattr(vehicle_repository, "insert", insert_vehicle)

    response = await vehicle_service.create_vehicle(
        fake_db_session(),
        VehicleCreateRequest(
            license_plate=record.license_plate,
            vin=record.vin,
            make=record.make,
            model=record.model,
            year=record.year,
            status=record.status,
            battery_capacity_kwh=None,
        ),
    )

    assert response.vehicle_id == record.vehicle_id
    assert response.vin == record.vin


@pytest.mark.asyncio
async def test_vehicle_service_soft_delete_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The soft-delete service returns a message when the repository succeeds."""
    record = build_vehicle_record()

    async def soft_delete(db: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    monkeypatch.setattr(vehicle_repository, "soft_delete", soft_delete)

    result = await vehicle_service.soft_delete_vehicle(
        fake_db_session(), record.vehicle_id
    )

    assert result == {"message": "Vehicle deleted successfully"}


@pytest.mark.asyncio
async def test_mark_device_assigned_advances_pending_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_device_assigned() moves a PENDING vehicle to DEVICE_ASSIGNED (F-F2)."""
    record = build_vehicle_record(activation_status=VehicleActivationStatus.PENDING)
    updated_values: dict[str, object] = {}

    async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    async def update_fields(
        db_session: AsyncSession, vehicle_id: UUID, values: dict[str, object]
    ) -> VehicleModel:
        updated_values.update(values)
        return record

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)

    await vehicle_service.mark_device_assigned(fake_db_session(), record.vehicle_id)

    assert updated_values == {
        "activation_status": VehicleActivationStatus.DEVICE_ASSIGNED
    }


@pytest.mark.asyncio
async def test_mark_device_assigned_is_noop_past_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_device_assigned() doesn't regress a vehicle already past PENDING (F-F2)."""
    record = build_vehicle_record(activation_status=VehicleActivationStatus.ACTIVATED)

    async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    async def fail_if_called(
        db_session: AsyncSession, vehicle_id: UUID, values: dict[str, object]
    ) -> VehicleModel:
        raise AssertionError("update_fields should not be called")

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", fail_if_called)

    await vehicle_service.mark_device_assigned(fake_db_session(), record.vehicle_id)


@pytest.mark.asyncio
async def test_mark_vehicle_activated_advances_device_assigned_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_vehicle_activated() moves a vehicle to ACTIVATED (F-F2)."""
    record = build_vehicle_record(
        activation_status=VehicleActivationStatus.DEVICE_ASSIGNED
    )
    updated_values: dict[str, object] = {}

    async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    async def update_fields(
        db_session: AsyncSession, vehicle_id: UUID, values: dict[str, object]
    ) -> VehicleModel:
        updated_values.update(values)
        return record

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)

    await vehicle_service.mark_vehicle_activated(fake_db_session(), record.vehicle_id)

    assert updated_values == {"activation_status": VehicleActivationStatus.ACTIVATED}


@pytest.mark.asyncio
async def test_mark_vehicle_activated_is_noop_when_already_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_vehicle_activated() is idempotent once a vehicle is ACTIVATED (F-F2)."""
    record = build_vehicle_record(activation_status=VehicleActivationStatus.ACTIVATED)

    async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    async def fail_if_called(
        db_session: AsyncSession, vehicle_id: UUID, values: dict[str, object]
    ) -> VehicleModel:
        raise AssertionError("update_fields should not be called")

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", fail_if_called)

    await vehicle_service.mark_vehicle_activated(fake_db_session(), record.vehicle_id)


@pytest.mark.asyncio
async def test_get_vehicle_activation_summary_computes_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_vehicle_activation_summary() computes the success rate from two counts (F-F2)."""

    async def count_by_status(
        db_session: AsyncSession, activation_status: VehicleActivationStatus
    ) -> int:
        return {
            VehicleActivationStatus.DEVICE_ASSIGNED: 3,
            VehicleActivationStatus.ACTIVATED: 7,
        }[activation_status]

    monkeypatch.setattr(
        vehicle_repository, "count_by_activation_status", count_by_status
    )

    summary = await vehicle_service.get_vehicle_activation_summary(fake_db_session())

    assert summary.attempted_count == 10
    assert summary.activated_count == 7
    assert summary.activation_rate_percent == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_get_vehicle_activation_summary_handles_zero_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fleet with no provisioning attempts yet reports None, not a division by zero (F-F2)."""

    async def count_by_status(
        db_session: AsyncSession, activation_status: VehicleActivationStatus
    ) -> int:
        return 0

    monkeypatch.setattr(
        vehicle_repository, "count_by_activation_status", count_by_status
    )

    summary = await vehicle_service.get_vehicle_activation_summary(fake_db_session())

    assert summary.attempted_count == 0
    assert summary.activated_count == 0
    assert summary.activation_rate_percent is None
