"""Smoke tests for the fleet-wide telemetry views (F-E1 live positions, F-A6 rollup).

The fleet and vehicles domains are monkeypatched at their public services;
the telemetry repository is monkeypatched for the per-vehicle fold.
"""

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.service as fleet_service
import app.domains.telemetry.reports as telemetry_reports
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.router as telemetry_router
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.api.main import app
from app.domains.fleet.exceptions import FleetNotFoundError
from app.domains.telemetry.exceptions import TelemetryInvalidRangeError
from app.domains.telemetry.schemas import FleetOperatingReportResponse
from app.domains.telemetry.types import (
    ReportFormat,
    VehicleLiveStatusReference,
    VehicleTelemetryWindowSummary,
)
from app.domains.vehicles.types import VehicleReference, VehicleStatus, VehicleSummary
from app.libs.common.config import settings
from tests.builders import fake_db_session
from tests.principals import build_internal_principal

_WINDOW_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
_WINDOW_END = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _patch_member_vehicle_ids(
    monkeypatch: pytest.MonkeyPatch, member_vehicle_ids: list[UUID]
) -> None:
    """Make the fleet domain return a fixed member list for any fleet.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        member_vehicle_ids: Member vehicle IDs to return, oldest first.
    """

    async def list_member_ids(
        db: AsyncSession, fleet_id: UUID, **_scope: object
    ) -> list[UUID]:
        return member_vehicle_ids

    monkeypatch.setattr(
        fleet_service, "list_active_member_vehicle_ids", list_member_ids
    )


def _patch_unknown_fleet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the fleet domain reject every fleet as unknown.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    async def unknown_fleet(
        db: AsyncSession, fleet_id: UUID, **_scope: object
    ) -> list[UUID]:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    monkeypatch.setattr(fleet_service, "list_active_member_vehicle_ids", unknown_fleet)


@pytest.mark.asyncio
async def test_fleet_live_statuses_page_the_member_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Members are paged in order; vehicle and position fields are filled per member."""
    reporting_id, silent_id, deleted_id = uuid4(), uuid4(), uuid4()
    _patch_member_vehicle_ids(monkeypatch, [reporting_id, silent_id, deleted_id])
    recorded_at = datetime.now(timezone.utc)

    async def resolve_summary(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleSummary | None:
        if vehicle_id == deleted_id:
            return None
        return VehicleSummary(
            vehicle_id=vehicle_id,
            vin=f"VIN{str(vehicle_id)[:14]}",
            license_plate="51C-123.45",
            status=VehicleStatus.ACTIVE,
        )

    async def resolve_live_status(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleLiveStatusReference | None:
        if vehicle_id != reporting_id:
            return None
        return VehicleLiveStatusReference(
            vehicle_id=vehicle_id,
            latitude=10.8,
            longitude=106.7,
            recorded_at=recorded_at,
            received_at=recorded_at,
            is_online=True,
            signal_strength_dbm=-70,
        )

    monkeypatch.setattr(
        vehicle_service, "resolve_vehicle_summary_by_id", resolve_summary
    )
    monkeypatch.setattr(
        telemetry_service, "resolve_vehicle_live_status", resolve_live_status
    )

    first_page = await telemetry_service.list_fleet_vehicle_live_statuses(
        fake_db_session(),
        uuid4(),
        page=1,
        page_size=2,
        principal=build_internal_principal(),
    )
    second_page = await telemetry_service.list_fleet_vehicle_live_statuses(
        fake_db_session(),
        uuid4(),
        page=2,
        page_size=2,
        principal=build_internal_principal(),
    )

    assert first_page.total == 3
    assert [item.vehicle_id for item in first_page.items] == [reporting_id, silent_id]
    reporting, silent = first_page.items
    assert reporting.vehicle_status is VehicleStatus.ACTIVE
    assert reporting.license_plate == "51C-123.45"
    assert (reporting.latitude, reporting.longitude) == (10.8, 106.7)
    assert reporting.recorded_at == recorded_at
    assert reporting.is_online is True
    assert reporting.signal_strength_dbm == -70
    assert silent.vin is not None
    assert silent.latitude is None
    assert silent.received_at is None
    assert silent.is_online is False

    assert second_page.page == 2
    assert [item.vehicle_id for item in second_page.items] == [deleted_id]
    deleted = second_page.items[0]
    assert deleted.vin is None
    assert deleted.license_plate is None
    assert deleted.vehicle_status is None


@pytest.mark.asyncio
async def test_fleet_live_statuses_reject_unknown_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown fleet surfaces the fleet domain's FleetNotFoundError (404)."""
    _patch_unknown_fleet(monkeypatch)

    with pytest.raises(FleetNotFoundError):
        await telemetry_service.list_fleet_vehicle_live_statuses(
            fake_db_session(), uuid4(), principal=build_internal_principal()
        )


def _window_summary(
    *, soc_discharge_percent: float, distance_km: float, sample_count: int
) -> VehicleTelemetryWindowSummary:
    """Build a folded window with the given sums.

    Args:
        soc_discharge_percent: Summed SOC drops (%).
        distance_km: Summed odometer deltas (km).
        sample_count: Rows in the window.

    Returns:
        The folded window.
    """
    return VehicleTelemetryWindowSummary(
        soc_discharge_percent=soc_discharge_percent,
        soc_charge_percent=0.0,
        distance_km=distance_km,
        sample_count=sample_count,
        odometer_sample_count=sample_count,
        first_recorded_at=_WINDOW_START if sample_count else None,
        last_recorded_at=_WINDOW_END if sample_count else None,
    )


def _patch_rollup_inputs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    vehicle_references: dict[UUID, VehicleReference | None],
    window_summaries: dict[UUID, VehicleTelemetryWindowSummary],
) -> None:
    """Make the member list, vehicle lookups and per-vehicle folds deterministic.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        vehicle_references: Member vehicles in order; ``None`` for a
            member that no longer resolves (soft-deleted).
        window_summaries: Fold returned per vehicle.
    """
    _patch_member_vehicle_ids(monkeypatch, list(vehicle_references))

    async def resolve_reference(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleReference | None:
        return vehicle_references[vehicle_id]

    async def window_summary(
        db: AsyncSession, **kwargs: Any
    ) -> VehicleTelemetryWindowSummary:
        return window_summaries[kwargs["vehicle_id"]]

    monkeypatch.setattr(
        vehicle_service, "resolve_vehicle_reference_by_id", resolve_reference
    )
    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )
    monkeypatch.setattr(settings, "TELEMETRY_ENERGY_COST_PER_KWH_VND", 3000.0)


