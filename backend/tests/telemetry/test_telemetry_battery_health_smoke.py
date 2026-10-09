"""Smoke tests for the daily battery-health trend (F-A3)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telemetry.reports as telemetry_reports
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.telemetry.exceptions import (
    TelemetryInvalidRangeError,
    TelemetryNotFoundError,
)
from app.domains.telemetry.types import VehicleBatteryHealthDay
from app.domains.vehicles.types import VehicleReference
from app.libs.common.config import settings
from tests.builders import fake_db_session

_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
_END = datetime(2026, 9, 8, tzinfo=timezone.utc)


def test_estimated_capacity_scales_nominal_capacity_by_soh() -> None:
    """90% SOH of a 400 kWh pack is 360 kWh usable."""
    assert telemetry_reports.calculate_estimated_capacity_kwh(
        soh_percent=90.0, battery_capacity_kwh=400.0
    ) == pytest.approx(360.0)


@pytest.mark.parametrize(
    ("soh_percent", "battery_capacity_kwh"), [(None, 400.0), (90.0, None)]
)
def test_estimated_capacity_is_none_when_an_input_is_unknown(
    soh_percent: float | None, battery_capacity_kwh: float | None
) -> None:
    """No estimate without both SOH and a recorded capacity (no default capacity)."""
    assert (
        telemetry_reports.calculate_estimated_capacity_kwh(
            soh_percent=soh_percent, battery_capacity_kwh=battery_capacity_kwh
        )
        is None
    )


@pytest.mark.asyncio
async def test_battery_health_builds_one_point_per_reported_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each repository day becomes a point, in the report time zone, with capacity."""
    vehicle_id = uuid4()
    captured_kwargs: dict[str, object] = {}
    health_days = [
        VehicleBatteryHealthDay(
            day_start=datetime(2026, 9, 1, 17, tzinfo=timezone.utc),
            soh_percent=95.0,
            cycle_count=120,
        ),
        VehicleBatteryHealthDay(
            day_start=datetime(2026, 9, 3, 17, tzinfo=timezone.utc),
            soh_percent=None,
            cycle_count=121,
        ),
    ]

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return VehicleReference(
            organization_id=uuid4(),
            vehicle_id=value,
            vin="1HGBH41JXMN109186",
            battery_capacity_kwh=400.0,
        )

    async def list_days(
        db: AsyncSession, **kwargs: object
    ) -> list[VehicleBatteryHealthDay]:
        captured_kwargs.update(kwargs)
        return health_days

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(
        telemetry_repository, "list_vehicle_battery_health_days", list_days
    )

    response = await telemetry_service.get_vehicle_battery_health_response(
        fake_db_session(), vehicle_id=vehicle_id, start_time=_START, end_time=_END
    )

    assert captured_kwargs["time_zone"] == settings.APP_REPORT_TIMEZONE
    assert response.vehicle_id == vehicle_id
    assert response.battery_capacity_kwh == 400.0
    assert response.count == 2
    first_point, second_point = response.points
    assert first_point.day_start == health_days[0].day_start
    assert first_point.soh_percent == 95.0
    assert first_point.cycle_count == 120
    assert first_point.estimated_capacity_kwh == pytest.approx(380.0)
    assert second_point.soh_percent is None
    assert second_point.estimated_capacity_kwh is None


@pytest.mark.asyncio
async def test_battery_health_raises_not_found_for_unknown_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown or soft-deleted vehicle is a 404."""

    async def no_vehicle(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", no_vehicle
    )

    with pytest.raises(TelemetryNotFoundError):
        await telemetry_service.get_vehicle_battery_health_response(
            fake_db_session(), vehicle_id=uuid4(), start_time=_START, end_time=_END
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_time", "end_time"),
    [
        (datetime(2026, 9, 1), _END),  # naive start
        (_START, _START),  # empty range
        (
            _START,
            _START
            + timedelta(days=settings.TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS + 1),
        ),
    ],
)
async def test_battery_health_rejects_invalid_window(
    start_time: datetime, end_time: datetime
) -> None:
    """The trend validates its window like history, with its own maximum."""
    with pytest.raises(TelemetryInvalidRangeError):
        await telemetry_service.get_vehicle_battery_health_response(
            fake_db_session(),
            vehicle_id=uuid4(),
            start_time=start_time,
            end_time=end_time,
        )
