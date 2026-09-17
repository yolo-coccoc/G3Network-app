"""Pydantic schemas for the vehicles domain HTTP API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.vehicles.types import VehicleActivationStatus, VehicleStatus
from app.libs.common.config import settings


class _VehicleInputFields(BaseModel):
    """Fields shared by the create and update vehicle requests."""

    license_plate: str = Field(
        ..., min_length=1, max_length=20, description="License plate"
    )
    vin: str = Field(
        ...,
        min_length=17,
        max_length=17,
        description="VIN (Vehicle Identification Number)",
    )
    make: str = Field(..., min_length=1, max_length=50, description="Manufacturer")
    model: str = Field(..., min_length=1, max_length=50, description="Model")
    year: int = Field(..., ge=1900, le=2100, description="Manufacturing year")
    status: VehicleStatus = Field(
        default=VehicleStatus.ACTIVE, description="Vehicle status"
    )
    battery_capacity_kwh: float | None = Field(
        None,
        gt=0,
        le=1000,
        description=(
            "Nominal battery pack capacity in kWh (nullable). Per the "
            "PATCH semantics on VehicleUpdateRequest, this can be set or "
            "changed but not cleared back to null once recorded."
        ),
    )


class VehicleCreateRequest(_VehicleInputFields):
    """HTTP request data for creating a new vehicle."""

    fleet_id: str | None = Field(
        None, description="Fleet ID (nullable - may not be assigned yet)"
    )


class VehicleUpdateRequest(BaseModel):
    """HTTP request data for partially updating a vehicle."""

    license_plate: str | None = Field(
        None, min_length=1, max_length=20, description="License plate"
    )
    vin: str | None = Field(
        None, min_length=17, max_length=17, description="VIN (chassis number)"
    )
    make: str | None = Field(
        None, min_length=1, max_length=50, description="Manufacturer"
    )
    model: str | None = Field(None, min_length=1, max_length=50, description="Model")
    year: int | None = Field(None, ge=1900, le=2100, description="Manufacturing year")
    status: VehicleStatus | None = Field(None, description="Vehicle status")
    fleet_id: str | None = Field(None, description="Fleet ID")
    battery_capacity_kwh: float | None = Field(
        None, gt=0, le=1000, description="Nominal battery pack capacity in kWh"
    )


class VehicleResponse(_VehicleInputFields):
    """Vehicle data returned via the HTTP API."""

    model_config = ConfigDict(from_attributes=True)

    vehicle_id: UUID = Field(..., description="Vehicle ID (internal)")
    fleet_id: str | None = Field(None, description="Fleet ID")
    activation_status: VehicleActivationStatus = Field(
        ..., description="Progress through the F-F2 device-provisioning flow"
    )
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class VehicleListResponse(BaseModel):
    """Paginated vehicle list data returned via the HTTP API."""

    items: list[VehicleResponse] = Field(..., description="List of vehicles")
    total: int = Field(..., ge=0, description="Total number of vehicles")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class VehicleActivationSummaryResponse(BaseModel):
    """Fleet-wide F-F2 activation success rate.

    Attributes:
        attempted_count: Vehicles at `DEVICE_ASSIGNED` or `ACTIVATED` -
            provisioning was at least started.
        activated_count: Vehicles at `ACTIVATED` - provisioning fully
            confirmed via a first telemetry message.
        activation_rate_percent: `activated_count / attempted_count * 100`,
            or `None` if `attempted_count` is zero.
    """

    attempted_count: int = Field(..., ge=0)
    activated_count: int = Field(..., ge=0)
    activation_rate_percent: float | None = Field(None, ge=0, le=100)
