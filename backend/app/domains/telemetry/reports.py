"""Pure calculations behind the vehicle reports and the battery-health trend.

Feature code: F-A6 (Operating performance report, computed from SOC drops
in telemetry - charging_sessions carries no vehicle linkage - with an
optional per-period breakdown and CSV export, and the fleet rollup), F-C6
(Per-customer energy usage, computed from SOC rises in the same telemetry
history; "customer" is a vehicle in this MVP), F-A3 (daily battery-health
trend).

Internal to the telemetry domain: other domains never import this module
(they go through ``telemetry/service.py``). Everything here is pure - the
service validates the window, looks up the vehicle and folds the window's
telemetry (``repository.get_vehicle_window_summary``, and
``list_vehicle_period_summaries`` for a breakdown), then hands the result
to the ``build_*`` functions here. A fleet rollup is built from its member
vehicles' additive ``VehicleOperatingSummary`` values: the sums are added
first and every fleet-wide rate is recomputed from them. The only
non-argument input is
``settings.TELEMETRY_ENERGY_COST_PER_KWH_VND``, read at call time.

Accuracy limits (see the response schemas for the full list): energy is
gross, not net (SOC rises are not netted out of F-A6, drops not out of
F-C6); sparse telemetry under-counts; capacity is nominal, not
SOH-adjusted; the tariff is one flat configured rate.
"""

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.domains.telemetry.schemas import (
    FleetOperatingReportResponse,
    FleetOperatingReportTotals,
    FleetVehicleOperatingReportRow,
    VehicleBatteryHealthPoint,
    VehicleBatteryHealthResponse,
    VehicleEnergyUsageResponse,
    VehicleOperatingReportPeriod,
    VehicleOperatingReportResponse,
)
from app.domains.telemetry.types import (
    DEFAULT_BATTERY_CAPACITY_KWH,
    ReportGranularity,
    TelemetryReportPeriod,
    VehicleBatteryHealthDay,
    VehicleOperatingSummary,
    VehicleTelemetryWindowSummary,
)
from app.domains.vehicles.types import VehicleReference
from app.libs.common.config import settings

# A rate needs an interval: with fewer than two samples in the window
# nothing between two readings was measured, so every derived rate is
# undefined (None) rather than a fabricated 0.
MIN_SAMPLES_FOR_RATES = 2

# Column order of the F-A6 CSV export: one row per period (or one row for
# the whole window). Timestamps are UTC ISO 8601; an undefined value is an
# empty cell.
OPERATING_REPORT_CSV_COLUMNS: tuple[str, ...] = (
    "vehicle_id",
    "period_start",
    "period_end",
    "sample_count",
    "odometer_sample_count",
    "first_recorded_at",
    "last_recorded_at",
    "distance_km",
    "energy_consumed_kwh",
    "energy_per_100km_kwh",
    "distance_per_day_km",
    "energy_cost_vnd",
    "cost_per_km_vnd",
    "battery_capacity_kwh",
    "cost_per_kwh_vnd",
)


# Column order of the fleet F-A6 CSV export: one row per vehicle, then one
# final row whose vehicle_id is FLEET_TOTAL_ROW_LABEL. The window and the
# tariff are repeated on every row so a row stays meaningful on its own.
FLEET_OPERATING_REPORT_CSV_COLUMNS: tuple[str, ...] = (
    "vehicle_id",
    "vin",
    "start_time",
    "end_time",
    "sample_count",
    "distance_km",
    "energy_consumed_kwh",
    "energy_per_100km_kwh",
    "energy_cost_vnd",
    "cost_per_km_vnd",
    "battery_capacity_kwh",
    "is_default_battery_capacity",
    "cost_per_kwh_vnd",
)

# vehicle_id cell of the fleet CSV's final totals row.
FLEET_TOTAL_ROW_LABEL = "TOTAL"


@dataclass(frozen=True)
class FleetReportVehicle:
    """One member vehicle's input to a fleet operating report (F-A6).

    Attributes:
        vin: VIN of the vehicle, shown in its report row.
        operating_summary: The vehicle's additive figures over the window.
    """

    vin: str
    operating_summary: VehicleOperatingSummary


