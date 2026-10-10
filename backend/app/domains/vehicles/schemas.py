"""Pydantic schemas for the vehicles domain HTTP API."""

from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
from app.libs.common.reason import Reason


class VehicleCreateRequest(BaseModel):
    """HTTP request data for creating a new vehicle.

    ``organization_id`` is only for internal staff creating a truck for a
    customer; everyone else owns the truck through their own organization.
    """

    organization_id: UUID | None = Field(
        default=None,
        description=(
            "Organization that owns the truck; internal staff only, defaults "
            "to the caller's organization"
        ),
    )
    acquired_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "When the owner took the truck (handover date); defaults to the "
            "time of the request. Must carry a timezone."
        ),
    )
    license_plate: str = Field(
        ..., min_length=1, max_length=20, description="License plate"
    )
    vin: str = Field(
        ...,
        min_length=17,
        max_length=17,
        description="VIN (Vehicle Identification Number)",
    )
    vehicle_model_id: UUID = Field(
        ..., description="The truck's model, from the vehicle model catalog"
    )
    year: int = Field(..., ge=1900, le=2100, description="Manufacturing year")
    status: VehicleStatus = Field(
        default=VehicleStatus.ACTIVE, description="Service status"
    )
    status_reason: Reason | None = Field(
        default=None, description="Why the vehicle has its status"
    )


class VehicleUpdateRequest(BaseModel):
    """HTTP request data for partially updating a vehicle.

    Ownership transfer is not an edit: it is a separate action (VH-12).
    """

    license_plate: str | None = Field(
        default=None, min_length=1, max_length=20, description="License plate"
    )
    vin: str | None = Field(
        default=None, min_length=17, max_length=17, description="VIN (chassis number)"
    )
    vehicle_model_id: UUID | None = Field(default=None, description="The truck's model")
    year: int | None = Field(
        default=None, ge=1900, le=2100, description="Manufacturing year"
    )
    status: VehicleStatus | None = Field(default=None, description="Service status")
    status_reason: Reason | None = Field(
        default=None, description="Why the vehicle has its status"
    )


class VehicleOwnershipTransferRequest(BaseModel):
    """HTTP request data for transferring a truck to a new owner (VH-12)."""

    organization_id: UUID = Field(..., description="The organization taking the truck")
    acquired_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "Effective date of the transfer (handover to the buyer); defaults "
            "to the time of the request. Not in the future, and after the date "
            "the current owner took the truck. Must carry a timezone."
        ),
    )
    reason: Reason = Field(
        ..., description="Why the truck changes owner (kept in the change history)"
    )


class VehicleOwnershipPeriodResponse(BaseModel):
    """One period during which an organization owned a truck (VH-10)."""

    organization_id: UUID = Field(..., description="The owning organization")
    owned_from: datetime = Field(..., description="When it took the truck")
    owned_until: datetime | None = Field(
        default=None, description="When the next owner took it; null for the owner now"
    )


class VehicleOwnershipPeriodListResponse(BaseModel):
    """Ownership periods of a truck, oldest first."""

    items: list[VehicleOwnershipPeriodResponse] = Field(
        ..., description="Ownership periods, oldest first"
    )


class VehicleResponse(BaseModel):
    """Vehicle data returned via the HTTP API."""

    model_config = ConfigDict(from_attributes=True)

    vehicle_id: UUID = Field(..., description="Vehicle ID (internal)")
    organization_id: UUID = Field(..., description="Owning organization")
    acquired_at: datetime = Field(..., description="When the owner took the truck")
    license_plate: str = Field(..., description="License plate")
    vin: str = Field(..., description="VIN (chassis number)")
    vehicle_model_id: UUID = Field(..., description="The truck's model")
    year: int = Field(..., description="Manufacturing year")
    status: VehicleStatus = Field(..., description="Service status")
    status_reason: str | None = Field(
        default=None, description="Why the vehicle has its status"
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


class ConsumptionCurvePoint(BaseModel):
    """One point of a model's reference energy consumption by load."""

    load_percent: float = Field(..., ge=0, le=100, description="Load, % of max payload")
    kwh_per_km: float = Field(..., gt=0, description="Energy use at that load")


class VehicleModelCreateRequest(BaseModel):
    """HTTP request data for adding a truck model to the catalog (VH-15)."""

    make: str = Field(..., min_length=1, max_length=50, description="Manufacturer")
    model_name: str = Field(..., min_length=1, max_length=50, description="Model line")
    gross_vehicle_weight_kg: int | None = Field(
        default=None, gt=0, description="GVW in kg"
    )
    max_payload_kg: int | None = Field(
        default=None, gt=0, description="Maximum payload in kg"
    )
    nominal_battery_capacity_kwh: float | None = Field(
        default=None,
        gt=0,
        lt=10000,
        description="Battery capacity the model is delivered with, in kWh",
    )
    consumption_curve: list[ConsumptionCurvePoint] | None = Field(
        default=None, description="Reference energy consumption by load"
    )


class VehicleModelUpdateRequest(BaseModel):
    """HTTP request data for partially updating a catalog model (VH-15).

    A field sent as null (or not sent) is left unchanged.
    """

    make: str | None = Field(
        default=None, min_length=1, max_length=50, description="Manufacturer"
    )
    model_name: str | None = Field(
        default=None, min_length=1, max_length=50, description="Model line"
    )
    gross_vehicle_weight_kg: int | None = Field(
        default=None, gt=0, description="GVW in kg"
    )
    max_payload_kg: int | None = Field(
        default=None, gt=0, description="Maximum payload in kg"
    )
    nominal_battery_capacity_kwh: float | None = Field(
        default=None,
        gt=0,
        lt=10000,
        description="Battery capacity the model is delivered with, in kWh",
    )
    consumption_curve: list[ConsumptionCurvePoint] | None = Field(
        default=None, description="Reference energy consumption by load"
    )


class VehicleModelResponse(BaseModel):
    """Vehicle model data returned via the HTTP API."""

    model_config = ConfigDict(from_attributes=True)

    vehicle_model_id: UUID = Field(..., description="Vehicle model ID (internal)")
    make: str = Field(..., description="Manufacturer")
    model_name: str = Field(..., description="Model line")
    gross_vehicle_weight_kg: int | None = Field(default=None, description="GVW in kg")
    max_payload_kg: int | None = Field(
        default=None, description="Maximum payload in kg"
    )
    nominal_battery_capacity_kwh: float | None = Field(
        default=None, description="Battery capacity the model is delivered with, in kWh"
    )
    consumption_curve: list[ConsumptionCurvePoint] | None = Field(
        default=None, description="Reference energy consumption by load"
    )
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class VehicleModelListResponse(BaseModel):
    """Paginated vehicle model list data returned via the HTTP API."""

    items: list[VehicleModelResponse] = Field(..., description="List of models")
    total: int = Field(..., ge=0, description="Total number of models")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )
