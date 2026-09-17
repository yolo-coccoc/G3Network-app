"""Smoke test for the backend's schema validation and canonical values."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from geoalchemy2.elements import WKBElement
from pydantic import ValidationError

from app.domains.charging_stations.ocpp.ocpp_server import (
    extract_meter_samples,
    parse_ocpp_evse_reference,
    parse_ocpp_transaction_id,
)
from app.domains.charging_stations.schemas import ChargingStationCreateRequest
from app.domains.drivers.schemas import DriverCreateRequest, DriverVehicleAssignRequest
from app.domains.telematics.schemas import (
    TelematicConfigPushRequest,
    TelematicCreateRequest,
)
from app.domains.telematics.types import TelematicStatus
from app.domains.telemetry.schemas import TelemetryMessage
from app.domains.vehicles.schemas import VehicleCreateRequest
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
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
    assert values["schema_version"] == 1
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
        battery_capacity_kwh=None,
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
            battery_capacity_kwh=None,
            fleet_id=None,
        )


def test_telematic_config_push_request_rejects_interval_outside_bounds() -> None:
    """F-J2's requested telemetry interval must stay within the configured bounds."""
    TelematicConfigPushRequest(
        telemetry_interval_seconds=settings.TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS
    )
    TelematicConfigPushRequest(
        telemetry_interval_seconds=settings.TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS
    )
    with pytest.raises(ValidationError):
        TelematicConfigPushRequest(
            telemetry_interval_seconds=(
                settings.TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS - 1
            )
        )
    with pytest.raises(ValidationError):
        TelematicConfigPushRequest(
            telemetry_interval_seconds=(
                settings.TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS + 1
            )
        )


def test_driver_create_request_validates_core_contract() -> None:
    """A driver request accepts valid data and rejects an empty full name (F-E4)."""
    driver = DriverCreateRequest(
        full_name="Test Driver",
        phone_number="0900000001",
        license_number="LICENSE-001",
    )

    assert driver.license_number == "LICENSE-001"
    with pytest.raises(ValidationError):
        DriverCreateRequest(
            full_name="",
            phone_number="0900000001",
            license_number="LICENSE-001",
        )


def test_driver_vehicle_assign_request_rejects_malformed_vin() -> None:
    """F-E4's assignment request enforces the same 17-character VIN length as vehicles."""
    DriverVehicleAssignRequest(vehicle_vin="1HGBH41JXMN109186")
    with pytest.raises(ValidationError):
        DriverVehicleAssignRequest(vehicle_vin="TOO-SHORT")


def test_extract_meter_samples_reads_raw_dict_payload() -> None:
    """extract_meter_samples parses the plain-dict shape python-ocpp actually
    delivers (Step 0's regression test) - not the ocpp.v201 dataclasses.
    """
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 1.25}],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("1.25")


def test_ocpp_kwh_sample_is_normalized_to_wh() -> None:
    """A kWh-unit energy-register sample is converted to Wh (F-B2).

    Renamed from ``test_ocpp_meter_value_is_kept_without_unit_conversion``,
    which used to assert the bug this fix removes (a kWh reading kept
    as-is, off by a factor of 1000). Renaming rather than editing in
    place makes the behavior change impossible to miss in review.
    """
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [
            {
                "value": 1.25,
                "measurand": "Energy.Active.Import.Register",
                "unit_of_measure": {"unit": "kWh"},
            }
        ],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("1250")


def test_ocpp_sample_without_unit_defaults_to_wh() -> None:
    """A sample with no unit_of_measure at all is treated as Wh (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 500}],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("500")


def test_ocpp_sample_without_multiplier_defaults_to_zero() -> None:
    """A sample with a unit but no multiplier key defaults multiplier to 0 (F-B2).

    This is the exact shape the simulator sends: ``{"unit": "Wh"}`` with
    no ``multiplier`` key.
    """
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 500, "unit_of_measure": {"unit": "Wh"}}],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("500")


def test_ocpp_sample_multiplier_scales_value() -> None:
    """A non-zero multiplier scales the value by 10**multiplier (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [
            {"value": 1.5, "unit_of_measure": {"unit": "Wh", "multiplier": 3}}
        ],
    }

    samples = extract_meter_samples([meter_value])

    assert samples[0].value_wh == Decimal("1500")


def test_ocpp_sample_without_measurand_defaults_to_energy_register() -> None:
    """An absent measurand is treated as the energy import register (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 1000}],
    }

    samples = extract_meter_samples([meter_value])

    assert len(samples) == 1
    assert samples[0].value_wh == Decimal("1000")


def test_ocpp_non_energy_measurand_is_skipped() -> None:
    """A non-energy-register measurand (e.g. power) is skipped, not an error (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [{"value": 7.5, "measurand": "Power.Active.Import"}],
    }

    samples = extract_meter_samples([meter_value])

    assert samples == []


def test_ocpp_unknown_unit_on_energy_register_is_rejected() -> None:
    """An unrecognized unit on an energy-register sample raises, not silently skips (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [
            {
                "value": 1.0,
                "measurand": "Energy.Active.Import.Register",
                "unit_of_measure": {"unit": "J"},
            }
        ],
    }

    with pytest.raises(ValueError):
        extract_meter_samples([meter_value])


def test_ocpp_mixed_measurands_keep_only_energy_register() -> None:
    """A message with energy + power + SoC keeps only the energy register (F-B2)."""
    meter_value = {
        "timestamp": "2026-08-26T10:00:00Z",
        "sampled_value": [
            {"value": 1000, "measurand": "Energy.Active.Import.Register"},
            {"value": 7.5, "measurand": "Power.Active.Import"},
            {"value": 80, "measurand": "SoC"},
        ],
    }

    samples = extract_meter_samples([meter_value])

    assert len(samples) == 1
    assert samples[0].value_wh == Decimal("1000")


def test_parse_ocpp_transaction_id_reads_dict_payload() -> None:
    """parse_ocpp_transaction_id reads the snake_cased dict python-ocpp delivers."""
    assert parse_ocpp_transaction_id({"transaction_id": "TX-001"}) == "TX-001"
    with pytest.raises(ValueError):
        parse_ocpp_transaction_id({})


def test_parse_ocpp_evse_reference_reads_dict_payload() -> None:
    """parse_ocpp_evse_reference reads the snake_cased dict python-ocpp delivers."""
    assert parse_ocpp_evse_reference({"id": 1, "connector_id": 2}) == (1, 2)
    with pytest.raises(ValueError):
        parse_ocpp_evse_reference(None)
    with pytest.raises(ValueError):
        parse_ocpp_evse_reference({"id": 1})
