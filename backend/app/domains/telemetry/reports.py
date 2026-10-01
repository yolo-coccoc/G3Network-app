"""Pure calculations behind the vehicle operating and energy-usage reports.

Feature code: F-A6 (Operating performance report, computed from SOC drops
in telemetry - charging_sessions carries no vehicle linkage), F-C6
(Per-customer energy usage, computed from SOC rises in the same telemetry
history; "customer" is a vehicle in this MVP).

Internal to the telemetry domain: other domains never import this module
(they go through ``telemetry/service.py``). Everything here is pure - the
service validates the window, looks up the vehicle and folds the window's
telemetry (``repository.get_vehicle_window_summary``), then hands the
result to ``build_operating_report``/``build_energy_usage_report``.

Accuracy limits (see the response schemas for the full list): energy is
gross, not net (SOC rises are not netted out of F-A6, drops not out of
F-C6); sparse telemetry under-counts; capacity is nominal, not
SOH-adjusted; the tariff is a flat engineering default.
"""

from dataclasses import dataclass
from datetime import datetime

from app.domains.telemetry.schemas import (
    VehicleEnergyUsageResponse,
    VehicleOperatingReportResponse,
)
from app.domains.telemetry.types import (
    DEFAULT_BATTERY_CAPACITY_KWH,
    ENERGY_COST_PER_KWH_VND,
    VehicleTelemetryWindowSummary,
)
from app.domains.vehicles.types import VehicleReference

# A rate needs an interval: with fewer than two samples in the window
# nothing between two readings was measured, so every derived rate is
# undefined (None) rather than a fabricated 0.
MIN_SAMPLES_FOR_RATES = 2


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
    """

    vehicle_reference: VehicleReference
    start_time: datetime
    end_time: datetime
    window_summary: VehicleTelemetryWindowSummary


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


def calculate_energy_cost_vnd(energy_kwh: float) -> float:
    """Price energy at the flat engineering-default tariff (F-A6).

    Args:
        energy_kwh: Energy amount to price.

    Returns:
        Cost in VND at ``ENERGY_COST_PER_KWH_VND``.
    """
    return energy_kwh * ENERGY_COST_PER_KWH_VND


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


def build_operating_report(
    report_context: VehicleReportContext,
) -> VehicleOperatingReportResponse:
    """Assemble the F-A6 operating report from a folded telemetry window.

    Rule:
        Every derived rate (``energy_per_100km_kwh``,
        ``distance_per_day_km``, ``cost_per_km_vnd``) is ``None`` when it's
        undefined - fewer than ``MIN_SAMPLES_FOR_RATES`` telemetry samples
        in the window (no interval was measurable), or zero distance
        traveled. The raw sums (``distance_km``, ``energy_consumed_kwh``,
        ``energy_cost_vnd``) are always numbers, 0 when nothing happened.

    Args:
        report_context: Validated window, vehicle and folded summary.

    Returns:
        The operating report over the normalized window.
    """
    window_summary = report_context.window_summary
    battery_capacity_kwh, is_default_battery_capacity = _select_battery_capacity(
        report_context.vehicle_reference
    )
    energy_consumed_kwh = calculate_energy_kwh(
        window_summary.soc_discharge_percent, battery_capacity_kwh
    )
    energy_cost_vnd = calculate_energy_cost_vnd(energy_consumed_kwh)

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
            window_summary.distance_km,
            report_context.start_time,
            report_context.end_time,
        )

    return VehicleOperatingReportResponse(
        vehicle_id=report_context.vehicle_reference.vehicle_id,
        start_time=report_context.start_time,
        end_time=report_context.end_time,
        sample_count=window_summary.sample_count,
        odometer_sample_count=window_summary.odometer_sample_count,
        first_recorded_at=window_summary.first_recorded_at,
        last_recorded_at=window_summary.last_recorded_at,
        distance_km=window_summary.distance_km,
        energy_consumed_kwh=energy_consumed_kwh,
        energy_per_100km_kwh=energy_per_100km_kwh,
        distance_per_day_km=distance_per_day_km,
        energy_cost_vnd=energy_cost_vnd,
        cost_per_km_vnd=cost_per_km_vnd,
        battery_capacity_kwh=battery_capacity_kwh,
        is_default_battery_capacity=is_default_battery_capacity,
        cost_per_kwh_vnd=ENERGY_COST_PER_KWH_VND,
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
