"""Pure mappings from telemetry ORM rows and MQTT messages to other shapes.

Feature code: F-A1 (latest telemetry and the live-status DTO), F-A5
(history points), F-A4 (the anomaly notification's data snapshot), F-E1
(a fleet member's live-status row).

Internal to the telemetry domain: other domains never import this module
(they go through ``telemetry/service.py``). Every function here is a pure
mapping - no database, no other service, no commit.

The HTTP responses are built field by field rather than with
``model_validate(row, from_attributes=True)`` because the ORM model stores
GPS as a single ``location`` geography point while the responses expose
plain ``latitude``/``longitude`` fields - the two don't line up 1:1 by
attribute name.
"""

from typing import Any
from uuid import UUID

from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.schemas import (
    FleetVehicleLiveStatusResponse,
    TelemetryMessage,
    VehicleTelemetryHistoryPoint,
    VehicleTelemetryLatestResponse,
)
from app.domains.telemetry.types import VehicleLiveStatusReference
from app.domains.vehicles.types import VehicleSummary
from app.libs.common.geo import location_to_coordinates


def to_coordinates(telemetry: VehicleTelemetryModel) -> tuple[float, float]:
    """Decode a telemetry row's NOT NULL ``location`` into latitude/longitude.

    Args:
        telemetry: Telemetry ORM row queried by the repository.

    Returns:
        ``(latitude, longitude)`` in decimal degrees.
    """
    latitude, longitude = location_to_coordinates(telemetry.location)
    # location_to_coordinates()'s return type is generic (Optional, since
    # charging_stations.location can be null) - vehicle_telemetry.location
    # is NOT NULL, so this pair is never actually missing; the assertion
    # documents that invariant for both mypy and a future reader.
    assert latitude is not None and longitude is not None, (
        "vehicle_telemetry.location is NOT NULL"
    )
    return latitude, longitude


def _to_reading_fields(telemetry: VehicleTelemetryModel) -> dict[str, Any]:
    """Collect the per-reading fields shared by the latest and history responses.

    Args:
        telemetry: Telemetry ORM row queried by the repository.

    Returns:
        Keyword arguments for every field ``VehicleTelemetryHistoryPoint``
        and ``VehicleTelemetryLatestResponse`` have in common, with
        latitude/longitude decoded from ``location``.
    """
    latitude, longitude = to_coordinates(telemetry)
    return {
        "recorded_at": telemetry.recorded_at,
        "latitude": latitude,
        "longitude": longitude,
        "speed": telemetry.speed,
        "heading": telemetry.heading,
        "soc": telemetry.soc,
        "battery_voltage": telemetry.battery_voltage,
        "battery_current": telemetry.battery_current,
        "battery_temperature": telemetry.battery_temperature,
        "soh_percent": telemetry.soh_percent,
        "cycle_count": telemetry.cycle_count,
        "motor_temperature": telemetry.motor_temperature,
        "odometer": telemetry.odometer,
        "signal_strength": telemetry.signal_strength,
        "error_codes": telemetry.error_codes,
        "schema_version": telemetry.schema_version,
    }


def to_vehicle_telemetry_latest_response(
    telemetry: VehicleTelemetryModel, *, is_online: bool
) -> VehicleTelemetryLatestResponse:
    """Build the latest-telemetry response from the ORM row (F-A1).

    Args:
        telemetry: Telemetry ORM row queried by the repository.
        is_online: Online flag already computed by the service.

    Returns:
        Response schema with latitude/longitude decoded from ``location``.
    """
    return VehicleTelemetryLatestResponse(
        vehicle_id=telemetry.vehicle_id,
        telematic_serial=telemetry.telematic_serial,
        received_at=telemetry.received_at,
        is_online=is_online,
        **_to_reading_fields(telemetry),
    )


