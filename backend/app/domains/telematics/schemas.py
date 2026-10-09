"""Pydantic schemas for the telematics domain HTTP API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domains.telematics.types import TelematicConfigPushOutcome, TelematicStatus
from app.libs.common.config import settings


class TelematicCreateRequest(BaseModel):
    """Request data to create a device; the VIN is used to resolve vehicle_id.

    Attributes:
        telematic_serial: Unique serial printed on the device.
        imei: IMEI of the device modem, if known.
        organization_id: The organization that owns the device (TX-07); an
            unknown organization is rejected (404).
        acquired_at: When the owner took the device; defaults to now.
        vehicle_vin: VIN of the vehicle to assign, if any; a VIN matching
            no live vehicle is rejected (404).
        status: Initial status.
        status_reason: Why the device starts in that status, if it needs
            explaining.
    """

    telematic_serial: str = Field(..., min_length=1, max_length=50)
    imei: str | None = Field(default=None, min_length=15, max_length=15)
    organization_id: UUID
    acquired_at: datetime | None = None
    vehicle_vin: str | None = Field(default=None, min_length=17, max_length=17)
    status: TelematicStatus = TelematicStatus.ACTIVE
    status_reason: str | None = Field(default=None, max_length=200)


class TelematicUpdateRequest(BaseModel):
    """Request data for a partial update of a device.

    A field not sent, or sent as ``null``, is left unchanged - except
    ``vehicle_vin``, where an explicit ``null`` unassigns the device.

    Attributes:
        telematic_serial: New serial.
        imei: New IMEI.
        vehicle_vin: VIN of the vehicle to (re)assign (a VIN matching no
            live vehicle is rejected with 404), or ``null`` to unassign.
        status: New status.
        status_reason: Why the status changes.
    """

    telematic_serial: str | None = Field(default=None, min_length=1, max_length=50)
    imei: str | None = Field(default=None, min_length=15, max_length=15)
    vehicle_vin: str | None = Field(default=None, min_length=17, max_length=17)
    status: TelematicStatus | None = None
    status_reason: str | None = Field(default=None, max_length=200)


class TelematicResponse(BaseModel):
    """Device information including the VIN of the currently assigned vehicle.

    Built field by field in ``service.build_telematic_response``.

    Attributes:
        telematic_id: Internal ID of the device.
        telematic_serial: Unique serial printed on the device.
        imei: IMEI of the device modem, if entered.
        organization_id: The organization that owns the device.
        acquired_at: When the current owner took the device.
        vehicle_id: Internal ID of the assigned vehicle, if any.
        vehicle_vin: VIN of the assigned vehicle, ``None`` when unassigned
            or the vehicle is soft-deleted.
        installed_at: When it was mounted on its current vehicle.
        status: Status set by a person.
        status_reason: Why the device is in that status.
        firmware_version: Firmware in the newest status report (TX-11),
            ``None`` until the device reports one.
        telemetry_interval_seconds: Publish interval in the newest status
            report, ``None`` until the device reports one.
        last_seen_at: Newest telemetry ``received_at`` of the mounted
            vehicle (F-J1); ``None`` when the device is not mounted on a
            live vehicle or the vehicle never reported.
        is_online: The newest telemetry arrived within
            ``TELEMETRY_ONLINE_THRESHOLD_SECONDS`` (planner D2); ``False``
            when not mounted or never reported.
        is_silent: The device-health monitor's silence rule holds: the
            device is ``ACTIVE``, mounted on a live vehicle, has reported at
            least once, and nothing arrived for
            ``TELEMATICS_SILENT_THRESHOLD_MINUTES`` (F-J1).
        last_signal_strength_dbm: Signal strength of the vehicle's newest
            reading in dBm; ``None`` when not mounted, never reported, or
            not reported in that reading.
        created_at: Creation time.
        updated_at: Last update time.
    """

    telematic_id: UUID
    telematic_serial: str
    imei: str | None
    organization_id: UUID
    acquired_at: datetime
    vehicle_id: UUID | None
    vehicle_vin: str | None
    installed_at: datetime | None
    status: TelematicStatus
    status_reason: str | None
    firmware_version: str | None
    telemetry_interval_seconds: int | None
    last_seen_at: datetime | None
    is_online: bool
    is_silent: bool
    last_signal_strength_dbm: int | None
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
    fail-closed contract: this response is never built if the MQTT publish
    itself failed.

    Attributes:
        telematic_id: Internal ID of the configured device.
        telematic_serial: Serial of the configured device.
        telemetry_interval_seconds: Interval that was published.
        config_pushed_at: Timestamp stamped on the command (not stored).
        command_topic: MQTT topic the command was published to.
    """

    telematic_id: UUID
    telematic_serial: str
    telemetry_interval_seconds: int
    config_pushed_at: datetime
    command_topic: str


class TelematicFleetConfigPushResult(BaseModel):
    """Outcome of a fleet-wide config push for one fleet vehicle (F-J2, D9).

    Attributes:
        vehicle_id: Internal ID of the fleet vehicle.
        telematic_id: Internal ID of its live device; ``None`` when the
            vehicle has none (or is soft-deleted, so no device is looked up).
        telematic_serial: Serial of that device, ``None`` likewise.
        outcome: ``published``, ``skipped`` or ``failed``.
        reason: Why the vehicle was skipped or the publish failed; ``None``
            when published.
    """

    vehicle_id: UUID
    telematic_id: UUID | None
    telematic_serial: str | None
    outcome: TelematicConfigPushOutcome
    reason: str | None


class TelematicFleetConfigPushResponse(BaseModel):
    """Per-vehicle report of a fleet-wide config push (F-J2, D9).

    Returned with 200 even when some vehicles were skipped or failed:
    partial failure is reported here, not raised.

    Attributes:
        fleet_id: Internal ID of the fleet.
        published_count: Vehicles whose device received the command.
        skipped_count: Vehicles for which nothing was attempted.
        failed_count: Vehicles whose publish raised.
        results: One entry per active fleet member, in membership order
            (oldest member first).
    """

    fleet_id: UUID
    published_count: int = Field(..., ge=0)
    skipped_count: int = Field(..., ge=0)
    failed_count: int = Field(..., ge=0)
    results: list[TelematicFleetConfigPushResult]


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
