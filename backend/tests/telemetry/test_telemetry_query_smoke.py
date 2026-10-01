"""Smoke tests for the telemetry latest/history query API (F-A1, F-A5)."""

from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.telemetry.exceptions import (
    TelemetryInvalidRangeError,
    TelemetryNotFoundError,
)
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.vehicles.types import (
    VehicleReference,
)
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location
from tests.builders import build_telemetry_record, fake_db_session


def test_telemetry_latest_response_decodes_location_to_lat_lon() -> None:
    """to_vehicle_telemetry_latest_response() exposes lat/lon from the stored geography.

    Regression guard for the vehicle_telemetry storage unification
    (future.md item 9): the response contract (plain latitude/longitude)
    stays the same even though the ORM model now stores a single
    ``location`` point instead.
    """
    now = datetime.now(timezone.utc)
    record = VehicleTelemetryModel(
        message_id=1,
        message_uuid=uuid4(),
        telematic_id=uuid4(),
        telematic_serial="TBOX-TEST-001",
        vehicle_id=uuid4(),
        recorded_at=now,
        received_at=now,
        location=coordinates_to_location(10.762622, 106.660172),
        speed=None,
        heading=None,
        soc=80.0,
        battery_voltage=None,
        battery_current=None,
        battery_temperature=None,
        motor_temperature=None,
        odometer=None,
        signal_strength=None,
        error_codes=None,
        raw_payload={},
        schema_version=3,
    )

    response = telemetry_service.to_vehicle_telemetry_latest_response(record)

    assert response.latitude == pytest.approx(10.762622)
    assert response.longitude == pytest.approx(106.660172)
    assert response.vehicle_id == record.vehicle_id
    assert response.soc == 80.0
    assert response.schema_version == 3


def test_telemetry_history_point_decodes_location_to_lat_lon() -> None:
    """to_vehicle_telemetry_history_point() exposes lat/lon and drops per-row vehicle fields."""
    now = datetime.now(timezone.utc)
    record = build_telemetry_record(vehicle_id=uuid4(), recorded_at=now)

    point = telemetry_service.to_vehicle_telemetry_history_point(record)

    assert point.latitude == pytest.approx(10.762622)
    assert point.longitude == pytest.approx(106.660172)
    assert point.recorded_at == now
    assert point.soc == 80.0
    assert not hasattr(point, "vehicle_id")


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_returns_ordered_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The history service returns every point the repository provides, count included."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)
    records = [
        build_telemetry_record(vehicle_id=vehicle_id, recorded_at=start, message_id=1),
        build_telemetry_record(
            vehicle_id=vehicle_id, recorded_at=start + timedelta(hours=1), message_id=2
        ),
    ]

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def history(
        db: AsyncSession, **kwargs: object
    ) -> list[VehicleTelemetryModel]:
        assert kwargs["vehicle_id"] == vehicle_id
        assert kwargs["start_time"] == start
        assert kwargs["end_time"] == end
        assert kwargs["limit"] == settings.TELEMETRY_HISTORY_DEFAULT_LIMIT
        return records

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(telemetry_repository, "get_vehicle_telemetry_history", history)

    response = await telemetry_service.get_vehicle_telemetry_history_response(
        fake_db_session(), vehicle_id=vehicle_id, start_time=start, end_time=end
    )

    assert response.vehicle_id == vehicle_id
    assert response.count == 2
    assert [point.recorded_at for point in response.points] == [
        start,
        start + timedelta(hours=1),
    ]


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_raises_not_found_for_unknown_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing/soft-deleted vehicle raises TelemetryNotFoundError, not a repository call."""

    async def no_vehicle(db: AsyncSession, value: UUID) -> None:
        return None

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", no_vehicle
    )

    with pytest.raises(TelemetryNotFoundError):
        await telemetry_service.get_vehicle_telemetry_history_response(
            fake_db_session(),
            vehicle_id=uuid4(),
            start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_rejects_naive_start_time() -> (
    None
):
    """A start_time with no timezone is rejected before any lookup runs."""
    with pytest.raises(TelemetryInvalidRangeError, match="start_time"):
        await telemetry_service.get_vehicle_telemetry_history_response(
            fake_db_session(),
            vehicle_id=uuid4(),
            start_time=datetime(2026, 9, 1),
            end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_rejects_naive_end_time() -> None:
    """An end_time with no timezone is rejected before any lookup runs."""
    with pytest.raises(TelemetryInvalidRangeError, match="end_time"):
        await telemetry_service.get_vehicle_telemetry_history_response(
            fake_db_session(),
            vehicle_id=uuid4(),
            start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 2),
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_rejects_non_positive_range() -> (
    None
):
    """end_time at or before start_time is rejected."""
    same_instant = datetime(2026, 9, 1, tzinfo=timezone.utc)

    with pytest.raises(TelemetryInvalidRangeError):
        await telemetry_service.get_vehicle_telemetry_history_response(
            fake_db_session(),
            vehicle_id=uuid4(),
            start_time=same_instant,
            end_time=same_instant,
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_rejects_range_exceeding_max_days() -> (
    None
):
    """A span longer than TELEMETRY_HISTORY_MAX_RANGE_DAYS is rejected."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    too_far = start + timedelta(days=settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS + 1)

    with pytest.raises(TelemetryInvalidRangeError):
        await telemetry_service.get_vehicle_telemetry_history_response(
            fake_db_session(), vehicle_id=uuid4(), start_time=start, end_time=too_far
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_clamps_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A limit above the configured max is clamped, not rejected."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    captured_limit: dict[str, int] = {}

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def history(
        db: AsyncSession, **kwargs: object
    ) -> list[VehicleTelemetryModel]:
        captured_limit["limit"] = cast(int, kwargs["limit"])
        return []

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(telemetry_repository, "get_vehicle_telemetry_history", history)

    await telemetry_service.get_vehicle_telemetry_history_response(
        fake_db_session(),
        vehicle_id=vehicle_id,
        start_time=start,
        end_time=end,
        limit=settings.TELEMETRY_HISTORY_MAX_LIMIT + 1000,
    )

    assert captured_limit["limit"] == settings.TELEMETRY_HISTORY_MAX_LIMIT
