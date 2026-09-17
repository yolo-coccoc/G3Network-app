"""Pydantic schemas for the telematics domain HTTP API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.telematics.types import TelematicStatus
from app.libs.common.config import settings


class TelematicCreateRequest(BaseModel):
    """Request data to create a device; VIN is used to resolve vehicle_id."""

    telematic_serial: str = Field(..., min_length=1, max_length=50)
    vehicle_vin: str | None = Field(None, min_length=17, max_length=17)
    status: TelematicStatus = TelematicStatus.ACTIVE
    firmware_version: str | None = Field(None, max_length=50)


class TelematicUpdateRequest(BaseModel):
    """Request data for a partial update of a device."""

    telematic_serial: str | None = Field(None, min_length=1, max_length=50)
    vehicle_vin: str | None = Field(None, min_length=17, max_length=17)
    status: TelematicStatus | None = None
    firmware_version: str | None = Field(None, max_length=50)


class TelematicResponse(BaseModel):
    """Device information including the VIN of the currently assigned vehicle."""

    model_config = ConfigDict(from_attributes=True)

    telematic_id: UUID
    telematic_serial: str
    vehicle_id: UUID | None
    vehicle_vin: str | None
    status: TelematicStatus
    firmware_version: str | None
    telemetry_interval_seconds: int | None
    config_pushed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TelematicConfigPushRequest(BaseModel):
    """Desired telemetry publish interval to push to a device over MQTT (F-J2)."""

    telemetry_interval_seconds: int = Field(
        ...,
        ge=settings.TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS,
        le=settings.TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS,
    )


class TelematicConfigResponse(BaseModel):
    """Outcome of one successful configuration push (F-J2).

    Returned only on success - see ``service.push_telematic_config``'s
    fail-closed contract: nothing is persisted and this response is never
    built if the MQTT publish itself failed.
    """

    telematic_id: UUID
    telematic_serial: str
    telemetry_interval_seconds: int
    config_pushed_at: datetime
    command_topic: str


class TelematicListResponse(BaseModel):
    """Paginated list of devices."""

    items: list[TelematicResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)
