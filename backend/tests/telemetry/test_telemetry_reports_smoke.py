"""Smoke tests for the operating and energy usage reports (F-A6, F-C6)."""

from datetime import datetime, timedelta, timezone
from typing import Any
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
from app.domains.telemetry.types import (
    DEFAULT_BATTERY_CAPACITY_KWH,
    VehicleTelemetryWindowSummary,
)
from app.domains.vehicles.types import (
    VehicleReference,
)
from app.libs.common.config import settings
from tests.builders import fake_db_session


def test_calculate_energy_kwh_converts_soc_percent_with_capacity() -> None:
    """A summed SOC delta converts to kWh proportionally to pack capacity."""
    assert telemetry_service.calculate_energy_kwh(20.0, 75.0) == pytest.approx(15.0)


def test_calculate_energy_per_100km_returns_none_without_distance() -> None:
    """Energy intensity is undefined, not zero or infinite, at zero distance."""
    assert telemetry_service.calculate_energy_per_100km_kwh(15.0, 0.0) is None


def test_calculate_energy_per_100km_scales_to_hundred_kilometres() -> None:
    """15 kWh over 50 km is 30 kWh/100km."""
    result = telemetry_service.calculate_energy_per_100km_kwh(15.0, 50.0)
    assert result == pytest.approx(30.0)


def test_calculate_cost_per_km_returns_none_without_distance() -> None:
    """Cost per km is undefined, not zero, at zero distance."""
    assert telemetry_service.calculate_cost_per_km_vnd(45000.0, 0.0) is None


def test_calculate_distance_per_day_uses_requested_window_not_observed_span() -> None:
    """km/day divides by the full requested window, not the sample span."""
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=7)

    result = telemetry_service.calculate_distance_per_day_km(140.0, start, end)

    assert result == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_operating_report_rejects_naive_start_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-A6 rejects a start_time with no timezone before touching the DB."""
    with pytest.raises(TelemetryInvalidRangeError):
        await telemetry_service.get_vehicle_operating_report(
            fake_db_session(),
            vehicle_id=uuid4(),
            start_time=datetime(2026, 9, 1),
            end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_operating_report_rejects_end_time_at_or_before_start_time() -> None:
    """F-A6 rejects a non-positive time range."""
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    with pytest.raises(TelemetryInvalidRangeError):
        await telemetry_service.get_vehicle_operating_report(
            fake_db_session(), vehicle_id=uuid4(), start_time=start, end_time=start
        )


@pytest.mark.asyncio
async def test_operating_report_rejects_range_beyond_configured_maximum() -> None:
    """F-A6 rejects a window wider than TELEMETRY_REPORT_MAX_RANGE_DAYS."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=settings.TELEMETRY_REPORT_MAX_RANGE_DAYS + 1)
    with pytest.raises(TelemetryInvalidRangeError):
        await telemetry_service.get_vehicle_operating_report(
            fake_db_session(), vehicle_id=uuid4(), start_time=start, end_time=end
        )


@pytest.mark.asyncio
async def test_operating_report_raises_not_found_for_unknown_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-A6 404s on a vehicle that doesn't exist or was soft-deleted."""

    async def no_vehicle(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", no_vehicle
    )

    with pytest.raises(TelemetryNotFoundError):
        await telemetry_service.get_vehicle_operating_report(
            fake_db_session(),
            vehicle_id=uuid4(),
            start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_operating_report_falls_back_to_default_battery_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vehicle with no recorded capacity gets the documented default, flagged."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    summary = VehicleTelemetryWindowSummary(
        soc_discharge_percent=20.0,
        soc_charge_percent=0.0,
        distance_km=50.0,
        sample_count=10,
        odometer_sample_count=10,
        first_recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        last_recorded_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def window_summary(db: AsyncSession, **kwargs: object) -> Any:
        return summary

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=vehicle_id,
        start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert report.is_default_battery_capacity is True
    assert report.battery_capacity_kwh == DEFAULT_BATTERY_CAPACITY_KWH
    assert report.energy_consumed_kwh == pytest.approx(
        20.0 / 100.0 * DEFAULT_BATTERY_CAPACITY_KWH
    )


@pytest.mark.asyncio
async def test_operating_report_uses_recorded_battery_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vehicle with a recorded capacity uses it, not flagged as default."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=60.0
    )
    summary = VehicleTelemetryWindowSummary(
        soc_discharge_percent=10.0,
        soc_charge_percent=0.0,
        distance_km=40.0,
        sample_count=5,
        odometer_sample_count=5,
        first_recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        last_recorded_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def window_summary(db: AsyncSession, **kwargs: object) -> Any:
        return summary

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=vehicle_id,
        start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert report.is_default_battery_capacity is False
    assert report.battery_capacity_kwh == 60.0


@pytest.mark.asyncio
async def test_operating_report_returns_none_rates_for_empty_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty window yields zero sums but None for every derived rate."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=75.0
    )
    summary = VehicleTelemetryWindowSummary(
        soc_discharge_percent=0.0,
        soc_charge_percent=0.0,
        distance_km=0.0,
        sample_count=0,
        odometer_sample_count=0,
        first_recorded_at=None,
        last_recorded_at=None,
    )

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def window_summary(db: AsyncSession, **kwargs: object) -> Any:
        return summary

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=vehicle_id,
        start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert report.distance_km == 0.0
    assert report.energy_consumed_kwh == 0.0
    assert report.energy_per_100km_kwh is None
    assert report.distance_per_day_km is None
    assert report.cost_per_km_vnd is None


