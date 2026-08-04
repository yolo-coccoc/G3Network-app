"""FastAPI router cho các endpoint HTTP của domain vehicles."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.service as vehicle_service
from app.domains.vehicles.exceptions import (
    VehicleConflictError,
    VehicleNotFoundError,
)
from app.domains.vehicles.schemas import (
    VehicleCreateRequest,
    VehicleListResponse,
    VehicleResponse,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["vehicles"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=VehicleResponse,
    summary="Tạo xe mới",
    description="Tạo một xe mới trong hệ thống. License plate và VIN phải là duy nhất.",
)
async def create_vehicle_endpoint(
    vehicle_create_request: VehicleCreateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> VehicleResponse:
    """Tạo một xe mới.

    Args:
        vehicle_create_request: Dữ liệu request tạo xe.
        db_session: Phiên database do HTTP boundary sở hữu.

    Returns:
        Created vehicle
    """
    try:
        return await vehicle_service.create_vehicle(
            db_session,
            vehicle_create_request,
        )
    except VehicleConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


@router.get(
    "/",
    response_model=VehicleListResponse,
    summary="Lấy danh sách xe",
    description="Lấy danh sách xe với phân trang và lọc theo trạng thái.",
)
async def list_vehicles_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Số trang"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Số bản ghi mỗi trang",
    ),
    status_filter: VehicleStatus | None = Query(
        None, alias="status", description="Lọc theo trạng thái"
    ),
    db_session: AsyncSession = Depends(get_db),
) -> VehicleListResponse:
    """Lấy danh sách xe có phân trang.

    Args:
        page: Số trang.
        page_size: Số bản ghi mỗi trang.
        status_filter: Bộ lọc trạng thái nếu có.
        db_session: Phiên database do HTTP boundary sở hữu.

    Returns:
        Paginated list of vehicles
    """
    return await vehicle_service.list_vehicles(
        db_session,
        page,
        page_size,
        status_filter,
    )


@router.get(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Lấy chi tiết xe",
    description="Lấy thông tin chi tiết của một xe theo ID.",
)
async def get_vehicle_endpoint(
    vehicle_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> VehicleResponse:
    """Lấy chi tiết một xe theo ID.

    Args:
        vehicle_id: ID nội bộ của xe.
        db_session: Phiên database do HTTP boundary sở hữu.
        db: Database session

    Returns:
        Vehicle details
    """
    try:
        return await vehicle_service.get_vehicle(db_session, vehicle_id)
    except VehicleNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Cập nhật xe",
    description="Cập nhật thông tin xe. Chỉ cập nhật các trường được cung cấp.",
)
async def update_vehicle_endpoint(
    vehicle_id: UUID,
    vehicle_update_request: VehicleUpdateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> VehicleResponse:
    """Cập nhật từng phần một xe.

    Args:
        vehicle_id: ID nội bộ của xe.
        vehicle_update_request: Dữ liệu request cập nhật xe.
        db_session: Phiên database do HTTP boundary sở hữu.

    Returns:
        Updated vehicle
    """
    try:
        return await vehicle_service.update_vehicle(
            db_session,
            vehicle_id,
            vehicle_update_request,
        )
    except VehicleNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except VehicleConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


@router.delete(
    "/{vehicle_id}",
    status_code=status.HTTP_200_OK,
    summary="Xoá xe",
    description="Soft delete xe. Xe vẫn còn trong database nhưng không hiển thị trong danh sách.",
)
async def soft_delete_vehicle_endpoint(
    vehicle_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Soft delete một xe.

    Args:
        vehicle_id: ID nội bộ của xe.
        db_session: Phiên database do HTTP boundary sở hữu.
        db: Database session

    Returns:
        Success message
    """
    try:
        return await vehicle_service.soft_delete_vehicle(db_session, vehicle_id)
    except VehicleNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
