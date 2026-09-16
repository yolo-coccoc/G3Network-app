"""HTTP router for reading vehicle telemetry data.

This module only turns requests into service calls and maps business
exceptions to HTTP status codes; it contains no database queries or
business logic.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telemetry.exceptions import TelemetryNotFoundError
from app.domains.telemetry.schemas import VehicleTelemetryLatestResponse
from app.domains.telemetry.service import get_latest_vehicle_telemetry_response
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
