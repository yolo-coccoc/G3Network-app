"""HTTP router for reading vehicle telemetry data.

This module only turns requests into service calls; it contains no
database queries or business logic. Domain exceptions are not caught here:
``app/api/main.py`` maps ``TelemetryNotFoundError`` to 404 and
``TelemetryInvalidRangeError`` to 400 through their shared bases.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telemetry.service as telemetry_service
from app.domains.telemetry.schemas import (
    VehicleEnergyUsageResponse,
    VehicleOperatingReportResponse,
    VehicleTelemetryHistoryResponse,
    VehicleTelemetryLatestResponse,
)
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
)
async def get_vehicle_operating_report_endpoint(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    db: AsyncSession = Depends(get_db),
) -> VehicleOperatingReportResponse:
    """Return distance, energy consumed, and cost for a vehicle over a window (F-A6).

    Energy is inferred from SOC drops in the vehicle's own telemetry, not
    from charging-session records. See
    ``VehicleOperatingReportResponse`` for the accuracy limits.

    Args:
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        db: Database session managed by the dependency.

    Returns:
        The operating report over the normalized time window.

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist or
            was soft-deleted.
    """
    return await telemetry_service.get_vehicle_operating_report(
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
