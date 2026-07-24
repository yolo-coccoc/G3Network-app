"""Service layer for Vehicle domain - handles business logic."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.vehicles.models import Vehicle, VehicleStatus
from app.domains.vehicles.repository import (
    create_vehicle as repo_create_vehicle,
    get_vehicle_by_id as repo_get_vehicle_by_id,
    get_vehicle_by_plate as repo_get_vehicle_by_plate,
    get_vehicle_by_vin as repo_get_vehicle_by_vin,
    get_vehicles as repo_get_vehicles,
    count_vehicles as repo_count_vehicles,
    update_vehicle as repo_update_vehicle,
    soft_delete_vehicle as repo_soft_delete_vehicle,
)
from app.domains.vehicles.schemas import (
    VehicleCreate,
    VehicleUpdate,
    VehicleResponse,
    VehicleListResponse,
)


async def create_vehicle(db: AsyncSession, vehicle_data: VehicleCreate) -> VehicleResponse:
    """Create a new vehicle.
    
    Validates that license_plate is unique before creating.
    
    Args:
        db: Async database session
        vehicle_data: Vehicle creation data
        
    Returns:
        Created vehicle response
        
    Raises:
        HTTPException: 400 if license_plate already exists
    """
    # Check if license_plate already exists
    existing_plate = await repo_get_vehicle_by_plate(db, vehicle_data.license_plate)
    if existing_plate:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Vehicle with license plate '{vehicle_data.license_plate}' already exists"
        )
    
    # Check if VIN already exists
    existing_vin = await repo_get_vehicle_by_vin(db, vehicle_data.vin)
    if existing_vin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Vehicle with VIN '{vehicle_data.vin}' already exists"
        )
    
    # Create vehicle
    vehicle_dict = vehicle_data.model_dump()
    vehicle = await repo_create_vehicle(db, vehicle_dict)
    
    return VehicleResponse.model_validate(vehicle)


async def get_vehicle(db: AsyncSession, vehicle_id: UUID) -> VehicleResponse:
    """Get a vehicle by ID.
    
    Args:
        db: Async database session
        vehicle_id: Vehicle internal ID (UUID)
        
    Returns:
        Vehicle response
        
    Raises:
        HTTPException: 404 if vehicle not found
    """
    vehicle = await repo_get_vehicle_by_id(db, vehicle_id)
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vehicle with id '{vehicle_id}' not found"
        )
    
    return VehicleResponse.model_validate(vehicle)


async def list_vehicles(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 10,
    status_filter: VehicleStatus | None = None
) -> VehicleListResponse:
    """List vehicles with pagination.
    
    Args:
        db: Async database session
        page: Page number (1-indexed)
        page_size: Number of items per page
        status_filter: Optional status filter
        
    Returns:
        Paginated list of vehicles
    """
    # Validate pagination
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 10
    if page_size > 100:
        page_size = 100
    
    skip = (page - 1) * page_size
    
    # Get vehicles and count
    vehicles = await repo_get_vehicles(db, skip, page_size, status_filter)
    total = await repo_count_vehicles(db, status_filter)
    
    return VehicleListResponse(
        items=[VehicleResponse.model_validate(v) for v in vehicles],
        total=total,
        page=page,
        page_size=page_size
    )


async def update_vehicle(
    db: AsyncSession,
    vehicle_id: UUID,
    update_data: VehicleUpdate
) -> VehicleResponse:
    """Update a vehicle.
    
    Validates that vehicle exists and license_plate is unique (if updating).
    
    Args:
        db: Async database session
        vehicle_id: Vehicle internal ID (UUID)
        update_data: Update data
        
    Returns:
        Updated vehicle response
        
    Raises:
        HTTPException: 404 if vehicle not found
        HTTPException: 400 if new license_plate already exists
    """
    # Check vehicle exists
    vehicle = await repo_get_vehicle_by_id(db, vehicle_id)
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vehicle with id '{vehicle_id}' not found"
        )
    
    # If updating license_plate, check uniqueness
    if update_data.license_plate and update_data.license_plate != vehicle.license_plate:
        existing = await repo_get_vehicle_by_plate(db, update_data.license_plate)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Vehicle with license plate '{update_data.license_plate}' already exists"
            )
    
    # Update vehicle
    update_dict = update_data.model_dump(exclude_unset=True)
    updated_vehicle = await repo_update_vehicle(db, vehicle_id, update_dict)
    
    return VehicleResponse.model_validate(updated_vehicle)


async def delete_vehicle(db: AsyncSession, vehicle_id: UUID) -> dict[str, str]:
    """Soft delete a vehicle.
    
    Args:
        db: Async database session
        vehicle_id: Vehicle ID (UUID)
        
    Returns:
        Success message
        
    Raises:
        HTTPException: 404 if vehicle not found
    """
    vehicle = await repo_soft_delete_vehicle(db, vehicle_id)
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vehicle with id '{vehicle_id}' not found"
        )
    
    return {"message": "Vehicle deleted successfully"}
