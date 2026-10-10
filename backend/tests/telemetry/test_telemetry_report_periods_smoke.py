"""Smoke tests for the F-A6 period breakdown, CSV export and operating summary."""

import csv
import io
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.telemetry.time_windows as telemetry_time_windows
import app.domains.vehicles.service as vehicles_public_service
from app.domains.telemetry.exceptions import TelemetryNotFoundError
from app.domains.telemetry.types import (
    ReportGranularity,
    VehicleTelemetryWindowSummary,
)
from app.domains.vehicles.types import VehicleReference
from app.libs.common.config import settings
from tests.builders import fake_db_session
from tests.principals import build_internal_principal

_HCM = "Asia/Ho_Chi_Minh"  # UTC+7, no DST


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Build a UTC datetime on the hour.

    Args:
        year: Year.
        month: Month.
        day: Day of month.
        hour: Hour of day (UTC).

    Returns:
        A timezone-aware UTC datetime.
    """
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _summary(
    *, soc_drop: float, distance_km: float, sample_count: int
) -> VehicleTelemetryWindowSummary:
    """Build a folded summary with the fields the operating report reads.

    Args:
        soc_drop: Summed SOC drop (%).
        distance_km: Summed distance.
        sample_count: Rows in the span (also used as odometer rows).

    Returns:
        The summary.
    """
    return VehicleTelemetryWindowSummary(
        soc_discharge_percent=soc_drop,
        soc_charge_percent=0.0,
        distance_km=distance_km,
        sample_count=sample_count,
        odometer_sample_count=sample_count,
        first_recorded_at=_utc(2026, 9, 1, 1) if sample_count else None,
        last_recorded_at=_utc(2026, 9, 1, 2) if sample_count else None,
    )


def test_day_periods_follow_report_timezone_and_clip_to_window() -> None:
    """Days start at local midnight (17:00 UTC in UTC+7); ends are clipped."""
    periods = telemetry_time_windows.build_report_periods(
        _utc(2026, 9, 1),
        _utc(2026, 9, 3),
        granularity=ReportGranularity.DAY,
        time_zone=_HCM,
    )

    assert [
        (period.bucket_start, period.period_start, period.period_end)
        for period in periods
    ] == [
        (_utc(2026, 8, 31, 17), _utc(2026, 9, 1), _utc(2026, 9, 1, 17)),
        (_utc(2026, 9, 1, 17), _utc(2026, 9, 1, 17), _utc(2026, 9, 2, 17)),
        (_utc(2026, 9, 2, 17), _utc(2026, 9, 2, 17), _utc(2026, 9, 3)),
    ]


def test_week_periods_start_on_monday() -> None:
    """A week is an ISO week: 2026-10-01 (Thursday) falls in the week of 09-28."""
    periods = telemetry_time_windows.build_report_periods(
        _utc(2026, 10, 1),
        _utc(2026, 10, 14),
        granularity=ReportGranularity.WEEK,
        time_zone=_HCM,
    )

    assert [period.bucket_start for period in periods] == [
        _utc(2026, 9, 27, 17),
        _utc(2026, 10, 4, 17),
        _utc(2026, 10, 11, 17),
    ]
    assert periods[0].period_start == _utc(2026, 10, 1)
    assert periods[-1].period_end == _utc(2026, 10, 14)


def test_month_periods_roll_over_the_year() -> None:
    """December is followed by January of the next year."""
    periods = telemetry_time_windows.build_report_periods(
        _utc(2025, 12, 20),
        _utc(2026, 2, 5),
        granularity=ReportGranularity.MONTH,
        time_zone=_HCM,
    )

    assert [period.bucket_start for period in periods] == [
        _utc(2025, 11, 30, 17),
        _utc(2025, 12, 31, 17),
        _utc(2026, 1, 31, 17),
    ]


def test_window_ending_on_a_boundary_has_no_zero_length_tail() -> None:
    """Local midnight to local midnight is exactly one day, not two."""
    periods = telemetry_time_windows.build_report_periods(
        _utc(2026, 9, 1, 17),
        _utc(2026, 9, 2, 17),
        granularity=ReportGranularity.DAY,
        time_zone=_HCM,
    )

    assert len(periods) == 1
    assert periods[0].period_end == _utc(2026, 9, 2, 17)


def test_day_periods_keep_whole_local_days_across_dst() -> None:
    """In a DST zone the spring-forward day is 23 hours long, not cut at 24 h."""
    periods = telemetry_time_windows.build_report_periods(
        _utc(2026, 3, 28, 12),
        _utc(2026, 3, 30, 12),
        granularity=ReportGranularity.DAY,
        time_zone="Europe/Berlin",
    )

    spring_forward_day = periods[1]
    assert spring_forward_day.period_start == _utc(2026, 3, 28, 23)
    assert spring_forward_day.period_end == _utc(2026, 3, 29, 22)


@pytest.fixture
def report_vehicle(monkeypatch: pytest.MonkeyPatch) -> UUID:
    """Patch the vehicle lookup to a known vehicle with a 100 kWh pack.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The vehicle's id.
    """
    vehicle_id = uuid4()

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return VehicleReference(
            organization_id=uuid4(),
            vehicle_id=value,
            vin="1HGBH41JXMN109186",
            battery_capacity_kwh=100.0,
        )

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(settings, "APP_REPORT_TIMEZONE", _HCM)
    return vehicle_id


@pytest.mark.asyncio
async def test_operating_report_without_granularity_keeps_single_aggregate(
    monkeypatch: pytest.MonkeyPatch, report_vehicle: UUID
) -> None:
    """No granularity: the old contract, periods stays None and isn't queried."""

    async def window_summary(db: AsyncSession, **kwargs: object) -> object:
        return _summary(soc_drop=10.0, distance_km=50.0, sample_count=4)

    async def no_period_query(db: AsyncSession, **kwargs: object) -> object:
        raise AssertionError("period query must not run without a granularity")

    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )
    monkeypatch.setattr(
        telemetry_repository, "list_vehicle_period_summaries", no_period_query
    )

    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=report_vehicle,
        start_time=_utc(2026, 9, 1),
        end_time=_utc(2026, 9, 3),
        principal=build_internal_principal(),
    )

    assert report.granularity is None
    assert report.periods is None
    assert report.energy_consumed_kwh == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_operating_report_lists_every_day_with_zero_for_empty_ones(
    monkeypatch: pytest.MonkeyPatch, report_vehicle: UUID
) -> None:
    """Each period gets the same metrics; a period without readings is zero."""
    captured_kwargs: dict[str, object] = {}

    async def window_summary(db: AsyncSession, **kwargs: object) -> object:
        return _summary(soc_drop=10.0, distance_km=50.0, sample_count=4)

    async def period_summaries(
        db: AsyncSession, **kwargs: object
    ) -> dict[datetime, VehicleTelemetryWindowSummary]:
        captured_kwargs.update(kwargs)
        return {
            _utc(2026, 9, 1, 17): _summary(
                soc_drop=10.0, distance_km=50.0, sample_count=4
            )
        }

    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )
    monkeypatch.setattr(
        telemetry_repository, "list_vehicle_period_summaries", period_summaries
    )
    monkeypatch.setattr(settings, "TELEMETRY_ENERGY_COST_PER_KWH_VND", 4000.0)

    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=report_vehicle,
        start_time=_utc(2026, 9, 1),
        end_time=_utc(2026, 9, 3),
        granularity=ReportGranularity.DAY,
        principal=build_internal_principal(),
    )

    assert captured_kwargs["granularity"] is ReportGranularity.DAY
    assert captured_kwargs["time_zone"] == _HCM
    assert report.granularity is ReportGranularity.DAY
    assert report.periods is not None
    assert len(report.periods) == 3
    empty_day, busy_day, _ = report.periods
    assert empty_day.sample_count == 0
    assert empty_day.energy_consumed_kwh == 0.0
    assert empty_day.distance_per_day_km is None
    assert busy_day.period_start == _utc(2026, 9, 1, 17)
    assert busy_day.energy_consumed_kwh == pytest.approx(10.0)
    assert busy_day.energy_cost_vnd == pytest.approx(40000.0)
    assert busy_day.distance_per_day_km == pytest.approx(50.0)
    assert report.cost_per_kwh_vnd == 4000.0


