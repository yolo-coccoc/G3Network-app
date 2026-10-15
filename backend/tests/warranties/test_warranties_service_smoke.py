"""Smoke tests for the warranties service: limits keys, object check, void rule."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.service as vehicle_service
import app.domains.warranties.repository as warranty_repository
import app.domains.warranties.service as warranty_service
from app.domains.identity.types import UserRole
from app.domains.vehicles.types import VehicleReference
from app.domains.warranties.exceptions import (
    WarrantyConflictError,
    WarrantyLimitsInvalidError,
    WarrantyNotFoundError,
    WarrantyObjectNotFoundError,
)
from app.domains.warranties.models import WarrantyModel
from app.domains.warranties.schemas import WarrantyCreateRequest
from app.domains.warranties.types import (
    WarrantyObjectKind,
    WarrantyStatus,
    WarrantyType,
)
from tests.builders import fake_db_session
from tests.principals import build_internal_principal, build_principal


def build_warranty_record(
    *, status: WarrantyStatus = WarrantyStatus.ACTIVE, ends_on: date | None = None
) -> WarrantyModel:
    """Create a minimal ORM truck warranty."""
    now = datetime.now(timezone.utc)
    return WarrantyModel(
        warranty_id=uuid4(),
        vehicle_id=uuid4(),
        warranty_type=WarrantyType.STANDARD.value,
        starts_on=date(2026, 1, 1),
        ends_on=ends_on or date(2031, 1, 1),
        status=status.value,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    ("object_kind", "limits"),
    [
        (WarrantyObjectKind.VEHICLE, {"distance_km": 200000}),
        (
            WarrantyObjectKind.BATTERY,
            {"charge_cycles": 3000, "energy_throughput_kwh": 1},
        ),
        (WarrantyObjectKind.TELEMATIC, {"operating_hours": 9000}),
        (WarrantyObjectKind.STATION, {"energy_delivered_kwh": 1e6, "session_count": 5}),
    ],
)
def test_validate_limits_accepts_the_keys_of_the_covered_object(
    object_kind: WarrantyObjectKind, limits: dict[str, float]
) -> None:
    """Each kind of object allows its own counter keys (VH-18, VH-19)."""
    assert warranty_service.validate_limits(object_kind, limits) == limits


@pytest.mark.parametrize(
    ("object_kind", "limits"),
    [
        (WarrantyObjectKind.VEHICLE, {"charge_cycles": 1}),
        (WarrantyObjectKind.STATION, {"distance_km": 1}),
        (WarrantyObjectKind.VEHICLE, {"distance_km": -5}),
    ],
)
def test_validate_limits_rejects_a_foreign_key_or_a_negative_reading(
    object_kind: WarrantyObjectKind, limits: dict[str, float]
) -> None:
    """A key of another kind of object, or a negative reading, is refused."""
    with pytest.raises(WarrantyLimitsInvalidError):
        warranty_service.validate_limits(object_kind, limits)


def test_create_request_needs_exactly_one_covered_object() -> None:
    """Zero or two links in the body are refused before the service runs."""
    starts_on, ends_on = date(2026, 1, 1), date(2027, 1, 1)
    with pytest.raises(ValueError):
        WarrantyCreateRequest(starts_on=starts_on, ends_on=ends_on)
    with pytest.raises(ValueError):
        WarrantyCreateRequest(
            vehicle_id=uuid4(),
            battery_id=uuid4(),
            starts_on=starts_on,
            ends_on=ends_on,
        )


@pytest.mark.asyncio
async def test_create_warranty_rejects_an_unknown_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The covered truck must exist (404 from the object's own domain)."""

    async def no_vehicle(db: AsyncSession, vehicle_id: UUID) -> VehicleReference | None:
        return None

    monkeypatch.setattr(vehicle_service, "resolve_vehicle_reference_by_id", no_vehicle)

    with pytest.raises(WarrantyObjectNotFoundError):
        await warranty_service.create_warranty(
            fake_db_session(),
            WarrantyCreateRequest(
                vehicle_id=uuid4(),
                starts_on=date(2026, 1, 1),
                ends_on=date(2027, 1, 1),
            ),
            principal=build_internal_principal(),
        )