@pytest.mark.asyncio
async def test_operating_report_returns_none_rates_for_single_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single telemetry row (no adjacent pair) yields None derived rates too."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=75.0
    )
    summary = VehicleTelemetryWindowSummary(
        soc_discharge_percent=0.0,
        soc_charge_percent=0.0,
        distance_km=0.0,
        sample_count=1,
        odometer_sample_count=1,
        first_recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        last_recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def window_summary(db: AsyncSession, **kwargs: object) -> Any:
        return summary

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=vehicle_id,
        start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert report.distance_per_day_km is None
    assert report.energy_per_100km_kwh is None
    assert report.cost_per_km_vnd is None


@pytest.mark.asyncio
async def test_operating_report_reports_energy_without_distance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parked vehicle can drain SOC (HVAC) with zero distance - energy stays positive."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=75.0
    )
    summary = VehicleTelemetryWindowSummary(
        soc_discharge_percent=5.0,
        soc_charge_percent=0.0,
        distance_km=0.0,
        sample_count=10,
        odometer_sample_count=10,
        first_recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        last_recorded_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def window_summary(db: AsyncSession, **kwargs: object) -> Any:
        return summary

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=vehicle_id,
        start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert report.energy_consumed_kwh > 0
    assert report.energy_per_100km_kwh is None
    assert report.cost_per_km_vnd is None


@pytest.mark.asyncio
async def test_operating_report_echoes_normalized_utc_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-UTC offset input is echoed back normalized to UTC."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=75.0
    )
    summary = VehicleTelemetryWindowSummary(
        soc_discharge_percent=0.0,
        soc_charge_percent=0.0,
        distance_km=0.0,
        sample_count=0,
        odometer_sample_count=0,
        first_recorded_at=None,
        last_recorded_at=None,
    )

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def window_summary(db: AsyncSession, **kwargs: object) -> Any:
        return summary

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    tz_plus_7 = timezone(timedelta(hours=7))
    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=vehicle_id,
        start_time=datetime(2026, 9, 1, 7, 0, tzinfo=tz_plus_7),
        end_time=datetime(2026, 9, 2, 7, 0, tzinfo=tz_plus_7),
    )

    assert report.start_time == datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    assert report.end_time == datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_energy_usage_report_reports_soc_rises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-C6 converts summed SOC rises (not drops) into charged energy."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=75.0
    )
    summary = VehicleTelemetryWindowSummary(
        soc_discharge_percent=5.0,
        soc_charge_percent=40.0,
        distance_km=0.0,
        sample_count=20,
        odometer_sample_count=0,
        first_recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        last_recorded_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def window_summary(db: AsyncSession, **kwargs: object) -> Any:
        return summary

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    report = await telemetry_service.get_vehicle_energy_usage_report(
        fake_db_session(),
        vehicle_id=vehicle_id,
        start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert report.energy_charged_kwh == pytest.approx(40.0 / 100.0 * 75.0)
    assert report.is_default_battery_capacity is False
