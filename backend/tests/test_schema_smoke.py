"""Smoke test cho validation schema và canonical value của backend."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from ocpp.v201.datatypes import MeterValueType, SampledValueType, UnitOfMeasureType
from ocpp.v201.enums import MeasurandEnumType
from pydantic import ValidationError

from app.domains.charging_stations.ocpp.ocpp_server import extract_meter_samples
from app.domains.telematics.schemas import TelematicCreateRequest
from app.domains.telematics.types import TelematicStatus
from app.domains.telemetry.schemas import TelemetryMessage
from app.domains.vehicles.schemas import VehicleCreateRequest
from app.domains.vehicles.types import VehicleStatus


def _valid_telemetry_payload() -> dict[str, object]:
    """Tạo payload tối thiểu hợp lệ cho schema telemetry."""
    return {
        "message_uuid": str(uuid4()),
        "telematic_serial": "TBOX-TEST-001",
        "recorded_at": "2026-08-26T10:00:00+07:00",
        "location": {"latitude": 10.8, "longitude": 106.7},
        "battery": {"soc": 80},
    }


def test_telemetry_timestamp_is_normalized_to_utc() -> None:
    """Timestamp có timezone phải được chuyển về UTC."""
    message = TelemetryMessage.model_validate(_valid_telemetry_payload())

    assert message.recorded_at == datetime(2026, 8, 26, 3, 0, tzinfo=timezone.utc)


def test_telemetry_rejects_naive_timestamp_and_invalid_location() -> None:
    """Schema từ chối timestamp không timezone và GPS ngoài range."""
    naive_payload = _valid_telemetry_payload()
    naive_payload["recorded_at"] = "2026-08-26T10:00:00"
    with pytest.raises(ValidationError):
        TelemetryMessage.model_validate(naive_payload)

    invalid_location = _valid_telemetry_payload()
    invalid_location["location"] = {"latitude": 100, "longitude": 106.7}
    with pytest.raises(ValidationError):
        TelemetryMessage.model_validate(invalid_location)


def test_vehicle_and_telematic_requests_validate_core_contract() -> None:
    """Vehicle và telematic request nhận dữ liệu hợp lệ, reject VIN sai."""
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
    """Giá trị meter OCPP được giữ nguyên, không đổi đơn vị."""
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
