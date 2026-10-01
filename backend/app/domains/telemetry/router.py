"""HTTP router for reading vehicle telemetry data.

This module only turns requests into service calls; it contains no
database queries or business logic. Domain exceptions are not caught here:
``app/api/main.py`` maps ``TelemetryNotFoundError`` (and the fleet
domain's ``FleetNotFoundError``, raised through the fleet-wide views) to
404 and ``TelemetryInvalidRangeError`` to 400 through their shared bases.

The fleet-wide views live here, under ``/fleets/{fleet_id}/...``, rather
than in the fleet domain, so the only edge is ``telemetry -> fleet``
(planner D7 of ``backend-happy-path-completion.md``).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telemetry.service as telemetry_service
from app.domains.telemetry.schemas import (
    FleetOperatingReportResponse,
    FleetVehicleLiveStatusListResponse,
    VehicleBatteryHealthResponse,
    VehicleEnergyUsageResponse,
    VehicleOperatingReportResponse,
    VehicleTelemetryHistoryResponse,
    VehicleTelemetryLatestResponse,
)
from app.domains.telemetry.types import ReportFormat, ReportGranularity
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["telemetry"])


@router.get(
    "/vehicles/{vehicle_id}/latest",
    response_model=VehicleTelemetryLatestResponse,
    summary="Get the latest telemetry for a vehicle",
)
async def get_latest_vehicle_telemetry_endpoint(
    vehicle_id: UUID, db: AsyncSession = Depends(get_db)
) -> VehicleTelemetryLatestResponse:
    """Return the latest telemetry record for a vehicle.

    Args:
        vehicle_id: Internal ID of the vehicle.
        db: Database session managed by the dependency.

    Returns:
        The latest telemetry record.

    Raises:
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist, was
            soft-deleted, or has no telemetry yet.
    """
    return await telemetry_service.get_latest_vehicle_telemetry_response(db, vehicle_id)


@router.get(
    "/vehicles/{vehicle_id}/history",
    response_model=VehicleTelemetryHistoryResponse,
    summary="Get telemetry history for a vehicle within a time range",
)
async def get_vehicle_telemetry_history_endpoint(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    limit: int = Query(
        settings.TELEMETRY_HISTORY_DEFAULT_LIMIT,
        ge=1,
        le=settings.TELEMETRY_HISTORY_MAX_LIMIT,
    ),
    db: AsyncSession = Depends(get_db),
) -> VehicleTelemetryHistoryResponse:
    """Return a vehicle's telemetry history within a bounded time range (F-A5).

    Scoped as a time-range location/telemetry history query for trip
    replay, not segmented trips - this backend has no trip concept yet (see
    ``docs/01-requirements/future.md``). The caller narrows the time window
    if a range holds more points than ``limit``.

    Args:
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        limit: Maximum number of points to return.
        db: Database session managed by the dependency.

    Returns:
        Telemetry points ordered chronologically (oldest first).

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist or
            was soft-deleted.
    """
    return await telemetry_service.get_vehicle_telemetry_history_response(
        db,
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )


@router.get(
    "/vehicles/{vehicle_id}/operating-report",
    response_model=VehicleOperatingReportResponse,
    summary="Get a vehicle's operating performance over a time window",
    responses={200: {"content": {"text/csv": {}}}},
)
async def get_vehicle_operating_report_endpoint(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    granularity: ReportGranularity | None = None,
    report_format: ReportFormat = Query(ReportFormat.JSON, alias="format"),
    db: AsyncSession = Depends(get_db),
) -> VehicleOperatingReportResponse | Response:
    """Return distance, energy consumed, and cost for a vehicle over a window (F-A6).

    Energy is inferred from SOC drops in the vehicle's own telemetry, not
    from charging-session records. See
    ``VehicleOperatingReportResponse`` for the accuracy limits.

    Args:
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        granularity: Optional ``day``/``week``/``month`` breakdown in
            ``settings.APP_REPORT_TIMEZONE``; omitted keeps the single
            whole-window aggregate.
        report_format: ``json`` (default) or ``csv`` (query parameter
            ``format``) - CSV has one row per period, or one row for the
            whole window.
        db: Database session managed by the dependency.

    Returns:
        The operating report over the normalized time window, as JSON or
        as a ``text/csv`` attachment.

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist or
            was soft-deleted.
    """
    operating_report = await telemetry_service.get_vehicle_operating_report(
        db,
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        granularity=granularity,
    )
    if report_format is ReportFormat.CSV:
        return Response(
            content=telemetry_service.serialize_vehicle_operating_report_csv(
                operating_report
            ),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="operating-report-{vehicle_id}.csv"'
                )
            },
        )
    return operating_report


@router.get(
    "/vehicles/{vehicle_id}/battery-health",
    response_model=VehicleBatteryHealthResponse,
    summary="Get a vehicle's daily battery-health (SOH) trend",
)
async def get_vehicle_battery_health_endpoint(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    db: AsyncSession = Depends(get_db),
) -> VehicleBatteryHealthResponse:
    """Return one SOH/cycle-count point per day over a window (F-A3).

    Days are calendar days of ``settings.APP_REPORT_TIMEZONE``; days
    without an SOH or cycle-count reading are omitted.

    Args:
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        db: Database session managed by the dependency.

    Returns:
        The daily battery-health trend over the normalized window.

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist or
            was soft-deleted.
    """
    return await telemetry_service.get_vehicle_battery_health_response(
        db, vehicle_id=vehicle_id, start_time=start_time, end_time=end_time
    )


@router.get(
    "/vehicles/{vehicle_id}/energy-usage",
    response_model=VehicleEnergyUsageResponse,
    summary="Get a vehicle's (customer's) charged energy over a time window",
)
async def get_vehicle_energy_usage_endpoint(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    db: AsyncSession = Depends(get_db),
) -> VehicleEnergyUsageResponse:
    """Return energy charged into a vehicle's pack over a window (F-C6).

    "Customer" is a vehicle in this MVP. Energy is inferred from SOC
    rises in the vehicle's own telemetry, not from a station meter - see
    ``VehicleEnergyUsageResponse`` for why this cannot satisfy NF-10's
    reconciliation requirement.

    Args:
        vehicle_id: Internal ID of the vehicle (customer).
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        db: Database session managed by the dependency.

    Returns:
        The energy-usage report over the normalized time window.

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist or
            was soft-deleted.
    """
    return await telemetry_service.get_vehicle_energy_usage_report(
        db, vehicle_id=vehicle_id, start_time=start_time, end_time=end_time
    )


@router.get(
    "/fleets/{fleet_id}/vehicles/latest",
    response_model=FleetVehicleLiveStatusListResponse,
    summary="Get the newest position and online flag of every vehicle in a fleet",
)
async def list_fleet_vehicle_live_statuses_endpoint(
    fleet_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    db: AsyncSession = Depends(get_db),
) -> FleetVehicleLiveStatusListResponse:
    """Return a fleet's current member vehicles with their live status (F-E1).

    Args:
        fleet_id: Internal ID of the fleet.
        page: Page number.
        page_size: Number of records per page.
        db: Database session managed by the dependency.

    Returns:
        One page of member vehicles (oldest member first) with their VIN,
        plate, status, newest position and online flag.

    Raises:
        FleetNotFoundError: HTTP 404 - the fleet does not exist or was
            soft-deleted.
    """
    return await telemetry_service.list_fleet_vehicle_live_statuses(
        db, fleet_id, page=page, page_size=page_size
    )


@router.get(
    "/fleets/{fleet_id}/operating-report",
    response_model=FleetOperatingReportResponse,
    summary="Get a fleet's operating performance over a time window",
    responses={200: {"content": {"text/csv": {}}}},
)
async def get_fleet_operating_report_endpoint(
    fleet_id: UUID,
    start_time: datetime,
    end_time: datetime,
    report_format: ReportFormat = Query(ReportFormat.JSON, alias="format"),
    db: AsyncSession = Depends(get_db),
) -> FleetOperatingReportResponse | Response:
    """Return per-vehicle and total distance, energy and cost of a fleet (F-A6).

    Covers the fleet's current member vehicles. Totals are sums; fleet
    rates are recomputed from the sums, never averaged. See
    ``FleetOperatingReportResponse`` for the accuracy limits.

    Args:
        fleet_id: Internal ID of the fleet.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        report_format: ``json`` (default) or ``csv`` (query parameter
            ``format``) - CSV has one row per vehicle plus a final
            ``TOTAL`` row.
        db: Database session managed by the dependency.

    Returns:
        The fleet operating report, as JSON or as a ``text/csv``
        attachment.

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS``.
        FleetNotFoundError: HTTP 404 - the fleet does not exist or was
            soft-deleted.
    """
    fleet_report = await telemetry_service.get_fleet_operating_report(
        db, fleet_id, start_time=start_time, end_time=end_time
    )
    if report_format is ReportFormat.CSV:
        return Response(
            content=telemetry_service.serialize_fleet_operating_report_csv(
                fleet_report
            ),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="fleet-operating-report-{fleet_id}.csv"'
                )
            },
        )
    return fleet_report
