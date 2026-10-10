"""Smoke tests for the vehicles service: vehicle CRUD and the model catalog."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.repository as vehicle_repository
import app.domains.vehicles.router as vehicle_router
import app.domains.vehicles.service as vehicle_service
from app.domains.identity.types import Principal
from app.domains.vehicles.exceptions import (
    VehicleModelConflictError,
    VehicleModelNotFoundError,
    VehicleNotFoundError,
    VehicleTransferInvalidError,
)
from app.domains.vehicles.models import VehicleModel, VehicleModelModel
from app.domains.vehicles.schemas import (
    VehicleCreateRequest,
    VehicleModelCreateRequest,
    VehicleModelUpdateRequest,
    VehicleOwnershipTransferRequest,
)
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
from tests.builders import (
    build_vehicle_model_record,
    build_vehicle_record,
    fake_db_session,
)
from tests.principals import build_internal_principal, build_principal


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
        principal=build_principal(organization_id=record.organization_id),
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
        changed_by: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> VehicleModel:
        updated_values.update(values)
        return record

    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)

    await vehicle_service.soft_delete_vehicle(
        fake_db_session(), record.vehicle_id, principal=build_principal()
    )

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
        changed_by: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> None:
        return None

    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)

    with pytest.raises(VehicleNotFoundError):
        await vehicle_service.soft_delete_vehicle(
            fake_db_session(), uuid4(), principal=build_principal()
        )


@pytest.mark.asyncio
async def test_soft_delete_vehicle_endpoint_keeps_confirmation_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE /vehicles/{id} still answers with the same confirmation message."""

    async def soft_delete_vehicle(
        db_session: AsyncSession,
        vehicle_id: UUID,
        *,
        principal: Principal,
        reason: str | None = None,
    ) -> None:
        return None

    monkeypatch.setattr(vehicle_service, "soft_delete_vehicle", soft_delete_vehicle)

    response_body = await vehicle_router.soft_delete_vehicle_endpoint(
        uuid4(), None, build_principal(), fake_db_session()
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
        organization_id: UUID | None = None,
        search: str | None = None,
        vehicle_model_id: UUID | None = None,
        owner_organization_id: UUID | None = None,
    ) -> list[VehicleModel]:
        list_arguments.update(
            offset=offset,
            limit=limit,
            status_filter=status_filter,
            organization_id=organization_id,
            search=search,
            vehicle_model_id=vehicle_model_id,
            owner_organization_id=owner_organization_id,
        )
        return [build_vehicle_record()]

    async def count(
        db_session: AsyncSession,
        *,
        status_filter: VehicleStatus | None = None,
        organization_id: UUID | None = None,
        search: str | None = None,
        vehicle_model_id: UUID | None = None,
        owner_organization_id: UUID | None = None,
    ) -> int:
        return 1

    monkeypatch.setattr(vehicle_repository, "list_all", list_all)
    monkeypatch.setattr(vehicle_repository, "count", count)

    principal = build_principal()
    vehicle_list_response = await vehicle_service.list_vehicles(
        fake_db_session(),
        principal=principal,
        page=3,
        page_size=settings.API_MAX_PAGE_SIZE + 1,
        status_filter=VehicleStatus.ACTIVE,
        search="TEST",
    )

    assert list_arguments == {
        "offset": 2 * settings.API_MAX_PAGE_SIZE,
        "limit": settings.API_MAX_PAGE_SIZE,
        "status_filter": VehicleStatus.ACTIVE,
        "organization_id": principal.organization_id,
        "search": "TEST",
        "vehicle_model_id": None,
        "owner_organization_id": None,
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
            principal=build_principal(organization_id=record.organization_id),
        )


@pytest.mark.asyncio
async def test_resolve_vehicle_reference_uses_model_battery_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pack capacity handed to other domains is the model's nominal figure."""
    record = build_vehicle_record()
    vehicle_model_record = build_vehicle_model_record()
    vehicle_model_record.nominal_battery_capacity_kwh = Decimal("282.0")

    async def get_by_id(
        db: AsyncSession, vehicle_id: UUID, *, organization_id: UUID | None = None
    ) -> VehicleModel:
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


