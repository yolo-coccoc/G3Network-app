"""Pydantic schemas for the drivers domain HTTP API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.drivers.types import DriverStatus
from app.libs.common.config import settings


class _DriverInputFields(BaseModel):
    """Fields shared by the create and response driver schemas."""

    full_name: str = Field(..., min_length=1, max_length=100, description="Full name")
    phone_number: str = Field(
        ..., min_length=1, max_length=20, description="Contact phone number"
    )
    license_number: str = Field(
        ..., min_length=1, max_length=50, description="Driving license number"
    )
    status: DriverStatus = Field(
        default=DriverStatus.ACTIVE, description="Driver status"
    )


class DriverCreateRequest(_DriverInputFields):
    """HTTP request data for creating a new driver."""


class DriverUpdateRequest(BaseModel):
    """HTTP request data for partially updating a driver."""

    full_name: str | None = Field(
        None, min_length=1, max_length=100, description="Full name"
    )
    phone_number: str | None = Field(
        None, min_length=1, max_length=20, description="Contact phone number"
    )
    license_number: str | None = Field(
        None, min_length=1, max_length=50, description="Driving license number"
    )
    status: DriverStatus | None = Field(None, description="Driver status")


class DriverResponse(_DriverInputFields):
    """Driver data returned via the HTTP API.

    Attributes:
        driver_id: Internal ID of the driver.
        current_vehicle_id: Internal ID of the vehicle currently assigned
            to this driver, or `None` if unassigned.
        current_vehicle_vin: VIN of that vehicle, enriched by the service
            (same idiom as `TelematicResponse.vehicle_vin`).
        created_at: Creation time.
        updated_at: Last update time.
    """

    model_config = ConfigDict(from_attributes=True)

    driver_id: UUID = Field(..., description="Driver ID (internal)")
    current_vehicle_id: UUID | None = Field(
        None, description="Currently assigned vehicle"
    )
    current_vehicle_vin: str | None = Field(
        None, description="VIN of the assigned vehicle"
    )
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class DriverListResponse(BaseModel):
    """Paginated driver list data returned via the HTTP API."""

    items: list[DriverResponse] = Field(..., description="List of drivers")
    total: int = Field(..., ge=0, description="Total number of drivers")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class DriverVehicleAssignRequest(BaseModel):
    """HTTP request data for assigning a vehicle to a driver."""

    vehicle_vin: str = Field(
        ..., min_length=17, max_length=17, description="VIN of the vehicle to assign"
    )


class DriverVehicleAssignmentResponse(BaseModel):
    """One driver-vehicle assignment record, open or closed.

    Attributes:
        assignment_id: Internal ID of the assignment.
        driver_id: The assigned driver.
        vehicle_id: The assigned vehicle.
        vehicle_vin: VIN of the assigned vehicle, enriched by the service.
            `None` only for a historical assignment whose vehicle has
            since been soft-deleted and no longer resolves.
        assigned_at: When this assignment began.
        unassigned_at: When this assignment ended, `None` while active.
    """

    assignment_id: UUID
    driver_id: UUID
    vehicle_id: UUID
    vehicle_vin: str | None
    assigned_at: datetime
    unassigned_at: datetime | None


class DriverAssignmentHistoryResponse(BaseModel):
    """A driver's paginated assignment history, newest first."""

    items: list[DriverVehicleAssignmentResponse] = Field(
        ..., description="Assignment history, newest first"
    )
    total: int = Field(..., ge=0, description="Total number of assignments")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )
