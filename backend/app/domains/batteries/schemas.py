"""Pydantic schemas for the batteries domain HTTP API."""

from datetime import date, datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.domains.batteries.types import BatteryChemistry, BatteryStatus
from app.libs.common.config import settings


class BatteryModelCreateRequest(BaseModel):
    """HTTP request data for adding a battery type to the catalog (BAT-01)."""

    manufacturer: str = Field(
        ..., min_length=1, max_length=50, description="Battery maker"
    )
    model_name: str = Field(..., min_length=1, max_length=50, description="Model name")
    chemistry: BatteryChemistry = Field(..., description="Cell chemistry")
    design_capacity_kwh: float | None = Field(
        default=None, gt=0, lt=10000, description="Energy the pack holds when new, kWh"
    )
    nominal_voltage_v: float | None = Field(
        default=None, gt=0, lt=10000, description="Nominal pack voltage, V"
    )


class BatteryModelUpdateRequest(BaseModel):
    """HTTP request data for partially updating a battery model.

    A field sent as null (or not sent) is left unchanged.
    """

    manufacturer: str | None = Field(
        default=None, min_length=1, max_length=50, description="Battery maker"
    )
    model_name: str | None = Field(
        default=None, min_length=1, max_length=50, description="Model name"
    )
    chemistry: BatteryChemistry | None = Field(
        default=None, description="Cell chemistry"
    )
    design_capacity_kwh: float | None = Field(
        default=None, gt=0, lt=10000, description="Energy the pack holds when new, kWh"
    )
    nominal_voltage_v: float | None = Field(
        default=None, gt=0, lt=10000, description="Nominal pack voltage, V"
    )


class BatteryModelResponse(BaseModel):
    """Battery model data returned via the HTTP API."""

    model_config = ConfigDict(from_attributes=True)

    battery_model_id: UUID = Field(..., description="Battery model ID (internal)")
    manufacturer: str = Field(..., description="Battery maker")
    model_name: str = Field(..., description="Model name")
    chemistry: str = Field(..., description="Cell chemistry")
    design_capacity_kwh: float | None = Field(
        default=None, description="Energy the pack holds when new, kWh"
    )
    nominal_voltage_v: float | None = Field(
        default=None, description="Nominal pack voltage, V"
    )
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class BatteryModelListResponse(BaseModel):
    """Paginated battery model list data returned via the HTTP API."""

    items: list[BatteryModelResponse] = Field(..., description="List of models")
    total: int = Field(..., ge=0, description="Total number of models")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class BatteryCreateRequest(BaseModel):
    """HTTP request data for registering a battery.

    ``organization_id`` names the owner; without it the caller's organization
    owns the pack. Fitting it to a truck is a separate call.
    """

    serial_number: str = Field(
        ..., min_length=1, max_length=50, description="Manufacturer's serial number"
    )
    battery_model_id: UUID = Field(..., description="The battery's model")
    organization_id: UUID | None = Field(
        default=None, description="Owning organization; defaults to the caller's"
    )
    acquired_at: AwareDatetime | None = Field(
        default=None,
        description="When the owner took the battery; defaults to the request time",
    )
    manufactured_on: date | None = Field(default=None, description="Manufacturing date")
    status: BatteryStatus = Field(
        default=BatteryStatus.ACTIVE, description="Status of the battery"
    )
    status_reason: str | None = Field(
        default=None, min_length=1, max_length=200, description="Why it has its status"
    )


class BatteryUpdateRequest(BaseModel):
    """HTTP request data for partially updating a battery.

    A field sent as null (or not sent) is left unchanged. Setting the status
    to ACTIVE without a reason clears the stored reason. Ownership and
    installation are separate actions.
    """

    serial_number: str | None = Field(
        default=None, min_length=1, max_length=50, description="Serial number"
    )
    battery_model_id: UUID | None = Field(default=None, description="The model")
    manufactured_on: date | None = Field(default=None, description="Manufacturing date")
    status: BatteryStatus | None = Field(default=None, description="Status")
    status_reason: str | None = Field(
        default=None, min_length=1, max_length=200, description="Why it has its status"
    )


class BatteryResponse(BaseModel):
    """Battery data returned via the HTTP API."""

    model_config = ConfigDict(from_attributes=True)

    battery_id: UUID = Field(..., description="Battery ID (internal)")
    serial_number: str = Field(..., description="Manufacturer's serial number")
    battery_model_id: UUID = Field(..., description="The battery's model")
    organization_id: UUID = Field(..., description="Owning organization")
    acquired_at: datetime = Field(..., description="When the owner took the battery")
    vehicle_id: UUID | None = Field(
        default=None, description="Truck it is fitted to now; null in stock"
    )
    installed_at: datetime | None = Field(
        default=None, description="When it was fitted to that truck"
    )
    manufactured_on: date | None = Field(default=None, description="Manufacturing date")
    status: str = Field(..., description="ACTIVE or INACTIVE")
    status_reason: str | None = Field(default=None, description="Why that status")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class BatteryListResponse(BaseModel):
    """Paginated battery list data returned via the HTTP API."""

    items: list[BatteryResponse] = Field(..., description="List of batteries")
    total: int = Field(..., ge=0, description="Total number of batteries")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class BatteryInstallRequest(BaseModel):
    """HTTP request data for fitting a battery to a truck (VH-16)."""

    vehicle_id: UUID = Field(..., description="The truck to fit the battery to")
    installed_at: AwareDatetime | None = Field(
        default=None,
        description="When it was fitted; defaults to the request time, not in the future",
    )
    reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Why (kept in the change history)",
    )


class BatteryOwnershipTransferRequest(BaseModel):
    """HTTP request data for handing a battery to another organization."""

    organization_id: UUID = Field(..., description="The organization taking the pack")
    acquired_at: AwareDatetime | None = Field(
        default=None, description="Effective date; defaults to the request time"
    )
    reason: str = Field(
        ..., min_length=1, max_length=200, description="Why it changes owner"
    )


class BatteryInstallationPeriodResponse(BaseModel):
    """One stay of a battery in a truck (from the installation view)."""

    vehicle_id: UUID = Field(..., description="The truck")
    installed_from: datetime = Field(..., description="When it was fitted")
    installed_until: datetime | None = Field(
        default=None, description="When it was removed; null while fitted"
    )


class BatteryInstallationPeriodListResponse(BaseModel):
    """Installation periods of a battery, oldest first."""

    items: list[BatteryInstallationPeriodResponse] = Field(
        ..., description="Installation periods, oldest first"
    )
