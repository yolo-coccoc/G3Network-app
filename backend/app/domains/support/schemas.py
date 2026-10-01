"""Pydantic schemas for the support domain HTTP API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.support.types import (
    SupportCaseCategory,
    SupportCaseChannel,
    SupportCaseStatus,
    SupportCaseType,
)
from app.libs.common.config import settings


class SupportTicketCreateRequest(BaseModel):
    """HTTP request data for creating an in-app support ticket (F-I1)."""

    vehicle_vin: str | None = Field(
        None, min_length=17, max_length=17, description="VIN of the vehicle context"
    )
    driver_id: UUID | None = Field(None, description="Driver raising the ticket")
    category: SupportCaseCategory = Field(..., description="Case category")
    channel: SupportCaseChannel = Field(
        default=SupportCaseChannel.IN_APP, description="Where the ticket originated"
    )
    subject: str = Field(..., min_length=1, max_length=200, description="Short subject")
    description: str | None = Field(None, description="Free-text details")
    error_code: str | None = Field(
        None, max_length=50, description="Active device/vehicle error code, if any"
    )
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    @model_validator(mode="after")
    def _require_coordinates_together(self) -> "SupportTicketCreateRequest":
        """Require latitude/longitude together, since one alone isn't a location.

        Returns:
            The validated request.

        Raises:
            ValueError: If exactly one of latitude/longitude is provided.
        """
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class SupportSosCreateRequest(BaseModel):
    """HTTP request data for an SOS report (F-I2).

    Unlike a ticket, there is no `subject` (a button tap has no free-text
    subject - the service fills one in) and location is required, not
    optional, matching F-I2's stated input ("current location, active
    error code").
    """

    vehicle_vin: str | None = Field(
        None, min_length=17, max_length=17, description="VIN of the vehicle context"
    )
    driver_id: UUID | None = Field(None, description="Driver raising the SOS")
    category: SupportCaseCategory = Field(
        default=SupportCaseCategory.BREAKDOWN, description="Case category"
    )
    description: str | None = Field(None, description="Free-text details")
    error_code: str | None = Field(
        None, max_length=50, description="Active device/vehicle error code, if any"
    )
    latitude: float = Field(..., ge=-90, le=90, description="Current GPS latitude")
    longitude: float = Field(..., ge=-180, le=180, description="Current GPS longitude")


class SupportCaseUpdateRequest(BaseModel):
    """HTTP request data for partially updating a support case."""

    status: SupportCaseStatus | None = Field(None, description="New lifecycle status")
    category: SupportCaseCategory | None = Field(None, description="Case category")
    subject: str | None = Field(
        None, min_length=1, max_length=200, description="Short subject"
    )
    description: str | None = Field(None, description="Free-text details")


class SupportCaseResponse(BaseModel):
    """Support case data returned via the HTTP API.

    Attributes:
        case_id: Internal ID of the support case.
        case_type: Whether this is a ticket or an SOS.
        category: Business categorization of the case.
        channel: Where the case originated.
        status: Current lifecycle status.
        vehicle_id: Internal ID of the resolved vehicle, if any.
        vehicle_vin: VIN snapshot recorded at case-creation time.
        driver_id: Internal ID of the resolved driver, if any.
        driver_name: Full name of the driver, enriched by the service.
        error_code: Active error code recorded at case-creation time.
        latitude: GPS latitude recorded at case-creation time.
        longitude: GPS longitude recorded at case-creation time.
        subject: Short subject line.
        description: Free-text details.
        sla_response_minutes: The response-SLA minutes value that applied
            to this case at creation time.
        response_due_at: Deadline for a first response.
        first_responded_at: When the case first left OPEN, if it has.
        resolved_at: When the case reached RESOLVED, if it has.
        closed_at: When the case reached CLOSED, if it has.
        is_sla_breached: Whether the first response missed the deadline, or
            the deadline has passed without one (a cancelled case is judged
            at its cancellation time). Computed, never stored.
        created_at: Creation time.
        updated_at: Last update time.
    """

    model_config = ConfigDict(from_attributes=True)

    case_id: UUID
    case_type: SupportCaseType
    category: SupportCaseCategory
    channel: SupportCaseChannel
    status: SupportCaseStatus
    vehicle_id: UUID | None
    vehicle_vin: str | None
    driver_id: UUID | None
    driver_name: str | None
    error_code: str | None
    latitude: float | None
    longitude: float | None
    subject: str | None
    description: str | None
    sla_response_minutes: int
    response_due_at: datetime
    first_responded_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    is_sla_breached: bool
    created_at: datetime
    updated_at: datetime


class SupportCaseListResponse(BaseModel):
    """Paginated support case list data returned via the HTTP API."""

    items: list[SupportCaseResponse] = Field(..., description="List of support cases")
    total: int = Field(..., ge=0, description="Total number of support cases")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )
