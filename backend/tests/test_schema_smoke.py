"""Smoke test for the backend's schema validation and canonical values."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from geoalchemy2.elements import WKBElement
from ocpp.v201.datatypes import MeterValueType, SampledValueType, UnitOfMeasureType
from ocpp.v201.enums import MeasurandEnumType
from pydantic import ValidationError

from app.domains.charging_stations.ocpp.ocpp_server import extract_meter_samples
from app.domains.charging_stations.schemas import ChargingStationCreateRequest
from app.domains.telematics.schemas import TelematicCreateRequest
from app.domains.telematics.types import TelematicStatus
from app.domains.telemetry.schemas import TelemetryMessage
from app.domains.vehicles.schemas import VehicleCreateRequest
from app.domains.vehicles.types import VehicleStatus
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
    (future.md item 9) - the dict must match VehicleTelemetryModel's
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
    location = cast(WKBElement, values["location"])
    latitude, longitude = location_to_coordinates(location)
    assert latitude == pytest.approx(10.8)
    assert longitude == pytest.approx(106.7)


def test_charging_station_request_rejects_partial_location() -> None:
    """A charging station request requires latitude/longitude together, or neither."""
    with pytest.raises(ValidationError):
        ChargingStationCreateRequest(
            ocpp_identity="STATION-PARTIAL-001",
            display_name="Partial Location Station",
            latitude=10.762622,
            longitude=None,
            power_rating_kw=None,
            connector_standard=None,
            operating_hours=None,
            maintenance_status=None,
        )

    without_location = ChargingStationCreateRequest(
        ocpp_identity="STATION-NO-LOCATION-001",
        display_name="No Location Station",
        latitude=None,
        longitude=None,
        power_rating_kw=None,
        connector_standard=None,
        operating_hours=None,
        maintenance_status=None,
    )
    assert without_location.latitude is None
    assert without_location.longitude is None

    with_location = ChargingStationCreateRequest(
        ocpp_identity="STATION-WITH-LOCATION-001",
        display_name="With Location Station",
        latitude=10.762622,
        longitude=106.660172,
        power_rating_kw=None,
        connector_standard=None,
        operating_hours=None,
        maintenance_status=None,
    )
    assert with_location.latitude == 10.762622
    assert with_location.longitude == 106.660172


def test_vehicle_and_telematic_requests_validate_core_contract() -> None:
    """Vehicle and telematic requests accept valid data and reject an invalid VIN."""
    vehicle = VehicleCreateRequest(
        license_plate="TEST-001",
        vin="1HGBH41JXMN109186",
        make="G3Network",
        model="E-Truck",
        year=2026,
        status=VehicleStatus.ACTIVE,
        fleet_id=None,
    )
    telematic = TelematicCreateRequest(
        telematic_serial="TBOX-TEST-001",
        vehicle_vin=vehicle.vin,
        status=TelematicStatus.ACTIVE,
        firmware_version=None,
    )

    assert telematic.vehicle_vin == vehicle.vin
    with pytest.raises(ValidationError):
        VehicleCreateRequest(
            license_plate="TEST-002",
            vin="VIN-TOO-SHORT",
            make="G3Network",
            model="E-Truck",
            year=2026,
            fleet_id=None,
        )


def test_ocpp_meter_value_is_kept_without_unit_conversion() -> None:
    """An OCPP meter value is kept as-is, with no unit conversion."""
    meter_value = MeterValueType(
        timestamp="2026-08-26T10:00:00Z",
        sampled_value=[
            SampledValueType(
                value=1.25,
                measurand=MeasurandEnumType.energy_active_import_register,
                unit_of_measure=UnitOfMeasureType(unit="kWh"),
            )
        ],
    )

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("1.25")
