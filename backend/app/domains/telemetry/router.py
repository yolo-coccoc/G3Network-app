"""Router HTTP đọc dữ liệu telemetry của xe.

Module này chỉ chuyển request thành lời gọi service và chuyển ngoại lệ nghiệp
vụ thành HTTP status code; không chứa truy vấn database hoặc business logic.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telemetry.exceptions import TelemetryNotFoundError
from app.domains.telemetry.schemas import LatestVehicleTelemetryResponse
from app.domains.telemetry.service import get_latest_vehicle_telemetry_response
from app.libs.db.session import get_db

router = APIRouter(tags=["telemetry"])


@router.get(
    "/vehicles/{vehicle_id}/latest",
    response_model=LatestVehicleTelemetryResponse,
    summary="Lấy telemetry mới nhất của xe",
)
async def get_latest_vehicle_telemetry(
    vehicle_id: UUID, db: AsyncSession = Depends(get_db)
) -> LatestVehicleTelemetryResponse:
    """Trả về bản ghi telemetry mới nhất của một xe.

    Args:
        vehicle_id: ID nội bộ của xe.
        db: Phiên database do dependency quản lý.

    Returns:
        Bản ghi telemetry mới nhất.

    Raises:
        HTTPException: Khi xe không tồn tại hoặc chưa có telemetry.
    """
    try:
        return await get_latest_vehicle_telemetry_response(db, vehicle_id)
    except TelemetryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
