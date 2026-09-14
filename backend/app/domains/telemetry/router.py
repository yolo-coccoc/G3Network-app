"""Router HTTP đọc dữ liệu telemetry của xe.

Module này chỉ chuyển request thành lời gọi service và chuyển ngoại lệ nghiệp
vụ thành HTTP status code; không chứa truy vấn database hoặc business logic.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telemetry.exceptions import (
    TelemetryNotFoundError,
    TelemetryPublishError,
)
from app.domains.telemetry.schemas import (
    BatteryThresholdPushResponse,
    TelemetryAlertListResponse,
    VehicleTelemetryHistoryResponse,
    VehicleTelemetryLatestResponse,
    VehicleTelemetryMapResponse,
)
from app.domains.telemetry.service import (
    get_latest_vehicle_telemetry_response,
    get_vehicle_telemetry_history_response,
    get_vehicle_telemetry_map_response,
    list_telemetry_alerts_response,
    push_battery_threshold_to_vehicle,
)
from app.domains.telemetry.types import TelemetryAlertStatus
from app.libs.db.session import get_db

router = APIRouter(tags=["telemetry"])


@router.get(
    "/vehicles/{vehicle_id}/latest",
    response_model=VehicleTelemetryLatestResponse,
    summary="Lấy telemetry mới nhất của xe",
)
async def get_latest_vehicle_telemetry(
    vehicle_id: UUID, db: AsyncSession = Depends(get_db)
) -> VehicleTelemetryLatestResponse:
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


@router.get(
    "/vehicles/{vehicle_id}/history",
    response_model=VehicleTelemetryHistoryResponse,
    summary="Lấy toàn bộ lịch sử telemetry của xe",
)
async def get_vehicle_telemetry_history(
    vehicle_id: UUID, db: AsyncSession = Depends(get_db)
) -> VehicleTelemetryHistoryResponse:
    """Trả toàn bộ telemetry lịch sử của xe, không phân trang.

    Args:
        vehicle_id: ID nội bộ của xe.
        db: Phiên database do dependency quản lý.

    Returns:
        Lịch sử telemetry theo thời gian tăng dần.

    Raises:
        HTTPException: Khi xe không tồn tại.
    """
    try:
        return await get_vehicle_telemetry_history_response(db, vehicle_id)
    except TelemetryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/vehicles/map",
    response_model=VehicleTelemetryMapResponse,
    summary="Lấy vị trí telemetry mới nhất của toàn bộ xe",
)
async def get_vehicle_telemetry_map(
    db: AsyncSession = Depends(get_db),
) -> VehicleTelemetryMapResponse:
    """Trả một marker cho mọi xe active."""
    return await get_vehicle_telemetry_map_response(db)


@router.post(
    "/vehicles/{vehicle_id}/battery-threshold/push",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatteryThresholdPushResponse,
    summary="Push ngưỡng pin tới thiết bị của xe",
)
async def push_vehicle_battery_threshold(
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> BatteryThresholdPushResponse:
    """Publish ngưỡng pin MVP 20% tới telematic đang gán cho xe.

    Raises:
        HTTPException: ``404`` nếu xe chưa có mapping; ``503`` nếu MQTT lỗi.
    """
    try:
        return await push_battery_threshold_to_vehicle(db, vehicle_id)
    except TelemetryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except TelemetryPublishError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error


@router.get(
    "/alerts",
    response_model=TelemetryAlertListResponse,
    summary="Liệt kê cảnh báo pin",
)
async def list_telemetry_alerts(
    vehicle_id: UUID | None = None,
    status_filter: TelemetryAlertStatus | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> TelemetryAlertListResponse:
    """Liệt kê cảnh báo pin theo xe hoặc trạng thái nếu có bộ lọc."""
    return await list_telemetry_alerts_response(
        db,
        vehicle_id=vehicle_id,
        status_filter=status_filter,
    )
