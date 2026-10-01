"""Pydantic schemas for the telematics domain HTTP API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domains.telematics.types import TelematicStatus
from app.libs.common.config import settings


class TelematicCreateRequest(BaseModel):
    """Request data to create a device; the VIN is used to resolve vehicle_id.

    Attributes:
        telematic_serial: Unique serial printed on the device.
        vehicle_vin: VIN of the vehicle to assign; a VIN matching no live
            vehicle leaves the device unassigned.
        status: Initial operating status.
        firmware_version: Current firmware version, if known.
    """

    telematic_serial: str = Field(..., min_length=1, max_length=50)
    vehicle_vin: str | None = Field(None, min_length=17, max_length=17)
    status: TelematicStatus = TelematicStatus.ACTIVE
    firmware_version: str | None = Field(None, max_length=50)


class TelematicUpdateRequest(BaseModel):
    """Request data for a partial update of a device.

    A field not sent, or sent as ``null``, is left unchanged - except
    ``vehicle_vin``, where ``null`` unassigns the device.

    Attributes:
        telematic_serial: New serial.
        vehicle_vin: VIN of the vehicle to (re)assign, or ``null`` to unassign.
        status: New operating status.
        firmware_version: New firmware version.
    """

    telematic_serial: str | None = Field(None, min_length=1, max_length=50)
    vehicle_vin: str | None = Field(None, min_length=17, max_length=17)
    status: TelematicStatus | None = None
    firmware_version: str | None = Field(None, max_length=50)


class TelematicResponse(BaseModel):
    """Device information including the VIN of the currently assigned vehicle.

    Built field by field in ``service.build_telematic_response``.

    Attributes:
        telematic_id: Internal ID of the device.
        telematic_serial: Unique serial printed on the device.
        vehicle_id: Internal ID of the assigned vehicle, if any.
        vehicle_vin: VIN of the assigned vehicle, ``None`` when unassigned
            or the vehicle is soft-deleted.
        status: Operating status.
        firmware_version: Current firmware version.
        telemetry_interval_seconds: Interval last pushed over MQTT (F-J2).
        config_pushed_at: When that interval was pushed.
        created_at: Creation time.
        updated_at: Last update time.
    """

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
    """Desired telemetry publish interval to push to a device over MQTT (F-J2).

    Attributes:
        telemetry_interval_seconds: Interval in seconds, within the
            ``TELEMATICS_MIN/MAX_TELEMETRY_INTERVAL_SECONDS`` bounds.
    """

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

    Attributes:
        telematic_id: Internal ID of the configured device.
        telematic_serial: Serial of the configured device.
        telemetry_interval_seconds: Interval that was published.
        config_pushed_at: Timestamp stamped on the command and stored.
        command_topic: MQTT topic the command was published to.
    """

    telematic_id: UUID
    telematic_serial: str
    telemetry_interval_seconds: int
    config_pushed_at: datetime
    command_topic: str


class TelematicListResponse(BaseModel):
    """Paginated list of devices.

    Attributes:
        items: Devices on this page.
        total: Number of matching devices across all pages.
        page: Normalized page number.
        page_size: Normalized page size.
    """

    items: list[TelematicResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)