def to_vehicle_live_status_reference(
    telemetry: VehicleTelemetryModel, *, is_online: bool
) -> VehicleLiveStatusReference:
    """Build the cross-domain live-status DTO from the newest ORM row (F-A1).

    Args:
        telemetry: The vehicle's newest telemetry row.
        is_online: Online flag already computed by the service.

    Returns:
        The vehicle's position, timestamps, online flag and signal strength.
    """
    latitude, longitude = to_coordinates(telemetry)
    return VehicleLiveStatusReference(
        vehicle_id=telemetry.vehicle_id,
        latitude=latitude,
        longitude=longitude,
        recorded_at=telemetry.recorded_at,
        received_at=telemetry.received_at,
        is_online=is_online,
        signal_strength_dbm=telemetry.signal_strength,
    )


def to_fleet_vehicle_live_status_response(
    vehicle_id: UUID,
    vehicle_summary: VehicleSummary | None,
    live_status: VehicleLiveStatusReference | None,
) -> FleetVehicleLiveStatusResponse:
    """Build one fleet-map row from a member's vehicle data and live status (F-E1).

    Args:
        vehicle_id: Internal ID of the member vehicle.
        vehicle_summary: The vehicle's display data, or ``None`` when the
            vehicle was soft-deleted after joining.
        live_status: The vehicle's newest position and online flag, or
            ``None`` when it has never reported.

    Returns:
        The row; vehicle fields are ``None`` without a summary, position
        fields are ``None`` and ``is_online`` is ``False`` without a live
        status.
    """
    return FleetVehicleLiveStatusResponse(
        vehicle_id=vehicle_id,
        vin=vehicle_summary.vin if vehicle_summary is not None else None,
        license_plate=(
            vehicle_summary.license_plate if vehicle_summary is not None else None
        ),
        vehicle_status=vehicle_summary.status if vehicle_summary is not None else None,
        latitude=live_status.latitude if live_status is not None else None,
        longitude=live_status.longitude if live_status is not None else None,
        recorded_at=live_status.recorded_at if live_status is not None else None,
        received_at=live_status.received_at if live_status is not None else None,
        is_online=live_status.is_online if live_status is not None else False,
        signal_strength_dbm=(
            live_status.signal_strength_dbm if live_status is not None else None
        ),
    )


def to_vehicle_telemetry_history_point(
    telemetry: VehicleTelemetryModel,
) -> VehicleTelemetryHistoryPoint:
    """Build one history point from the ORM row (F-A5).

    Args:
        telemetry: Telemetry ORM row queried by the repository.

    Returns:
        One point with latitude/longitude decoded from ``location``.
    """
    return VehicleTelemetryHistoryPoint(**_to_reading_fields(telemetry))


def to_telemetry_snapshot(message: TelemetryMessage) -> dict[str, object]:
    """Build a JSONB-safe data snapshot of a telemetry message (F-A4).

    This is the "event log with a data snapshot" F-A4 asks for - stored
    inside the anomaly notification's ``payload`` rather than a separate
    table (see ``docs/planners/done/backend-anomaly-detection.md``).
    Only JSON-serializable values are included (``UUID``/``datetime`` are
    converted to strings) since ``payload`` is a JSONB column.

    Args:
        message: The telemetry message the anomaly was detected in.

    Returns:
        A flat dict of the message's fields relevant to investigating an
        anomaly.
    """
    return {
        "message_uuid": str(message.message_uuid),
        "recorded_at": message.recorded_at.isoformat(),
        "latitude": message.location.latitude,
        "longitude": message.location.longitude,
        "speed": message.vehicle_state.speed if message.vehicle_state else None,
        "odometer": message.vehicle_state.odometer if message.vehicle_state else None,
        "soc": message.battery.soc,
        "battery_voltage": message.battery.voltage,
        "battery_current": message.battery.current,
        "battery_temperature": message.battery.temperature,
        "motor_temperature": message.motor.temperature if message.motor else None,
        "error_codes": message.errors,
        "schema_version": message.schema_version,
    }