@dataclass(frozen=True)
class VehicleReportPeriodSummary:
    """One period of a broken-down report and its folded telemetry (F-A6).

    Attributes:
        period: The period's clipped bounds and bucket key.
        window_summary: The vehicle's telemetry folded over the period
            (all zeros when the period has no reading).
    """

    period: TelemetryReportPeriod
    window_summary: VehicleTelemetryWindowSummary


@dataclass(frozen=True)
class VehicleReportContext:
    """Everything a report needs, already validated and looked up by the service.

    Attributes:
        vehicle_reference: The reported vehicle (its id and recorded
            battery capacity, if any).
        start_time: Normalized (UTC) lower bound of the requested window.
        end_time: Normalized (UTC) upper bound of the requested window;
            always after ``start_time``.
        window_summary: The vehicle's telemetry folded over the window.
        granularity: Requested period breakdown, or ``None`` for the
            whole-window aggregate only.
        period_summaries: Every period of the window in order (empty when
            ``granularity`` is ``None``).
    """

    vehicle_reference: VehicleReference
    start_time: datetime
    end_time: datetime
    window_summary: VehicleTelemetryWindowSummary
    granularity: ReportGranularity | None = None
    period_summaries: tuple[VehicleReportPeriodSummary, ...] = ()


def calculate_energy_kwh(soc_percent: float, battery_capacity_kwh: float) -> float:
    """Convert a summed SOC percentage into energy using pack capacity.

    Args:
        soc_percent: Summed SOC delta (%), already clamped non-negative.
        battery_capacity_kwh: Pack capacity to convert against - the
            vehicle's recorded value or the engineering default.

    Returns:
        Energy in kWh.
    """
    return soc_percent / 100.0 * battery_capacity_kwh


def calculate_energy_cost_vnd(energy_kwh: float, cost_per_kwh_vnd: float) -> float:
    """Price energy at a flat tariff (F-A6).

    Args:
        energy_kwh: Energy amount to price.
        cost_per_kwh_vnd: Tariff in VND per kWh
            (``settings.TELEMETRY_ENERGY_COST_PER_KWH_VND``).

    Returns:
        Cost in VND.
    """
    return energy_kwh * cost_per_kwh_vnd


def calculate_energy_per_100km_kwh(
    energy_kwh: float, distance_km: float
) -> float | None:
    """Compute energy intensity, or None when no distance was recorded.

    Args:
        energy_kwh: Energy consumed over the window.
        distance_km: Distance traveled over the window.

    Returns:
        kWh per 100 km, or ``None`` if ``distance_km`` is 0 - reporting a
        rate against zero distance would fabricate a number rather than
        state "undefined" (a parked vehicle can still consume energy via
        HVAC, so ``energy_kwh > 0`` here is legitimate, not a bug).
    """
    if distance_km <= 0:
        return None
    return energy_kwh / distance_km * 100.0


def calculate_cost_per_km_vnd(
    energy_cost_vnd: float, distance_km: float
) -> float | None:
    """Compute cost per kilometre, or None when no distance was recorded.

    Args:
        energy_cost_vnd: Total energy cost over the window.
        distance_km: Distance traveled over the window.

    Returns:
        VND per km, or ``None`` if ``distance_km`` is 0 - same
        undefined-rather-than-zero reasoning as
        ``calculate_energy_per_100km_kwh``.
    """
    if distance_km <= 0:
        return None
    return energy_cost_vnd / distance_km


def calculate_distance_per_day_km(
    distance_km: float, start_time: datetime, end_time: datetime
) -> float:
    """Compute average daily distance across the *requested* window.

    Args:
        distance_km: Distance traveled over the window.
        start_time: Normalized start of the requested window.
        end_time: Normalized end of the requested window.

    Returns:
        km/day, using the requested span as the denominator - not the
        observed first-to-last sample span. Dividing by the observed span
        would silently rescale: a vehicle that reported for one hour of a
        30-day window would read as if it drove that hour's distance
        every day. Callers see ``sample_count``/``first_recorded_at``/
        ``last_recorded_at`` and can judge coverage themselves. Never
        divides by zero: the caller has already rejected
        ``end_time <= start_time``.
    """
    window_days = (end_time - start_time).total_seconds() / 86400.0
    return distance_km / window_days


def _select_battery_capacity(
    vehicle_reference: VehicleReference,
) -> tuple[float, bool]:
    """Choose the pack capacity to use for a kWh conversion (F-A6/F-C6).

    Args:
        vehicle_reference: The vehicle's cross-domain reference DTO.

    Returns:
        ``(battery_capacity_kwh, is_default_battery_capacity)`` - the
        vehicle's recorded capacity if present, else
        ``DEFAULT_BATTERY_CAPACITY_KWH`` with the flag set so the
        response can tell a consumer the number is an estimate.
    """
    if vehicle_reference.battery_capacity_kwh is not None:
        return vehicle_reference.battery_capacity_kwh, False
    return DEFAULT_BATTERY_CAPACITY_KWH, True


def _build_operating_metrics(
    window_summary: VehicleTelemetryWindowSummary,
    *,
    battery_capacity_kwh: float,
    cost_per_kwh_vnd: float,
    start_time: datetime,
    end_time: datetime,
) -> dict[str, Any]:
    """Compute the F-A6 metrics of one folded span (whole window or period).

    Rule:
        Every derived rate (``energy_per_100km_kwh``,
        ``distance_per_day_km``, ``cost_per_km_vnd``) is ``None`` when it's
        undefined - fewer than ``MIN_SAMPLES_FOR_RATES`` telemetry samples
        in the span (no interval was measurable), or zero distance
        traveled. The raw sums (``distance_km``, ``energy_consumed_kwh``,
        ``energy_cost_vnd``) are always numbers, 0 when nothing happened.

    Args:
        window_summary: Telemetry folded over the span.
        battery_capacity_kwh: Pack capacity for the kWh conversion.
        cost_per_kwh_vnd: Tariff for the cost figures.
        start_time: Start of the span (the km/day denominator).
        end_time: End of the span; after ``start_time``.

    Returns:
        Keyword arguments for the metric fields that
        ``VehicleOperatingReportResponse`` and
        ``VehicleOperatingReportPeriod`` share.
    """
    energy_consumed_kwh = calculate_energy_kwh(
        window_summary.soc_discharge_percent, battery_capacity_kwh
    )
    energy_cost_vnd = calculate_energy_cost_vnd(energy_consumed_kwh, cost_per_kwh_vnd)

    energy_per_100km_kwh: float | None = None
    cost_per_km_vnd: float | None = None
    distance_per_day_km: float | None = None
    if window_summary.sample_count >= MIN_SAMPLES_FOR_RATES:
        energy_per_100km_kwh = calculate_energy_per_100km_kwh(
            energy_consumed_kwh, window_summary.distance_km
        )
        cost_per_km_vnd = calculate_cost_per_km_vnd(
            energy_cost_vnd, window_summary.distance_km
        )
        distance_per_day_km = calculate_distance_per_day_km(
            window_summary.distance_km, start_time, end_time
        )

    return {
        "sample_count": window_summary.sample_count,
        "odometer_sample_count": window_summary.odometer_sample_count,
        "first_recorded_at": window_summary.first_recorded_at,
        "last_recorded_at": window_summary.last_recorded_at,
        "distance_km": window_summary.distance_km,
        "energy_consumed_kwh": energy_consumed_kwh,
        "energy_per_100km_kwh": energy_per_100km_kwh,
        "distance_per_day_km": distance_per_day_km,
        "energy_cost_vnd": energy_cost_vnd,
        "cost_per_km_vnd": cost_per_km_vnd,
    }


def build_operating_report(
    report_context: VehicleReportContext,
) -> VehicleOperatingReportResponse:
    """Assemble the F-A6 operating report from a folded telemetry window.

    The whole-window metrics are always present; with a granularity the
    same metrics are added per period (see ``_build_operating_metrics``
    for when a rate is ``None``). The tariff is read once from
    ``settings.TELEMETRY_ENERGY_COST_PER_KWH_VND`` so the window and its
    periods are priced alike.

    Args:
        report_context: Validated window, vehicle, folded summary and, if
            requested, the folded periods.

    Returns:
        The operating report over the normalized window.
    """
    battery_capacity_kwh, is_default_battery_capacity = _select_battery_capacity(
        report_context.vehicle_reference
    )
    cost_per_kwh_vnd = settings.TELEMETRY_ENERGY_COST_PER_KWH_VND

    periods: list[VehicleOperatingReportPeriod] | None = None
    if report_context.granularity is not None:
        periods = [
            VehicleOperatingReportPeriod(
                period_start=period_summary.period.period_start,
                period_end=period_summary.period.period_end,
                **_build_operating_metrics(
                    period_summary.window_summary,
                    battery_capacity_kwh=battery_capacity_kwh,
                    cost_per_kwh_vnd=cost_per_kwh_vnd,
                    start_time=period_summary.period.period_start,
                    end_time=period_summary.period.period_end,
                ),
            )
            for period_summary in report_context.period_summaries
        ]

    return VehicleOperatingReportResponse(
        vehicle_id=report_context.vehicle_reference.vehicle_id,
        start_time=report_context.start_time,
        end_time=report_context.end_time,
        battery_capacity_kwh=battery_capacity_kwh,
        is_default_battery_capacity=is_default_battery_capacity,
        cost_per_kwh_vnd=cost_per_kwh_vnd,
        granularity=report_context.granularity,
        periods=periods,
        **_build_operating_metrics(
            report_context.window_summary,
            battery_capacity_kwh=battery_capacity_kwh,
            cost_per_kwh_vnd=cost_per_kwh_vnd,
            start_time=report_context.start_time,
            end_time=report_context.end_time,
        ),
    )


