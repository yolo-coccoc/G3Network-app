"""Pure mappings from telemetry ORM rows and MQTT messages to other shapes.

Feature code: F-A1 (latest telemetry), F-A5 (history points), F-A4 (the
anomaly notification's data snapshot).

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

from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.schemas import (
    TelemetryMessage,
    VehicleTelemetryHistoryPoint,
    VehicleTelemetryLatestResponse,
)
from app.libs.common.geo import location_to_coordinates


def _to_reading_fields(telemetry: VehicleTelemetryModel) -> dict[str, Any]:
    """Collect the per-reading fields shared by the latest and history responses.

    Args:
        telemetry: Telemetry ORM row queried by the repository.

    Returns:
        Keyword arguments for every field ``VehicleTelemetryHistoryPoint``
        and ``VehicleTelemetryLatestResponse`` have in common, with
        latitude/longitude decoded from ``location``.
    """
    latitude, longitude = location_to_coordinates(telemetry.location)
    # location_to_coordinates()'s return type is generic (Optional, since
    # charging_stations.location can be null) - vehicle_telemetry.location
    # is NOT NULL, so this pair is never actually missing; the assertion
    # documents that invariant for both mypy and a future reader.
    assert latitude is not None and longitude is not None, (
        "vehicle_telemetry.location is NOT NULL"
    )
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
    telemetry: VehicleTelemetryModel,
) -> VehicleTelemetryLatestResponse:
    """Build the latest-telemetry response from the ORM row (F-A1).

    Args:
        telemetry: Telemetry ORM row queried by the repository.

    Returns:
        Response schema with latitude/longitude decoded from ``location``.
    """
    return VehicleTelemetryLatestResponse(
        vehicle_id=telemetry.vehicle_id,
        telematic_serial=telemetry.telematic_serial,
        **_to_reading_fields(telemetry),
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
    table (see ``docs/02-planners/done/backend-anomaly-detection.md``).
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
