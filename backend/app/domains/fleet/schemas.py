"""Pydantic schemas for the fleet domain HTTP API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.fleet.types import FleetStatus
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings


class _FleetInputFields(BaseModel):
    """Fields shared by the create and response fleet schemas."""

    fleet_code: str = Field(
        ..., min_length=1, max_length=50, description="Natural fleet code"
    )
    name: str = Field(..., min_length=1, max_length=100, description="Fleet name")
    status: FleetStatus = Field(default=FleetStatus.ACTIVE, description="Fleet status")


class FleetCreateRequest(_FleetInputFields):
    """HTTP request data for creating a new fleet."""


class FleetUpdateRequest(BaseModel):
    """HTTP request data for partially updating a fleet."""

    fleet_code: str | None = Field(
        None, min_length=1, max_length=50, description="Natural fleet code"
    )
    name: str | None = Field(
        None, min_length=1, max_length=100, description="Fleet name"
    )
    status: FleetStatus | None = Field(None, description="Fleet status")


class FleetResponse(_FleetInputFields):
    """Fleet data returned via the HTTP API.

    Attributes:
        fleet_id: Internal ID of the fleet.
        vehicle_count: Number of vehicles currently in the fleet (open
            memberships only), enriched by the service.
        created_at: Creation time.
        updated_at: Last update time.
    """

    model_config = ConfigDict(from_attributes=True)

    fleet_id: UUID = Field(..., description="Fleet ID (internal)")
    vehicle_count: int = Field(..., ge=0, description="Number of member vehicles")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class FleetListResponse(BaseModel):
    """Paginated fleet list data returned via the HTTP API."""

    items: list[FleetResponse] = Field(..., description="List of fleets")
    total: int = Field(..., ge=0, description="Total number of fleets")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class FleetVehicleAddRequest(BaseModel):
    """HTTP request data for adding a vehicle to a fleet."""

    vehicle_vin: str = Field(
        ..., min_length=17, max_length=17, description="VIN of the vehicle to add"
    )


class FleetVehicleResponse(BaseModel):
    """One vehicle in a fleet's current member list (F-E1).

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        vin: VIN of the vehicle; ``None`` when the vehicle was soft-deleted
            after joining (the membership is still open and still counted
            in ``total``).
        license_plate: License plate of the vehicle; ``None`` in the same
            case.
        status: Vehicle lifecycle status - the only status this backend
            can honestly report; no online/offline signal exists yet.
            ``None`` in the same case.
        joined_at: When this vehicle joined the fleet.
    """

    vehicle_id: UUID
    vin: str | None
    license_plate: str | None
    status: VehicleStatus | None
    joined_at: datetime


class FleetVehicleListResponse(BaseModel):
    """A fleet's paginated current vehicle list (F-E1)."""

    items: list[FleetVehicleResponse] = Field(
        ..., description="Vehicles currently in the fleet"
    )
    total: int = Field(..., ge=0, description="Total number of member vehicles")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class FleetMembershipResponse(BaseModel):
    """One fleet-vehicle membership record, open or closed.

    Attributes:
        membership_id: Internal ID of the membership.
        fleet_id: The owning fleet.
        vehicle_id: The member vehicle.
        vehicle_vin: VIN of the member vehicle, enriched by the service.
            `None` only for a historical membership whose vehicle has
            since been soft-deleted and no longer resolves.
        joined_at: When this membership began.
        left_at: When this membership ended, `None` while active.
    """

    membership_id: UUID
    fleet_id: UUID
    vehicle_id: UUID
    vehicle_vin: str | None
    joined_at: datetime
    left_at: datetime | None


class FleetMembershipHistoryResponse(BaseModel):
    """A fleet's paginated membership history, newest first."""

    items: list[FleetMembershipResponse] = Field(
        ..., description="Membership history, newest first"
    )
    total: int = Field(..., ge=0, description="Total number of memberships")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )
