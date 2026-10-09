"""Smoke tests for the vehicle online flag and live-status DTO (F-A1, planner D2)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.telemetry.models import TelemetryModel
from app.domains.telemetry.types import VehicleLiveStatusReference
from app.domains.vehicles.types import VehicleReference
from app.libs.common.config import settings
from tests.builders import build_telemetry_record, fake_db_session


def _patch_latest_reading(
    monkeypatch: pytest.MonkeyPatch,
    *,
    telemetry: TelemetryModel | None,
    last_received_at: datetime | None,
) -> None:
    """Make the repository return a given newest row and newest receive time.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        telemetry: Row returned as the newest reading (by ``recorded_at``).
        last_received_at: Value returned as the newest ``received_at``.
    """

    async def latest_reading(
        db: AsyncSession, vehicle_id: UUID
    ) -> TelemetryModel | None:
        return telemetry

    async def latest_received_at(db: AsyncSession, vehicle_id: UUID) -> datetime | None:
        return last_received_at

    monkeypatch.setattr(
        telemetry_repository, "get_latest_vehicle_telemetry", latest_reading
    )
    monkeypatch.setattr(
        telemetry_repository, "find_latest_received_at", latest_received_at
    )


@pytest.mark.asyncio
async def test_resolve_vehicle_live_status_returns_none_without_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vehicle that never reported has no live status (not an error)."""
    _patch_latest_reading(monkeypatch, telemetry=None, last_received_at=None)

    live_status = await telemetry_service.resolve_vehicle_live_status(
        fake_db_session(), uuid4()
    )

    assert live_status is None


@pytest.mark.asyncio
async def test_resolve_vehicle_live_status_is_online_for_recent_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry received within the threshold makes the vehicle online."""
    vehicle_id = uuid4()
    now = datetime.now(timezone.utc)
    telemetry = build_telemetry_record(
        vehicle_id=vehicle_id, recorded_at=now - timedelta(seconds=30)
    )
    telemetry.signal_dbm = -71
    _patch_latest_reading(
        monkeypatch, telemetry=telemetry, last_received_at=telemetry.received_at
    )

    live_status = await telemetry_service.resolve_vehicle_live_status(
        fake_db_session(), vehicle_id
    )

    assert isinstance(live_status, VehicleLiveStatusReference)
    assert live_status.vehicle_id == vehicle_id
    assert live_status.latitude == pytest.approx(10.762622)
    assert live_status.longitude == pytest.approx(106.660172)
    assert live_status.recorded_at == telemetry.recorded_at
    assert live_status.received_at == telemetry.received_at
    assert live_status.is_online is True
    assert live_status.signal_strength_dbm == -71


@pytest.mark.asyncio
async def test_resolve_vehicle_live_status_is_offline_after_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry older than the threshold makes the vehicle offline."""
    vehicle_id = uuid4()
    stale_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.TELEMETRY_ONLINE_THRESHOLD_SECONDS + 60
    )
    telemetry = build_telemetry_record(vehicle_id=vehicle_id, recorded_at=stale_at)
    _patch_latest_reading(monkeypatch, telemetry=telemetry, last_received_at=stale_at)

    live_status = await telemetry_service.resolve_vehicle_live_status(
        fake_db_session(), vehicle_id
    )

    assert live_status is not None
    assert live_status.is_online is False


@pytest.mark.asyncio
async def test_online_flag_follows_newest_receive_time_not_device_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future-dated, long-ago-received row can't hide a fresh arrival."""
    vehicle_id = uuid4()
    now = datetime.now(timezone.utc)
    future_dated = build_telemetry_record(
        vehicle_id=vehicle_id, recorded_at=now + timedelta(days=1)
    )
    future_dated.received_at = now - timedelta(hours=2)
    _patch_latest_reading(monkeypatch, telemetry=future_dated, last_received_at=now)

    live_status = await telemetry_service.resolve_vehicle_live_status(
        fake_db_session(), vehicle_id
    )

    assert live_status is not None
    assert live_status.is_online is True


@pytest.mark.asyncio
async def test_online_threshold_is_read_from_settings_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing TELEMETRY_ONLINE_THRESHOLD_SECONDS changes the verdict."""
    vehicle_id = uuid4()
    received_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    telemetry = build_telemetry_record(vehicle_id=vehicle_id, recorded_at=received_at)
    _patch_latest_reading(
        monkeypatch, telemetry=telemetry, last_received_at=received_at
    )
    monkeypatch.setattr(settings, "TELEMETRY_ONLINE_THRESHOLD_SECONDS", 60)

    live_status = await telemetry_service.resolve_vehicle_live_status(
        fake_db_session(), vehicle_id
    )

    assert live_status is not None
    assert live_status.is_online is False


@pytest.mark.asyncio
async def test_latest_response_exposes_received_at_and_online_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /latest adds received_at and the read-time is_online flag (F-A1)."""
    vehicle_id = uuid4()
    now = datetime.now(timezone.utc)
    telemetry = build_telemetry_record(vehicle_id=vehicle_id, recorded_at=now)

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return VehicleReference(
            organization_id=uuid4(),
            vehicle_id=value,
            vin="1HGBH41JXMN109186",
            battery_capacity_kwh=None,
        )

    async def resolve_serial(db: AsyncSession, telematic_id: UUID) -> str:
        return "TBOX-TEST-001"

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(telematics_service, "resolve_serial_by_id", resolve_serial)
    _patch_latest_reading(monkeypatch, telemetry=telemetry, last_received_at=now)

    response = await telemetry_service.get_latest_vehicle_telemetry_response(
        fake_db_session(), vehicle_id
    )

    assert response.received_at == telemetry.received_at
    assert response.is_online is True
