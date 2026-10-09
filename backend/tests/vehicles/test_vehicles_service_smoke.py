"""Smoke tests for the vehicles service: vehicle CRUD and the model catalog."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.repository as vehicle_repository
import app.domains.vehicles.router as vehicle_router
import app.domains.vehicles.service as vehicle_service
from app.domains.vehicles.exceptions import (
    VehicleModelConflictError,
    VehicleModelNotFoundError,
    VehicleNotFoundError,
)
from app.domains.vehicles.models import VehicleModel, VehicleModelModel
from app.domains.vehicles.schemas import (
    VehicleCreateRequest,
    VehicleModelCreateRequest,
)
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
from tests.builders import (
    build_vehicle_model_record,
    build_vehicle_record,
    fake_db_session,
)


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
        assert values["acquired_at"] is not None
        return record

    async def existing_vehicle_model(
        db: AsyncSession, vehicle_model_id: UUID, *, include_deleted: bool = False
    ) -> VehicleModelModel:
        return build_vehicle_model_record()

    monkeypatch.setattr(
        vehicle_repository, "get_vehicle_model_by_id", existing_vehicle_model
    )
    monkeypatch.setattr(vehicle_repository, "find_by_license_plate", no_existing_plate)
    monkeypatch.setattr(vehicle_repository, "find_by_vin", no_existing_vin)
    monkeypatch.setattr(vehicle_repository, "insert", insert_vehicle)

    response = await vehicle_service.create_vehicle(
        fake_db_session(),
        VehicleCreateRequest(
            organization_id=record.organization_id,
            license_plate=record.license_plate,
            vin=record.vin,
            vehicle_model_id=record.vehicle_model_id,
            year=record.year,
            status=record.status,
        ),
    )

    assert response.vehicle_id == record.vehicle_id
    assert response.vin == record.vin


@pytest.mark.asyncio
async def test_vehicle_service_soft_delete_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft delete stamps deleted_at and sets the vehicle INACTIVE with a reason.

    The INACTIVE rule (DM-25) is the service's decision; the repository only
    persists the values it is given.
    """
    record = build_vehicle_record()
    updated_values: dict[str, object] = {}

    async def update_fields(
        db_session: AsyncSession,
        vehicle_id: UUID,
        values: dict[str, object],
        *,
        change_reason: str,
    ) -> VehicleModel:
        updated_values.update(values)
        return record

    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)

    await vehicle_service.soft_delete_vehicle(fake_db_session(), record.vehicle_id)

    assert updated_values["status"] is VehicleStatus.INACTIVE
    assert updated_values["status_reason"]
    deleted_at = updated_values["deleted_at"]
    assert isinstance(deleted_at, datetime)
    assert deleted_at.tzinfo is not None


@pytest.mark.asyncio
async def test_vehicle_service_soft_delete_rejects_missing_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft-deleting an unknown or already-deleted vehicle raises NotFound."""

    async def update_fields(
        db_session: AsyncSession,
        vehicle_id: UUID,
        values: dict[str, object],
        *,
        change_reason: str,
    ) -> None:
        return None

    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)

    with pytest.raises(VehicleNotFoundError):
        await vehicle_service.soft_delete_vehicle(fake_db_session(), uuid4())


@pytest.mark.asyncio
async def test_soft_delete_vehicle_endpoint_keeps_confirmation_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE /vehicles/{id} still answers with the same confirmation message."""

    async def soft_delete_vehicle(db_session: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(vehicle_service, "soft_delete_vehicle", soft_delete_vehicle)

    response_body = await vehicle_router.soft_delete_vehicle_endpoint(
        uuid4(), fake_db_session()
    )

    assert response_body == {"message": "Vehicle deleted successfully"}


@pytest.mark.asyncio
async def test_list_vehicles_normalizes_page_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_vehicles() clamps page/page_size and passes the offset as a keyword."""
    list_arguments: dict[str, object] = {}

    async def list_all(
        db_session: AsyncSession,
        *,
        offset: int,
        limit: int,
        status_filter: VehicleStatus | None = None,
    ) -> list[VehicleModel]:
        list_arguments.update(offset=offset, limit=limit, status_filter=status_filter)
        return [build_vehicle_record()]

    async def count(
        db_session: AsyncSession,
        *,
        status_filter: VehicleStatus | None = None,
    ) -> int:
        return 1

    monkeypatch.setattr(vehicle_repository, "list_all", list_all)
    monkeypatch.setattr(vehicle_repository, "count", count)

    vehicle_list_response = await vehicle_service.list_vehicles(
        fake_db_session(),
        page=3,
        page_size=settings.API_MAX_PAGE_SIZE + 1,
        status_filter=VehicleStatus.ACTIVE,
    )

    assert list_arguments == {
        "offset": 2 * settings.API_MAX_PAGE_SIZE,
        "limit": settings.API_MAX_PAGE_SIZE,
        "status_filter": VehicleStatus.ACTIVE,
    }
    assert vehicle_list_response.page == 3
    assert vehicle_list_response.page_size == settings.API_MAX_PAGE_SIZE
    assert vehicle_list_response.total == 1


@pytest.mark.asyncio
async def test_create_vehicle_rejects_unknown_vehicle_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vehicle can only be created for a model that is in the catalog."""
    record = build_vehicle_record()

    async def no_vehicle_model(
        db: AsyncSession, vehicle_model_id: UUID, *, include_deleted: bool = False
    ) -> None:
        return None

    monkeypatch.setattr(vehicle_repository, "get_vehicle_model_by_id", no_vehicle_model)

    with pytest.raises(VehicleModelNotFoundError):
        await vehicle_service.create_vehicle(
            fake_db_session(),
            VehicleCreateRequest(
                organization_id=record.organization_id,
                license_plate=record.license_plate,
                vin=record.vin,
                vehicle_model_id=record.vehicle_model_id,
                year=record.year,
            ),
        )


@pytest.mark.asyncio
async def test_resolve_vehicle_reference_uses_model_battery_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pack capacity handed to other domains is the model's nominal figure."""
    record = build_vehicle_record()
    vehicle_model_record = build_vehicle_model_record()
    vehicle_model_record.nominal_battery_capacity_kwh = Decimal("282.0")

    async def get_by_id(db: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    async def get_vehicle_model_by_id(
        db: AsyncSession, vehicle_model_id: UUID, *, include_deleted: bool = False
    ) -> VehicleModelModel:
        assert include_deleted is True
        return vehicle_model_record

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicle_repository, "get_vehicle_model_by_id", get_vehicle_model_by_id
    )

    reference = await vehicle_service.resolve_vehicle_reference_by_id(
        fake_db_session(), record.vehicle_id
    )

    assert reference is not None
    assert reference.battery_capacity_kwh == 282.0


@pytest.mark.asyncio
async def test_create_vehicle_model_rejects_duplicate_make_and_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two live catalog models cannot share make and model name."""

    async def existing(
        db: AsyncSession, make: str, model_name: str
    ) -> VehicleModelModel:
        return build_vehicle_model_record()

    monkeypatch.setattr(
        vehicle_repository, "find_vehicle_model_by_make_and_name", existing
    )

    with pytest.raises(VehicleModelConflictError):
        await vehicle_service.create_vehicle_model(
            fake_db_session(),
            VehicleModelCreateRequest(make="Tri-Ring", model_name="EVT-400"),
        )