def build_operating_summary(
    report_context: VehicleReportContext,
) -> VehicleOperatingSummary:
    """Project a folded window into the additive cross-domain summary (F-A6/F-E1).

    Args:
        report_context: Validated window, vehicle and folded summary.

    Returns:
        The vehicle's distance, consumed energy, sample counts and the
        capacity used - only sums and counts, so a caller can aggregate
        several vehicles before deriving any rate.
    """
    window_summary = report_context.window_summary
    battery_capacity_kwh, is_default_battery_capacity = _select_battery_capacity(
        report_context.vehicle_reference
    )
    return VehicleOperatingSummary(
        vehicle_id=report_context.vehicle_reference.vehicle_id,
        start_time=report_context.start_time,
        end_time=report_context.end_time,
        distance_km=window_summary.distance_km,
        energy_consumed_kwh=calculate_energy_kwh(
            window_summary.soc_discharge_percent, battery_capacity_kwh
        ),
        sample_count=window_summary.sample_count,
        odometer_sample_count=window_summary.odometer_sample_count,
        first_recorded_at=window_summary.first_recorded_at,
        last_recorded_at=window_summary.last_recorded_at,
        battery_capacity_kwh=battery_capacity_kwh,
        is_default_battery_capacity=is_default_battery_capacity,
    )


def build_fleet_vehicle_operating_row(
    report_vehicle: FleetReportVehicle, *, cost_per_kwh_vnd: float
) -> FleetVehicleOperatingReportRow:
    """Compute one vehicle's row of a fleet operating report (F-A6).

    Args:
        report_vehicle: The vehicle's VIN and additive window figures.
        cost_per_kwh_vnd: Tariff for the cost figures.

    Returns:
        The row; a rate is ``None`` with fewer than
        ``MIN_SAMPLES_FOR_RATES`` samples or zero distance, as in the
        per-vehicle report.
    """
    operating_summary = report_vehicle.operating_summary
    energy_cost_vnd = calculate_energy_cost_vnd(
        operating_summary.energy_consumed_kwh, cost_per_kwh_vnd
    )
    energy_per_100km_kwh: float | None = None
    cost_per_km_vnd: float | None = None
    if operating_summary.sample_count >= MIN_SAMPLES_FOR_RATES:
        energy_per_100km_kwh = calculate_energy_per_100km_kwh(
            operating_summary.energy_consumed_kwh, operating_summary.distance_km
        )
        cost_per_km_vnd = calculate_cost_per_km_vnd(
            energy_cost_vnd, operating_summary.distance_km
        )
    return FleetVehicleOperatingReportRow(
        vehicle_id=operating_summary.vehicle_id,
        vin=report_vehicle.vin,
        sample_count=operating_summary.sample_count,
        distance_km=operating_summary.distance_km,
        energy_consumed_kwh=operating_summary.energy_consumed_kwh,
        energy_per_100km_kwh=energy_per_100km_kwh,
        energy_cost_vnd=energy_cost_vnd,
        cost_per_km_vnd=cost_per_km_vnd,
        battery_capacity_kwh=operating_summary.battery_capacity_kwh,
        is_default_battery_capacity=operating_summary.is_default_battery_capacity,
    )