@pytest.mark.asyncio
async def test_get_vehicle_of_another_organization_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A customer principal's lookup is scoped to its organization (404)."""
    scopes: list[UUID | None] = []

    async def get_by_id(
        db: AsyncSession, vehicle_id: UUID, *, organization_id: UUID | None = None
    ) -> None:
        scopes.append(organization_id)
        return None

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    principal = build_principal()

    with pytest.raises(VehicleNotFoundError):
        await vehicle_service.get_vehicle(
            fake_db_session(), uuid4(), principal=principal
        )

    with pytest.raises(VehicleNotFoundError):
        await vehicle_service.get_vehicle(
            fake_db_session(), uuid4(), principal=build_internal_principal()
        )

    # Customers are scoped to their organization; internal staff see all.
    assert scopes == [principal.organization_id, None]


@pytest.mark.asyncio
async def test_transfer_vehicle_ownership_changes_owner_and_date_with_reason(
    monkeypatch: pytest.MonkeyPatch, own_organization_for_new_records: None
) -> None:
    """A transfer writes the buyer and the effective date; the typed reason is the history reason."""
    record = build_vehicle_record()
    record.acquired_at = datetime.now(timezone.utc) - timedelta(days=30)
    buyer_id = uuid4()
    effective_at = datetime.now(timezone.utc) - timedelta(days=1)
    written: dict[str, object] = {}

    async def get_by_id(
        db: AsyncSession, vehicle_id: UUID, *, organization_id: UUID | None = None
    ) -> VehicleModel:
        return record

    async def update_fields(
        db_session: AsyncSession,
        vehicle_id: UUID,
        values: dict[str, object],
        *,
        change_reason: str,
        changed_by: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> VehicleModel:
        written.update(values, change_reason=change_reason)
        return record

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)

    result = await vehicle_service.transfer_vehicle_ownership(
        fake_db_session(),
        record.vehicle_id,
        VehicleOwnershipTransferRequest(
            organization_id=buyer_id, acquired_at=effective_at, reason="Sold"
        ),
        principal=build_internal_principal(),
    )

    assert written == {
        "organization_id": buyer_id,
        "acquired_at": effective_at,
        "change_reason": "Sold",
    }
    assert result.previous_organization_id == record.organization_id
    assert result.organization_id == buyer_id


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_days", [-60, 2])
async def test_transfer_rejects_same_owner_and_bad_dates(
    monkeypatch: pytest.MonkeyPatch,
    own_organization_for_new_records: None,
    offset_days: int,
) -> None:
    """The buyer must differ, and the date must be after the last handover and not future."""
    record = build_vehicle_record()
    record.acquired_at = datetime.now(timezone.utc) - timedelta(days=30)

    async def get_by_id(
        db: AsyncSession, vehicle_id: UUID, *, organization_id: UUID | None = None
    ) -> VehicleModel:
        return record

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    internal = build_internal_principal()

    with pytest.raises(VehicleTransferInvalidError):
        await vehicle_service.transfer_vehicle_ownership(
            fake_db_session(),
            record.vehicle_id,
            VehicleOwnershipTransferRequest(
                organization_id=record.organization_id, reason="Same"
            ),
            principal=internal,
        )
    with pytest.raises(VehicleTransferInvalidError):
        await vehicle_service.transfer_vehicle_ownership(
            fake_db_session(),
            record.vehicle_id,
            VehicleOwnershipTransferRequest(
                organization_id=uuid4(),
                acquired_at=datetime.now(timezone.utc) + timedelta(days=offset_days),
                reason="Bad date",
            ),
            principal=internal,
        )


@pytest.mark.asyncio
async def test_update_vehicle_model_rejects_a_taken_make_and_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renaming a model onto another live model's make and name is a conflict."""
    record = build_vehicle_model_record()

    async def get_vehicle_model_by_id(
        db: AsyncSession, vehicle_model_id: UUID, *, include_deleted: bool = False
    ) -> VehicleModelModel:
        return record

    async def taken(db: AsyncSession, make: str, model_name: str) -> VehicleModelModel:
        return build_vehicle_model_record()

    monkeypatch.setattr(
        vehicle_repository, "get_vehicle_model_by_id", get_vehicle_model_by_id
    )
    monkeypatch.setattr(
        vehicle_repository, "find_vehicle_model_by_make_and_name", taken
    )

    with pytest.raises(VehicleModelConflictError):
        await vehicle_service.update_vehicle_model(
            fake_db_session(),
            record.vehicle_model_id,
            VehicleModelUpdateRequest(model_name="EVT-825"),
            principal=build_internal_principal(),
        )
