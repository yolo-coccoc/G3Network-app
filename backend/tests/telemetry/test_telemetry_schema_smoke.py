"""Smoke tests for the telemetry MQTT message schema."""

from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

import pytest
from geoalchemy2.elements import WKBElement
from pydantic import ValidationError

from app.domains.telemetry.schemas import TelemetryMessage
from app.libs.common.geo import location_to_coordinates


def _valid_telemetry_payload() -> dict[str, object]:
    """Create a minimal valid payload for the telemetry schema."""
    return {
        "message_uuid": str(uuid4()),
        "telematic_serial": "TBOX-TEST-001",
        "recorded_at": "2026-08-26T10:00:00+07:00",
        "location": {"latitude": 10.8, "longitude": 106.7},
        "battery": {"soc": 80},
    }


def test_telemetry_timestamp_is_normalized_to_utc() -> None:
    """A timestamp with a timezone must be converted to UTC."""
    message = TelemetryMessage.model_validate(_valid_telemetry_payload())

    assert message.recorded_at == datetime(2026, 8, 26, 3, 0, tzinfo=timezone.utc)


def test_telemetry_message_schema_version_defaults_to_one() -> None:
    """schema_version defaults to 1 for devices/tests that don't send it.

    F-A1's "schema is versioned" requirement must stay backward compatible
    with every existing device and fixture that predates this field.
    """
    message = TelemetryMessage.model_validate(_valid_telemetry_payload())
    assert message.schema_version == 1

    versioned_payload = _valid_telemetry_payload()
    versioned_payload["schema_version"] = 2
    versioned_message = TelemetryMessage.model_validate(versioned_payload)
    assert versioned_message.schema_version == 2


def test_telemetry_rejects_naive_timestamp_and_invalid_location() -> None:
    """The schema rejects a timezone-naive timestamp and an out-of-range GPS location."""
    naive_payload = _valid_telemetry_payload()
    naive_payload["recorded_at"] = "2026-08-26T10:00:00"
    with pytest.raises(ValidationError):
        TelemetryMessage.model_validate(naive_payload)

    invalid_location = _valid_telemetry_payload()
    invalid_location["location"] = {"latitude": 100, "longitude": 106.7}
    with pytest.raises(ValidationError):
        TelemetryMessage.model_validate(invalid_location)


def test_telemetry_message_stores_location_as_geography_not_lat_lon() -> None:
    """to_vehicle_telemetry_values() outputs a PostGIS point, not lat/lon columns.

    Regression guard for the vehicle_telemetry storage unification
    (deferred.md item 9) - the dict must match VehicleTelemetryModel's
    location column, not the old latitude/longitude columns.
    """
    message = TelemetryMessage.model_validate(_valid_telemetry_payload())

    values = message.to_vehicle_telemetry_values(
        uuid4(),
        uuid4(),
        datetime.now(timezone.utc),
        {},
    )

    assert "latitude" not in values
    assert "longitude" not in values
    assert values["schema_version"] == 1
    location = cast(WKBElement, values["location"])
    latitude, longitude = location_to_coordinates(location)
    assert latitude == pytest.approx(10.8)
    assert longitude == pytest.approx(106.7)
