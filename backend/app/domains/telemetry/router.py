"""HTTP router for reading vehicle telemetry data.

This module only turns requests into service calls and maps business
exceptions to HTTP status codes; it contains no database queries or
business logic.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telemetry.exceptions import (
    TelemetryInvalidRangeError,
    TelemetryNotFoundError,
)
from app.domains.telemetry.schemas import (
    VehicleTelemetryHistoryResponse,
    VehicleTelemetryLatestResponse,
)
from app.domains.telemetry.service import (
    get_latest_vehicle_telemetry_response,
    get_vehicle_telemetry_history_response,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["telemetry"])


@router.get(
    "/vehicles/{vehicle_id}/latest",
    response_model=VehicleTelemetryLatestResponse,
    summary="Get the latest telemetry for a vehicle",
)
async def get_latest_vehicle_telemetry(
    vehicle_id: UUID, db: AsyncSession = Depends(get_db)
) -> VehicleTelemetryLatestResponse:
    """Return the latest telemetry record for a vehicle.

    Args:
        vehicle_id: Internal ID of the vehicle.
        db: Database session managed by the dependency.

    Returns:
        The latest telemetry record.

    Raises:
        HTTPException: When the vehicle does not exist or has no telemetry yet.
    """
    try:
        return await get_latest_vehicle_telemetry_response(db, vehicle_id)
    except TelemetryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


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
        HTTPException: ``400`` if the time range is invalid (missing
            timezone, ``end_time`` not after ``start_time``, or the span
            exceeds the configured maximum); ``404`` if the vehicle does not
            exist or has been soft deleted.
    """
    try:
        return await get_vehicle_telemetry_history_response(
            db,
            vehicle_id=vehicle_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
    except TelemetryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except TelemetryInvalidRangeError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