def build_fleet_operating_report(
    fleet_id: UUID,
    *,
    start_time: datetime,
    end_time: datetime,
    report_vehicles: list[FleetReportVehicle],
) -> FleetOperatingReportResponse:
    """Assemble the F-A6 fleet rollup from its members' additive figures.

    Rule:
        Totals add up the vehicles' sample counts, distances and energies;
        the total cost prices the summed energy, and each total rate is
        the summed energy/cost over the summed distance (``None`` when the
        fleet traveled no distance) - never an average of per-vehicle
        rates. Any distance implies a measured interval, so the totals
        need no sample-count gate. The tariff is read once from
        ``settings.TELEMETRY_ENERGY_COST_PER_KWH_VND``.

    Args:
        fleet_id: Internal ID of the fleet.
        start_time: Normalized (UTC) lower bound used.
        end_time: Normalized (UTC) upper bound used.
        report_vehicles: Included member vehicles, in report order.

    Returns:
        The fleet report with one row per vehicle and the totals.
    """
    cost_per_kwh_vnd = settings.TELEMETRY_ENERGY_COST_PER_KWH_VND
    vehicle_rows = [
        build_fleet_vehicle_operating_row(
            report_vehicle, cost_per_kwh_vnd=cost_per_kwh_vnd
        )
        for report_vehicle in report_vehicles
    ]
    total_distance_km = sum(row.distance_km for row in vehicle_rows)
    total_energy_kwh = sum(row.energy_consumed_kwh for row in vehicle_rows)
    total_cost_vnd = calculate_energy_cost_vnd(total_energy_kwh, cost_per_kwh_vnd)
    totals = FleetOperatingReportTotals(
        vehicle_count=len(vehicle_rows),
        sample_count=sum(row.sample_count for row in vehicle_rows),
        distance_km=total_distance_km,
        energy_consumed_kwh=total_energy_kwh,
        energy_per_100km_kwh=calculate_energy_per_100km_kwh(
            total_energy_kwh, total_distance_km
        ),
        energy_cost_vnd=total_cost_vnd,
        cost_per_km_vnd=calculate_cost_per_km_vnd(total_cost_vnd, total_distance_km),
    )
    return FleetOperatingReportResponse(
        fleet_id=fleet_id,
        start_time=start_time,
        end_time=end_time,
        cost_per_kwh_vnd=cost_per_kwh_vnd,
        vehicles=vehicle_rows,
        totals=totals,
    )