@pytest.mark.asyncio
async def test_fleet_operating_report_sums_before_deriving_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Totals add the members up and recompute rates from the sums (never averaged)."""
    busy_id, short_id, deleted_id = uuid4(), uuid4(), uuid4()
    _patch_rollup_inputs(
        monkeypatch,
        vehicle_references={
            busy_id: VehicleReference(
                organization_id=uuid4(),
                vehicle_id=busy_id,
                vin="VINBUSY0000000001",
                battery_capacity_kwh=100.0,
            ),
            short_id: VehicleReference(
                organization_id=uuid4(),
                vehicle_id=short_id,
                vin="VINSHORT000000002",
                battery_capacity_kwh=None,
            ),
            deleted_id: None,
        },
        window_summaries={
            # 20% of 100 kWh = 20 kWh over 100 km.
            busy_id: _window_summary(
                soc_discharge_percent=20.0, distance_km=100.0, sample_count=50
            ),
            # 30% of the 75 kWh default = 22.5 kWh over 300 km.
            short_id: _window_summary(
                soc_discharge_percent=30.0, distance_km=300.0, sample_count=40
            ),
        },
    )

    report = await telemetry_service.get_fleet_operating_report(
        fake_db_session(),
        uuid4(),
        start_time=_WINDOW_START,
        end_time=_WINDOW_END,
        principal=build_internal_principal(),
    )

    assert [row.vehicle_id for row in report.vehicles] == [busy_id, short_id]
    busy, short = report.vehicles
    assert busy.energy_consumed_kwh == pytest.approx(20.0)
    assert busy.energy_per_100km_kwh == pytest.approx(20.0)
    assert busy.energy_cost_vnd == pytest.approx(60_000.0)
    assert busy.cost_per_km_vnd == pytest.approx(600.0)
    assert short.energy_consumed_kwh == pytest.approx(22.5)
    assert short.is_default_battery_capacity is True
    assert short.energy_per_100km_kwh == pytest.approx(7.5)

    totals = report.totals
    assert totals.vehicle_count == 2
    assert totals.sample_count == 90
    assert totals.distance_km == pytest.approx(400.0)
    assert totals.energy_consumed_kwh == pytest.approx(42.5)
    # 42.5 kWh / 400 km, not the mean of 20.0 and 7.5 (13.75).
    assert totals.energy_per_100km_kwh == pytest.approx(10.625)
    assert totals.energy_cost_vnd == pytest.approx(127_500.0)
    assert totals.cost_per_km_vnd == pytest.approx(318.75)
    assert report.cost_per_kwh_vnd == 3000.0


@pytest.mark.asyncio
async def test_fleet_operating_report_leaves_rates_undefined_without_distance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vehicle with one sample has no rates; a fleet without distance has no rates."""
    vehicle_id = uuid4()
    _patch_rollup_inputs(
        monkeypatch,
        vehicle_references={
            vehicle_id: VehicleReference(
                organization_id=uuid4(),
                vehicle_id=vehicle_id,
                vin="VINPARKED00000001",
                battery_capacity_kwh=80.0,
            )
        },
        window_summaries={
            vehicle_id: _window_summary(
                soc_discharge_percent=0.0, distance_km=0.0, sample_count=1
            )
        },
    )

    report = await telemetry_service.get_fleet_operating_report(
        fake_db_session(),
        uuid4(),
        start_time=_WINDOW_START,
        end_time=_WINDOW_END,
        principal=build_internal_principal(),
    )

    assert report.vehicles[0].energy_per_100km_kwh is None
    assert report.vehicles[0].cost_per_km_vnd is None
    assert report.totals.energy_per_100km_kwh is None
    assert report.totals.cost_per_km_vnd is None
    assert report.totals.energy_cost_vnd == 0.0


@pytest.mark.asyncio
async def test_fleet_operating_report_validates_window_before_fleet_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A window over TELEMETRY_REPORT_MAX_RANGE_DAYS is a 400 even for a real fleet."""
    _patch_member_vehicle_ids(monkeypatch, [])
    too_long_end = _WINDOW_START + timedelta(
        days=settings.TELEMETRY_REPORT_MAX_RANGE_DAYS + 1
    )

    with pytest.raises(TelemetryInvalidRangeError):
        await telemetry_service.get_fleet_operating_report(
            fake_db_session(),
            uuid4(),
            start_time=_WINDOW_START,
            end_time=too_long_end,
            principal=build_internal_principal(),
        )


@pytest.mark.asyncio
async def test_fleet_operating_report_rejects_unknown_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown fleet surfaces FleetNotFoundError (404)."""
    _patch_unknown_fleet(monkeypatch)

    with pytest.raises(FleetNotFoundError):
        await telemetry_service.get_fleet_operating_report(
            fake_db_session(),
            uuid4(),
            start_time=_WINDOW_START,
            end_time=_WINDOW_END,
            principal=build_internal_principal(),
        )


