"""Smoke tests for the vehicle and telematic request contracts."""

import pytest
from pydantic import ValidationError

from app.domains.telematics.schemas import (
    TelematicCreateRequest,
)
from app.domains.telematics.types import TelematicStatus
from app.domains.vehicles.schemas import VehicleCreateRequest
from app.domains.vehicles.types import VehicleStatus


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
        )