def _format_csv_value(value: object) -> str:
    """Render one CSV cell: UTC ISO 8601 timestamps, empty for ``None``.

    Args:
        value: A report field value.

    Returns:
        The cell text.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def serialize_operating_report_csv(report: VehicleOperatingReportResponse) -> str:
    """Serialize an F-A6 operating report as CSV text.

    One header row (``OPERATING_REPORT_CSV_COLUMNS``), then one row per
    period when the report has a breakdown, else one row for the whole
    window (``period_start``/``period_end`` = the window bounds).

    Args:
        report: The operating report to export.

    Returns:
        CSV text (``csv`` module defaults: comma separator, CRLF rows).
    """
    shared_values: dict[str, object] = {
        "vehicle_id": report.vehicle_id,
        "battery_capacity_kwh": report.battery_capacity_kwh,
        "cost_per_kwh_vnd": report.cost_per_kwh_vnd,
    }
    if report.periods is not None:
        csv_rows = [
            {**shared_values, **period.model_dump()} for period in report.periods
        ]
    else:
        csv_rows = [
            {
                **report.model_dump(),
                **shared_values,
                "period_start": report.start_time,
                "period_end": report.end_time,
            }
        ]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(OPERATING_REPORT_CSV_COLUMNS)
    for csv_row in csv_rows:
        writer.writerow(
            [
                _format_csv_value(csv_row[column])
                for column in OPERATING_REPORT_CSV_COLUMNS
            ]
        )
    return buffer.getvalue()


def serialize_fleet_operating_report_csv(report: FleetOperatingReportResponse) -> str:
    """Serialize an F-A6 fleet operating report as CSV text.

    One header row (``FLEET_OPERATING_REPORT_CSV_COLUMNS``), one row per
    vehicle, then a final row whose ``vehicle_id`` is ``TOTAL`` holding
    the totals (its ``vin``, ``battery_capacity_kwh`` and
    ``is_default_battery_capacity`` cells are empty).

    Args:
        report: The fleet report to export.

    Returns:
        CSV text (``csv`` module defaults: comma separator, CRLF rows).
    """
    shared_values: dict[str, object] = {
        "start_time": report.start_time,
        "end_time": report.end_time,
        "cost_per_kwh_vnd": report.cost_per_kwh_vnd,
    }
    csv_rows: list[dict[str, object]] = [
        {**vehicle_row.model_dump(), **shared_values} for vehicle_row in report.vehicles
    ]
    csv_rows.append(
        {
            **report.totals.model_dump(),
            **shared_values,
            "vehicle_id": FLEET_TOTAL_ROW_LABEL,
            "vin": None,
            "battery_capacity_kwh": None,
            "is_default_battery_capacity": None,
        }
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(FLEET_OPERATING_REPORT_CSV_COLUMNS)
    for csv_row in csv_rows:
        writer.writerow(
            [
                _format_csv_value(csv_row[column])
                for column in FLEET_OPERATING_REPORT_CSV_COLUMNS
            ]
        )
    return buffer.getvalue()


def calculate_estimated_capacity_kwh(
    soh_percent: float | None, battery_capacity_kwh: float | None
) -> float | None:
    """Estimate usable pack capacity from SOH and the nominal capacity (F-A3).

    Args:
        soh_percent: State of health (%), or ``None`` if not reported.
        battery_capacity_kwh: The vehicle's recorded nominal capacity, or
            ``None`` if not recorded. The engineering default is
            deliberately not used: an estimate built on an assumed
            capacity would read as a measured fade.

    Returns:
        ``soh_percent / 100 * battery_capacity_kwh``, or ``None`` when
        either input is unknown.
    """
    if soh_percent is None or battery_capacity_kwh is None:
        return None
    return soh_percent / 100.0 * battery_capacity_kwh


def build_battery_health_response(
    vehicle_reference: VehicleReference,
    *,
    start_time: datetime,
    end_time: datetime,
    health_days: list[VehicleBatteryHealthDay],
) -> VehicleBatteryHealthResponse:
    """Assemble the F-A3 daily battery-health trend.

    Args:
        vehicle_reference: The vehicle (id and recorded capacity).
        start_time: Normalized (UTC) lower bound used.
        end_time: Normalized (UTC) upper bound used.
        health_days: Per-day SOH/cycle values from the repository, oldest
            first, days without data already omitted.

    Returns:
        The trend response with an estimated capacity per point.
    """
    points = [
        VehicleBatteryHealthPoint(
            day_start=health_day.day_start,
            soh_percent=health_day.soh_percent,
            cycle_count=health_day.cycle_count,
            estimated_capacity_kwh=calculate_estimated_capacity_kwh(
                health_day.soh_percent, vehicle_reference.battery_capacity_kwh
            ),
        )
        for health_day in health_days
    ]
    return VehicleBatteryHealthResponse(
        vehicle_id=vehicle_reference.vehicle_id,
        start_time=start_time,
        end_time=end_time,
        battery_capacity_kwh=vehicle_reference.battery_capacity_kwh,
        points=points,
        count=len(points),
    )


def build_energy_usage_report(
    report_context: VehicleReportContext,
) -> VehicleEnergyUsageResponse:
    """Assemble the F-C6 energy-usage report from a folded telemetry window.

    Energy charged is the window's summed SOC rises converted to kWh - the
    mirror image of F-A6's SOC-drop sum, from the same single-scan
    aggregate.

    Args:
        report_context: Validated window, vehicle and folded summary.

    Returns:
        The energy-usage report over the normalized window.
    """
    window_summary = report_context.window_summary
    battery_capacity_kwh, is_default_battery_capacity = _select_battery_capacity(
        report_context.vehicle_reference
    )
    return VehicleEnergyUsageResponse(
        vehicle_id=report_context.vehicle_reference.vehicle_id,
        start_time=report_context.start_time,
        end_time=report_context.end_time,
        sample_count=window_summary.sample_count,
        first_recorded_at=window_summary.first_recorded_at,
        last_recorded_at=window_summary.last_recorded_at,
        energy_charged_kwh=calculate_energy_kwh(
            window_summary.soc_charge_percent, battery_capacity_kwh
        ),
        battery_capacity_kwh=battery_capacity_kwh,
        is_default_battery_capacity=is_default_battery_capacity,
    )