@pytest.mark.asyncio
async def test_resolve_vehicle_operating_summary_returns_additive_figures(
    monkeypatch: pytest.MonkeyPatch, report_vehicle: UUID
) -> None:
    """The cross-domain summary carries sums and counts, not rates."""

    async def window_summary(db: AsyncSession, **kwargs: object) -> object:
        return _summary(soc_drop=20.0, distance_km=80.0, sample_count=6)

    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    summary = await telemetry_service.resolve_vehicle_operating_summary(
        fake_db_session(),
        report_vehicle,
        start_time=_utc(2026, 9, 1),
        end_time=_utc(2026, 9, 3),
    )

    assert summary.vehicle_id == report_vehicle
    assert summary.distance_km == 80.0
    assert summary.energy_consumed_kwh == pytest.approx(20.0)
    assert summary.sample_count == 6
    assert summary.battery_capacity_kwh == 100.0
    assert summary.is_default_battery_capacity is False


@pytest.mark.asyncio
async def test_resolve_vehicle_operating_summary_raises_for_unknown_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown vehicle is TelemetryNotFoundError, like the report."""

    async def no_vehicle(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", no_vehicle
    )

    with pytest.raises(TelemetryNotFoundError):
        await telemetry_service.resolve_vehicle_operating_summary(
            fake_db_session(),
            uuid4(),
            start_time=_utc(2026, 9, 1),
            end_time=_utc(2026, 9, 2),
        )


@pytest.mark.asyncio
async def test_operating_report_csv_has_header_and_one_row_per_period(
    monkeypatch: pytest.MonkeyPatch, report_vehicle: UUID
) -> None:
    """CSV: a header, one row per period, UTC ISO timestamps, empty for None."""

    async def window_summary(db: AsyncSession, **kwargs: object) -> object:
        return _summary(soc_drop=10.0, distance_km=50.0, sample_count=4)

    async def period_summaries(
        db: AsyncSession, **kwargs: object
    ) -> dict[datetime, VehicleTelemetryWindowSummary]:
        return {}

    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )
    monkeypatch.setattr(
        telemetry_repository, "list_vehicle_period_summaries", period_summaries
    )

    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=report_vehicle,
        start_time=_utc(2026, 9, 1),
        end_time=_utc(2026, 9, 3),
        granularity=ReportGranularity.DAY,
        principal=build_internal_principal(),
    )
    csv_rows = list(
        csv.DictReader(
            io.StringIO(
                telemetry_service.serialize_vehicle_operating_report_csv(report)
            )
        )
    )

    assert len(csv_rows) == 3
    assert csv_rows[0]["vehicle_id"] == str(report_vehicle)
    assert csv_rows[0]["period_start"] == "2026-09-01T00:00:00+00:00"
    assert csv_rows[0]["period_end"] == "2026-09-01T17:00:00+00:00"
    assert csv_rows[0]["energy_per_100km_kwh"] == ""
    assert csv_rows[0]["sample_count"] == "0"


@pytest.mark.asyncio
async def test_operating_report_csv_without_granularity_is_one_window_row(
    monkeypatch: pytest.MonkeyPatch, report_vehicle: UUID
) -> None:
    """Without a breakdown the CSV holds one row spanning the whole window."""

    async def window_summary(db: AsyncSession, **kwargs: object) -> object:
        return _summary(soc_drop=10.0, distance_km=50.0, sample_count=4)

    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    report = await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=report_vehicle,
        start_time=_utc(2026, 9, 1),
        end_time=_utc(2026, 9, 3),
        principal=build_internal_principal(),
    )
    csv_rows = list(
        csv.DictReader(
            io.StringIO(
                telemetry_service.serialize_vehicle_operating_report_csv(report)
            )
        )
    )

    assert len(csv_rows) == 1
    assert csv_rows[0]["period_start"] == "2026-09-01T00:00:00+00:00"
    assert csv_rows[0]["period_end"] == "2026-09-03T00:00:00+00:00"
    assert float(csv_rows[0]["distance_km"]) == 50.0
