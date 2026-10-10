"""Pydantic schemas for the warranties domain HTTP API."""

from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.warranties.types import WarrantyType
from app.libs.common.config import settings
from app.libs.common.reason import Reason


class WarrantyCreateRequest(BaseModel):
    """HTTP request data for entering a warranty of one object.

    Exactly one of ``vehicle_id``, ``battery_id``, ``telematic_id`` and
    ``station_id`` names the covered object; that choice decides which
    ``limits`` keys are allowed.
    """

    vehicle_id: UUID | None = Field(default=None, description="The truck covered")
    battery_id: UUID | None = Field(default=None, description="The battery covered")
    telematic_id: UUID | None = Field(default=None, description="The T-Box covered")
    station_id: UUID | None = Field(default=None, description="The charger covered")
    warranty_type: WarrantyType = Field(
        default=WarrantyType.STANDARD, description="STANDARD or EXTENDED"
    )
    contract_reference: str | None = Field(
        default=None, min_length=1, max_length=100, description="Contract number"
    )
    starts_on: date = Field(..., description="First day of coverage")
    ends_on: date = Field(..., description="Last day of coverage")
    limits: dict[str, float] | None = Field(
        default=None,
        description=(
            "Counter readings at which coverage ends; keys depend on the "
            "covered object (truck distance_km; battery "
            "energy_throughput_kwh, charge_cycles; T-Box operating_hours, "
            "message_count; charger energy_delivered_kwh, session_count)"
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_object(self) -> Self:
        """Require exactly one covered object.

        Returns:
            The validated request.

        Raises:
            ValueError: Zero or several objects are named.
        """
        named = [
            link
            for link in (
                self.vehicle_id,
                self.battery_id,
                self.telematic_id,
                self.station_id,
            )
            if link is not None
        ]
        if len(named) != 1:
            raise ValueError(
                "Name exactly one of vehicle_id, battery_id, telematic_id and "
                "station_id"
            )
        return self


class WarrantyUpdateRequest(BaseModel):
    """HTTP request data for partially updating a warranty.

    A field sent as null (or not sent) is left unchanged; ``limits`` replaces
    the whole object when sent. The covered object cannot change, and a
    VOIDED warranty cannot be edited.
    """

    warranty_type: WarrantyType | None = Field(
        default=None, description="STANDARD or EXTENDED"
    )
    contract_reference: str | None = Field(
        default=None, min_length=1, max_length=100, description="Contract number"
    )
    starts_on: date | None = Field(default=None, description="First day of coverage")
    ends_on: date | None = Field(default=None, description="Last day of coverage")
    limits: dict[str, float] | None = Field(
        default=None, description="Counter readings at which coverage ends"
    )


class WarrantyVoidRequest(BaseModel):
    """HTTP request data for voiding a warranty (VOIDED, with the reason)."""

    reason: Reason = Field(..., description="Why the warranty is voided")


class WarrantyResponse(BaseModel):
    """Warranty data returned via the HTTP API."""

    model_config = ConfigDict(from_attributes=True)

    warranty_id: UUID = Field(..., description="Warranty ID (internal)")
    vehicle_id: UUID | None = Field(default=None, description="The truck covered")
    battery_id: UUID | None = Field(default=None, description="The battery covered")
    telematic_id: UUID | None = Field(default=None, description="The T-Box covered")
    station_id: UUID | None = Field(default=None, description="The charger covered")
    warranty_type: str = Field(..., description="STANDARD or EXTENDED")
    contract_reference: str | None = Field(default=None, description="Contract number")
    starts_on: date = Field(..., description="First day of coverage")
    ends_on: date = Field(..., description="Last day of coverage")
    limits: dict[str, float] | None = Field(
        default=None, description="Counter readings at which coverage ends"
    )
    status: str = Field(..., description="ACTIVE or VOIDED")
    status_reason: str | None = Field(default=None, description="Why it was voided")
    is_expired: bool = Field(
        default=False,
        description=(
            "Computed: ends_on has passed (report calendar day). Counter "
            "limits are not evaluated"
        ),
    )
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class WarrantyListResponse(BaseModel):
    """Paginated warranty list data returned via the HTTP API."""

    items: list[WarrantyResponse] = Field(..., description="List of warranties")
    total: int = Field(..., ge=0, description="Total number of warranties")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )
