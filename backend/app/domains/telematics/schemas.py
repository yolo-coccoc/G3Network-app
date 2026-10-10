"""Pydantic schemas of the telematics domain: the HTTP API and the status message.

Two separate contracts live here and never share a class: the HTTP request and
response schemas, and ``TelematicStatusMessage`` (the T-Box status report the
MQTT consumer validates, mqtt-spec.md 2.2).
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.domains.telematics.types import (
    TelematicConfigPushOutcome,
    TelematicHealthState,
    TelematicStatus,
)
from app.libs.common.config import settings


class TelematicCreateRequest(BaseModel):
    """Request data to create a device; the VIN is used to resolve vehicle_id.

    Attributes:
        telematic_serial: Unique serial printed on the device.
        imei: IMEI of the device modem, if known.
        organization_id: The organization that owns the device (TX-07);
            internal staff only, defaults to the caller's organization; an
            unknown or out-of-reach organization is rejected (404).
        acquired_at: When the owner took the device; defaults to now.
        vehicle_vin: VIN of the vehicle to assign, if any; a VIN matching
            no live vehicle is rejected (404).
        status: Initial status.
        status_reason: Why the device starts in that status, if it needs
            explaining.
    """

    telematic_serial: str = Field(..., min_length=1, max_length=50)
    imei: str | None = Field(default=None, min_length=15, max_length=15)
    organization_id: UUID | None = None
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


class TelematicStatusReportResponse(BaseModel):
    """One health report a device sent about itself (TX-10).

    Attributes:
        telematic_status_report_id: ID of the report.
        telematic_id: Device that sent it.
        firmware_version: Firmware version reported.
        telemetry_interval_seconds: Publish interval the device said it uses.
        sim_iccid: ICCID of the SIM in the device.
        is_esim: Whether the SIM is an eSIM.
        sim_data_status: Mobile data status.
        supply_voltage_v: Power supply voltage at the device, V.
        signal_dbm: Mobile signal strength, dBm.
        storage_used_percent: Share of the device storage in use, 0-100.
        gnss_status: Satellite positioning status.
        reported_at: When the device produced the report (UTC).
        received_at: When the backend received it (UTC).
    """

    model_config = ConfigDict(from_attributes=True)

    telematic_status_report_id: int
    telematic_id: UUID
    firmware_version: str | None
    telemetry_interval_seconds: int | None
    sim_iccid: str | None
    is_esim: bool | None
    sim_data_status: str | None
    supply_voltage_v: float | None
    signal_dbm: int | None
    storage_used_percent: float | None
    gnss_status: str | None
    reported_at: datetime
    received_at: datetime


class TelematicStatusReportListResponse(BaseModel):
    """The status reports of one device, newest first (the DEV-04 trend).

    Attributes:
        items: Reports, newest ``reported_at`` first.
        count: Number of reports returned.
    """

    items: list[TelematicStatusReportResponse]
    count: int = Field(..., ge=0)


class TelematicHealthResponse(BaseModel):
    """Health of one device for the dashboard (DEV-04), computed at read time.

    Attributes:
        telematic_id: Internal ID of the device.
        telematic_serial: Serial printed on the device.
        organization_id: The organization that owns the device.
        status: Status set by a person.
        vehicle_id: Truck it is mounted on, if any.
        vehicle_vin: VIN of that truck, ``None`` when not mounted or the truck
            is deleted.
        health_state: One-word health (see ``TelematicHealthState``).
        last_seen_at: Newest telemetry time of the truck, if it ever reported.
        is_online: The truck reported within the online threshold.
        is_silent: The silence rule holds.
        latest_report: The newest status report (firmware, SIM, power,
            signal, storage, GNSS), ``None`` until the device sends one.
    """

    telematic_id: UUID
    telematic_serial: str
    organization_id: UUID
    status: TelematicStatus
    vehicle_id: UUID | None
    vehicle_vin: str | None
    health_state: TelematicHealthState
    last_seen_at: datetime | None
    is_online: bool
    is_silent: bool
    latest_report: TelematicStatusReportResponse | None


class TelematicHealthListResponse(BaseModel):
    """A page of device health entries.

    Attributes:
        items: Devices on this page.
        total: Devices matching the filters across all pages.
        page: Normalized page number.
        page_size: Normalized page size.
    """

    items: list[TelematicHealthResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class TelematicHealthSummaryResponse(BaseModel):
    """Share of healthy devices over a scope (DEV-04).

    Attributes:
        total_count: Devices in the scope (not deleted).
        healthy_count: Devices in state ``HEALTHY``.
        attention_count: Devices in state ``ATTENTION``.
        silent_count: Devices in state ``SILENT``.
        no_data_count: Devices in state ``NO_DATA``.
        not_mounted_count: Devices in state ``NOT_MOUNTED``.
        inactive_count: Devices in state ``INACTIVE``.
        online_count: Devices whose truck reported within the online threshold.
        healthy_percent: ``healthy_count`` as a share of the devices that
            should be reporting (mounted and in service: healthy, attention,
            silent and no-data), rounded to one decimal; ``None`` when there
            are none.
    """

    total_count: int = Field(..., ge=0)
    healthy_count: int = Field(..., ge=0)
    attention_count: int = Field(..., ge=0)
    silent_count: int = Field(..., ge=0)
    no_data_count: int = Field(..., ge=0)
    not_mounted_count: int = Field(..., ge=0)
    inactive_count: int = Field(..., ge=0)
    online_count: int = Field(..., ge=0)
    healthy_percent: float | None


def _coerce_number(
    minimum: float, maximum: float, *, as_int: bool
) -> Callable[[object], int | float | None]:
    """Build a coercer that turns anything not a number in range into `None`.

    The status fields are provisional (the vendor has not confirmed them), so
    a value of the wrong type or out of range must never fail the whole
    report: it is ignored and the rest of the report is still stored.

    Args:
        minimum: Lowest accepted value.
        maximum: Highest accepted value.
        as_int: Round to an integer when `True`; otherwise keep two decimals.

    Returns:
        A function for a ``BeforeValidator``.
    """

    def coerce(value: object) -> int | float | None:
        """Return the value as a number in range, else `None`."""
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or not minimum <= number <= maximum:
            return None
        return int(round(number)) if as_int else round(number, 2)

    return coerce


def _coerce_text(
    max_length: int, *, upper: bool = False
) -> Callable[[object], str | None]:
    """Build a coercer that keeps a non-empty string, cut to the column width.

    Args:
        max_length: Width of the target column.
        upper: Upper-case the text (status words), when `True`.

    Returns:
        A function for a ``BeforeValidator``.
    """

    def coerce(value: object) -> str | None:
        """Return the cleaned string, else `None`."""
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        return (text.upper() if upper else text)[:max_length]

    return coerce


def _coerce_bool(value: object) -> bool | None:
    """Return a boolean or its ``true`` / ``false`` text, else `None`."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _coerce_timestamp(value: object) -> datetime | None:
    """Return a timezone-aware ISO 8601 time, else `None` (the caller uses the receive time)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _coerce_sim(value: object) -> object:
    """Pass a ``sim`` object through; anything else is ignored."""
    return value if isinstance(value, dict) else None


class TelematicStatusSimPayload(BaseModel):
    """The ``sim`` object of a status message (provisional, mqtt-spec.md 2.2).

    Unknown keys are ignored and a value of the wrong type becomes `None`.

    Attributes:
        iccid: ICCID of the SIM.
        is_esim: The SIM is an eSIM.
        data_status: Mobile data status (``ACTIVE``, ``NO_DATA``, ...).
    """

    model_config = ConfigDict(extra="ignore")

    iccid: Annotated[str | None, BeforeValidator(_coerce_text(22))] = None
    is_esim: Annotated[bool | None, BeforeValidator(_coerce_bool)] = None
    data_status: Annotated[
        str | None, BeforeValidator(_coerce_text(20, upper=True))
    ] = None


class TelematicStatusMessage(BaseModel):
    """A T-Box status message, as far as it carries health fields (TX-09, TX-10).

    The vendor has not confirmed the format (mqtt-spec.md 2.2), so every field
    is optional and tolerant: unknown keys are ignored, a value of the wrong
    type or out of range becomes `None`, and a message with none of the health
    fields (a plain ``online`` / ``offline`` notice) is not a report.

    Attributes:
        status: Connection word (``online`` / ``offline``), not stored.
        firmware_version: Firmware version.
        telemetry_interval_seconds: Publish interval the device uses.
        sim: SIM details.
        supply_voltage_v: Supply voltage in volts.
        signal_dbm: Mobile signal strength in dBm.
        storage_used_percent: Device storage in use, 0-100.
        gnss_status: Satellite positioning status.
        timestamp: When the device made the report; absent or invalid means
            the receive time is used.
    """

    model_config = ConfigDict(extra="ignore")

    status: Annotated[str | None, BeforeValidator(_coerce_text(20))] = None
    firmware_version: Annotated[str | None, BeforeValidator(_coerce_text(50))] = None
    telemetry_interval_seconds: Annotated[
        int | None, BeforeValidator(_coerce_number(1, 2_147_483_647, as_int=True))
    ] = None
    sim: Annotated[TelematicStatusSimPayload | None, BeforeValidator(_coerce_sim)] = (
        None
    )
    supply_voltage_v: Annotated[
        float | None, BeforeValidator(_coerce_number(0, 999.99, as_int=False))
    ] = None
    signal_dbm: Annotated[
        int | None, BeforeValidator(_coerce_number(-32768, 32767, as_int=True))
    ] = None
    storage_used_percent: Annotated[
        float | None, BeforeValidator(_coerce_number(0, 100, as_int=False))
    ] = None
    gnss_status: Annotated[
        str | None, BeforeValidator(_coerce_text(20, upper=True))
    ] = None
    timestamp: Annotated[datetime | None, BeforeValidator(_coerce_timestamp)] = None

    def has_health_fields(self) -> bool:
        """Tell whether the message carries at least one health field.

        Returns:
            `True` if any stored field is present; a bare ``{"status":
            "offline"}`` notice (the Last Will) returns `False`.
        """
        sim = self.sim
        return any(
            value is not None
            for value in (
                self.firmware_version,
                self.telemetry_interval_seconds,
                self.supply_voltage_v,
                self.signal_dbm,
                self.storage_used_percent,
                self.gnss_status,
                sim.iccid if sim else None,
                sim.is_esim if sim else None,
                sim.data_status if sim else None,
            )
        )

    def to_status_report_values(
        self, telematic_id: UUID, received_at: datetime
    ) -> dict[str, object]:
        """Convert the message into the values of one ``telematic_status_reports`` row.

        Args:
            telematic_id: The device that sent it (looked up from the topic's
                serial).
            received_at: When the backend received the message.

        Returns:
            Column values; ``reported_at`` is the device's timestamp or, when
            it sent none, ``received_at``.
        """
        sim = self.sim
        return {
            "telematic_id": telematic_id,
            "firmware_version": self.firmware_version,
            "telemetry_interval_seconds": self.telemetry_interval_seconds,
            "sim_iccid": sim.iccid if sim else None,
            "is_esim": sim.is_esim if sim else None,
            "sim_data_status": sim.data_status if sim else None,
            "supply_voltage_v": (
                Decimal(str(self.supply_voltage_v))
                if self.supply_voltage_v is not None
                else None
            ),
            "signal_dbm": self.signal_dbm,
            "storage_used_percent": (
                Decimal(str(self.storage_used_percent))
                if self.storage_used_percent is not None
                else None
            ),
            "gnss_status": self.gnss_status,
            "reported_at": self.timestamp or received_at,
            "received_at": received_at,
        }


@dataclass(frozen=True)
class TelematicStatusEnvelope:
    """A validated status message paired with the serial from its topic.

    The payload carries no serial (the topic does), so the consumer passes
    both to the worker together.

    Attributes:
        telematic_serial: Serial taken from the topic
            ``g3network/telematics/{serial}/status``.
        message: The tolerant message.
    """

    telematic_serial: str
    message: TelematicStatusMessage
