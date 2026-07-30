"""Service layer for Vehicle domain - handles business logic."""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.vehicles.exceptions import (
    VehicleConflictError,
    VehicleNotFoundError,
)
from app.domains.vehicles.repository import count_vehicles as repo_count_vehicles
from app.domains.vehicles.repository import create_vehicle as repo_create_vehicle
from app.domains.vehicles.repository import get_vehicle_by_id as repo_get_vehicle_by_id
from app.domains.vehicles.repository import (
    get_vehicle_by_plate as repo_get_vehicle_by_plate,
)
from app.domains.vehicles.repository import (
    get_vehicle_by_vin as repo_get_vehicle_by_vin,
)
from app.domains.vehicles.repository import get_vehicles as repo_get_vehicles
from app.domains.vehicles.repository import (
    soft_delete_vehicle as repo_soft_delete_vehicle,
)
from app.domains.vehicles.repository import update_vehicle as repo_update_vehicle
from app.domains.vehicles.schemas import (
    VehicleCreate,
    VehicleListResponse,
    VehicleResponse,
    VehicleUpdate,
)
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings


async def find_active_vehicle_by_vin(
    db: AsyncSession, vin: str
) -> VehicleResponse | None:
    """Tìm xe chưa bị xoá theo VIN để domain khác resolve mapping.

    Args:
        db: Phiên database hiện tại.
        vin: Số khung cần tìm.

    Returns:
        Vehicle response hoặc None nếu không tìm thấy.
    """
    vehicle = await repo_get_vehicle_by_vin(db, vin)
    return VehicleResponse.model_validate(vehicle) if vehicle else None


async def find_active_vehicle_by_id(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleResponse | None:
    """Tìm xe chưa bị xoá theo ID để domain khác dựng response.

    Args:
        db: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.

    Returns:
        Vehicle response hoặc None nếu không tìm thấy.
    """
    vehicle = await repo_get_vehicle_by_id(db, vehicle_id)
    return VehicleResponse.model_validate(vehicle) if vehicle else None


async def create_vehicle(
    db: AsyncSession, vehicle_data: VehicleCreate
) -> VehicleResponse:
    """Create a new vehicle.

    Validates that license_plate is unique before creating.

    Args:
        db: Async database session
        vehicle_data: Vehicle creation data

    Returns:
        Created vehicle response

    Raises:
        VehicleConflictError: If the license plate or VIN already exists.
    """
    # Check if license_plate already exists
    existing_plate = await repo_get_vehicle_by_plate(db, vehicle_data.license_plate)
    if existing_plate:
        raise VehicleConflictError(
            f"Vehicle with license plate '{vehicle_data.license_plate}' already exists"
        )

    # Check if VIN already exists
    existing_vin = await repo_get_vehicle_by_vin(db, vehicle_data.vin)
    if existing_vin:
        raise VehicleConflictError(
            f"Vehicle with VIN '{vehicle_data.vin}' already exists"
        )

    # Create vehicle
    vehicle_dict = vehicle_data.model_dump()
    try:
        vehicle = await repo_create_vehicle(db, vehicle_dict)
    except IntegrityError as error:
        raise VehicleConflictError(
            "Vehicle license plate or VIN already exists"
        ) from error

    return VehicleResponse.model_validate(vehicle)


async def get_vehicle(db: AsyncSession, vehicle_id: UUID) -> VehicleResponse:
    """Get a vehicle by ID.

    Args:
        db: Async database session
        vehicle_id: Vehicle internal ID (UUID)

    Returns:
        Vehicle response

    Raises:
        VehicleNotFoundError: If the vehicle does not exist.
    """
    vehicle = await repo_get_vehicle_by_id(db, vehicle_id)
    if not vehicle:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    return VehicleResponse.model_validate(vehicle)


async def list_vehicles(
    db: AsyncSession,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: VehicleStatus | None = None,
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
        page = settings.API_DEFAULT_PAGE
    if page_size < 1:
        page_size = settings.API_DEFAULT_PAGE_SIZE
    if page_size > settings.API_MAX_PAGE_SIZE:
        page_size = settings.API_MAX_PAGE_SIZE

    skip = (page - 1) * page_size

    # Get vehicles and count
    vehicles = await repo_get_vehicles(db, skip, page_size, status_filter)
    total = await repo_count_vehicles(db, status_filter)

    return VehicleListResponse(
        items=[VehicleResponse.model_validate(v) for v in vehicles],
        total=total,
        page=page,
        page_size=page_size,
    )


async def update_vehicle(
    db: AsyncSession, vehicle_id: UUID, update_data: VehicleUpdate
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
        VehicleNotFoundError: If the vehicle does not exist.
        VehicleConflictError: If the new license plate or VIN already exists.
    """
    # Check vehicle exists
    vehicle = await repo_get_vehicle_by_id(db, vehicle_id)
    if not vehicle:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    # If updating license_plate, check uniqueness
    if update_data.license_plate and update_data.license_plate != vehicle.license_plate:
        existing = await repo_get_vehicle_by_plate(db, update_data.license_plate)
        if existing:
            raise VehicleConflictError(
                f"Vehicle with license plate '{update_data.license_plate}' already exists"
            )

    if update_data.vin and update_data.vin != vehicle.vin:
        existing = await repo_get_vehicle_by_vin(db, update_data.vin)
        if existing:
            raise VehicleConflictError(
                f"Vehicle with VIN '{update_data.vin}' already exists"
            )

    # Update vehicle
    update_dict = {
        field_name: value
        for field_name, value in update_data.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not update_dict:
        return VehicleResponse.model_validate(vehicle)

    try:
        updated_vehicle = await repo_update_vehicle(db, vehicle_id, update_dict)
    except IntegrityError as error:
        raise VehicleConflictError(
            "Vehicle license plate or VIN already exists"
        ) from error

    return VehicleResponse.model_validate(updated_vehicle)


async def delete_vehicle(db: AsyncSession, vehicle_id: UUID) -> dict[str, str]:
    """Soft delete a vehicle.

    Args:
        db: Async database session
        vehicle_id: Vehicle ID (UUID)

    Returns:
        Success message

    Raises:
        VehicleNotFoundError: If the vehicle does not exist.
    """
    vehicle = await repo_soft_delete_vehicle(db, vehicle_id)
    if not vehicle:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    return {"message": "Vehicle deleted successfully"}
