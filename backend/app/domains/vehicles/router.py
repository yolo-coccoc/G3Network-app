"""Router layer for Vehicle domain - defines API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.vehicles.models import VehicleStatus
from app.domains.vehicles.schemas import (
    VehicleCreate,
    VehicleUpdate,
    VehicleResponse,
    VehicleListResponse,
)
from app.domains.vehicles import service
from app.libs.db.session import get_db


router = APIRouter(tags=["vehicles"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=VehicleResponse,
    summary="Tạo xe mới",
    description="Tạo một xe mới trong hệ thống. License plate và VIN phải là duy nhất."
)
async def create_vehicle(
    vehicle_data: VehicleCreate,
    db: AsyncSession = Depends(get_db)
) -> VehicleResponse:
    """Create a new vehicle.
    
    Args:
        vehicle_data: Vehicle creation data
        db: Database session
        
    Returns:
        Created vehicle
    """
    return await service.create_vehicle(db, vehicle_data)


@router.get(
    "/",
    response_model=VehicleListResponse,
    summary="Lấy danh sách xe",
    description="Lấy danh sách xe với phân trang và lọc theo trạng thái."
)
async def list_vehicles(
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    status_filter: VehicleStatus | None = Query(None, alias="status", description="Lọc theo trạng thái"),
    db: AsyncSession = Depends(get_db)
) -> VehicleListResponse:
    """List vehicles with pagination.
    
    Args:
        page: Page number
        page_size: Items per page
        status_filter: Optional status filter
        db: Database session
        
    Returns:
        Paginated list of vehicles
    """
    return await service.list_vehicles(db, page, page_size, status_filter)


@router.get(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Lấy chi tiết xe",
    description="Lấy thông tin chi tiết của một xe theo ID."
)
async def get_vehicle(
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> VehicleResponse:
    """Get a vehicle by ID.
    
    Args:
        vehicle_id: Vehicle UUID
        db: Database session
        
    Returns:
        Vehicle details
    """
    return await service.get_vehicle(db, vehicle_id)


@router.put(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Cập nhật xe",
    description="Cập nhật thông tin xe. Chỉ cập nhật các trường được cung cấp."
)
async def update_vehicle(
    vehicle_id: UUID,
    update_data: VehicleUpdate,
    db: AsyncSession = Depends(get_db)
) -> VehicleResponse:
    """Update a vehicle.
    
    Args:
        vehicle_id: Vehicle UUID
        update_data: Update data
        db: Database session
        
    Returns:
        Updated vehicle
    """
    return await service.update_vehicle(db, vehicle_id, update_data)


@router.delete(
    "/{vehicle_id}",
    status_code=status.HTTP_200_OK,
    summary="Xoá xe",
    description="Soft delete xe. Xe vẫn còn trong database nhưng không hiển thị trong danh sách."
)
async def delete_vehicle(
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Delete a vehicle (soft delete).
    
    Args:
        vehicle_id: Vehicle UUID
        db: Database session
        
    Returns:
        Success message
    """
    return await service.delete_vehicle(db, vehicle_id)