@pytest.mark.asyncio
async def test_void_warranty_is_final_and_expiry_is_computed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A voided warranty cannot be voided again; ends_on in the past reads as expired."""
    voided = build_warranty_record(status=WarrantyStatus.VOIDED)

    async def get_by_id(
        db: AsyncSession, warranty_id: UUID, **_scope: object
    ) -> WarrantyModel:
        return voided

    monkeypatch.setattr(warranty_repository, "get_by_id", get_by_id)

    with pytest.raises(WarrantyConflictError):
        await warranty_service.void_warranty(
            fake_db_session(),
            voided.warranty_id,
            principal=build_internal_principal(),
            reason="Again",
        )

    past = build_warranty_record(
        ends_on=datetime.now(timezone.utc).date() - timedelta(days=1)
    )
    assert warranty_service.to_warranty_response(past).is_expired is True
    assert (
        warranty_service.to_warranty_response(build_warranty_record()).is_expired
        is False
    )


def test_fleet_limit_predicate_reaches_trucks_their_packs_and_devices_only() -> None:
    """The limit matches a truck, its fitted pack and T-Box; never a charger (D5)."""
    predicate_sql = str(
        warranty_repository._visible_to(frozenset({uuid4()})).compile(
            compile_kwargs={"render_postcompile": True}
        )
    )

    assert "warranties.vehicle_id IN" in predicate_sql
    assert "FROM batteries" in predicate_sql
    assert "batteries.vehicle_id IN" in predicate_sql
    assert "FROM telematics" in predicate_sql
    assert "telematics.vehicle_id IN" in predicate_sql
    assert "station_id" not in predicate_sql


@pytest.mark.asyncio
async def test_get_and_list_warranties_apply_the_fleet_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detail and list hand the limited manager's trucks to the repository."""
    received_vehicle_ids: list[object] = []

    async def get_by_id(
        db: AsyncSession, warranty_id: UUID, **scope: object
    ) -> WarrantyModel | None:
        received_vehicle_ids.append(scope["vehicle_ids"])
        return None

    async def list_all(db: AsyncSession, **filters: object) -> list[WarrantyModel]:
        received_vehicle_ids.append(filters["vehicle_ids"])
        return []

    async def count(db: AsyncSession, **filters: object) -> int:
        received_vehicle_ids.append(filters["vehicle_ids"])
        return 0

    monkeypatch.setattr(warranty_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(warranty_repository, "list_all", list_all)
    monkeypatch.setattr(warranty_repository, "count", count)
    visible_vehicle_ids = frozenset({uuid4()})
    principal = build_principal(roles=frozenset({UserRole.FLEET_MANAGER}))

    with pytest.raises(WarrantyNotFoundError):
        await warranty_service.get_warranty(
            fake_db_session(),
            uuid4(),
            principal=principal,
            visible_vehicle_ids=visible_vehicle_ids,
        )
    await warranty_service.list_warranties(
        fake_db_session(),
        principal=principal,
        visible_vehicle_ids=visible_vehicle_ids,
    )

    assert received_vehicle_ids == [visible_vehicle_ids] * 3


@pytest.mark.asyncio
async def test_the_overlap_check_runs_under_the_object_coverage_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug-fix A12: two concurrent creates cannot both pass the overlap check."""
    vehicle_id = uuid4()
    calls: list[str] = []

    async def vehicle(db: AsyncSession, requested_id: UUID) -> VehicleReference:
        return VehicleReference(
            organization_id=uuid4(),
            vehicle_id=requested_id,
            vin="1HGBH41JXMN109186",
            battery_capacity_kwh=None,
        )

    async def lock(db: AsyncSession, object_id: UUID, warranty_type: str) -> None:
        calls.append(f"lock {object_id} {warranty_type}")

    async def overlapping(db: AsyncSession, *_args: object, **_kwargs: object) -> None:
        calls.append("check")
        raise WarrantyConflictError("stop here")

    monkeypatch.setattr(vehicle_service, "resolve_vehicle_reference_by_id", vehicle)
    monkeypatch.setattr(warranty_repository, "lock_object_coverage", lock)
    monkeypatch.setattr(warranty_repository, "find_overlapping", overlapping)

    with pytest.raises(WarrantyConflictError):
        await warranty_service.create_warranty(
            fake_db_session(),
            WarrantyCreateRequest(
                vehicle_id=vehicle_id,
                starts_on=date(2026, 1, 1),
                ends_on=date(2027, 1, 1),
            ),
            principal=build_internal_principal(),
        )

    assert calls == [f"lock {vehicle_id} STANDARD", "check"]