def _fleet_report() -> FleetOperatingReportResponse:
    """Build a two-vehicle fleet report through the pure builder.

    Returns:
        A report priced at 3000 VND/kWh.
    """
    fleet_id = uuid4()
    report_vehicles = [
        telemetry_reports.FleetReportVehicle(
            vin=vin,
            operating_summary=telemetry_reports.build_operating_summary(
                telemetry_reports.VehicleReportContext(
                    vehicle_reference=VehicleReference(
                        organization_id=uuid4(),
                        vehicle_id=uuid4(),
                        vin=vin,
                        battery_capacity_kwh=100.0,
                    ),
                    start_time=_WINDOW_START,
                    end_time=_WINDOW_END,
                    window_summary=_window_summary(
                        soc_discharge_percent=10.0,
                        distance_km=50.0,
                        sample_count=10,
                    ),
                )
            ),
        )
        for vin in ("VINCSV00000000001", "VINCSV00000000002")
    ]
    return telemetry_reports.build_fleet_operating_report(
        fleet_id,
        start_time=_WINDOW_START,
        end_time=_WINDOW_END,
        report_vehicles=report_vehicles,
    )


def test_fleet_operating_report_csv_has_one_row_per_vehicle_and_a_total_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CSV export lists every vehicle, then a TOTAL row with the fleet sums."""
    monkeypatch.setattr(settings, "TELEMETRY_ENERGY_COST_PER_KWH_VND", 3000.0)
    report = _fleet_report()

    csv_text = telemetry_service.serialize_fleet_operating_report_csv(report)
    rows = list(csv.DictReader(io.StringIO(csv_text)))

    assert list(rows[0]) == list(telemetry_reports.FLEET_OPERATING_REPORT_CSV_COLUMNS)
    assert [row["vin"] for row in rows] == [
        "VINCSV00000000001",
        "VINCSV00000000002",
        "",
    ]
    total_row = rows[-1]
    assert total_row["vehicle_id"] == "TOTAL"
    assert float(total_row["distance_km"]) == pytest.approx(100.0)
    assert float(total_row["energy_consumed_kwh"]) == pytest.approx(20.0)
    assert float(total_row["energy_per_100km_kwh"]) == pytest.approx(20.0)
    assert total_row["battery_capacity_kwh"] == ""
    assert total_row["start_time"] == _WINDOW_START.isoformat()


def test_fleet_routes_are_registered_with_their_parameters() -> None:
    """F-E1/F-A6: both fleet views are under /telemetry/fleets/{fleet_id}."""
    paths = app.openapi()["paths"]
    prefix = "/api/v1/telemetry/fleets/{fleet_id}"

    latest_parameters = {
        parameter["name"]: parameter
        for parameter in paths[f"{prefix}/vehicles/latest"]["get"]["parameters"]
    }
    report_parameters = {
        parameter["name"]: parameter
        for parameter in paths[f"{prefix}/operating-report"]["get"]["parameters"]
    }

    assert {"page", "page_size"} <= set(latest_parameters)
    assert report_parameters["start_time"]["required"] is True
    assert report_parameters["end_time"]["required"] is True
    assert report_parameters["format"].get("required", False) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("report_format", [ReportFormat.JSON, ReportFormat.CSV])
async def test_fleet_operating_report_endpoint_returns_json_or_csv(
    monkeypatch: pytest.MonkeyPatch, report_format: ReportFormat
) -> None:
    """format=csv returns a text/csv attachment; json returns the model."""
    monkeypatch.setattr(settings, "TELEMETRY_ENERGY_COST_PER_KWH_VND", 3000.0)
    fleet_report = _fleet_report()

    async def get_report(
        db: AsyncSession, fleet_id: UUID, **kwargs: object
    ) -> FleetOperatingReportResponse:
        return fleet_report

    monkeypatch.setattr(telemetry_service, "get_fleet_operating_report", get_report)

    endpoint_result = await telemetry_router.get_fleet_operating_report_endpoint(
        fleet_report.fleet_id,
        _WINDOW_START,
        _WINDOW_END,
        report_format=report_format,
        db=fake_db_session(),
    )

    if report_format is ReportFormat.JSON:
        assert endpoint_result is fleet_report
        return
    assert isinstance(endpoint_result, Response)
    assert endpoint_result.media_type == "text/csv"
    assert "attachment" in endpoint_result.headers["content-disposition"]
    assert bytes(endpoint_result.body).decode().startswith("vehicle_id,vin")
