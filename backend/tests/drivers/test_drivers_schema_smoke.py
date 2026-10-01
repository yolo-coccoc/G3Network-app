"""Smoke tests for the driver request schemas."""

import pytest
from pydantic import ValidationError

from app.domains.drivers.schemas import DriverCreateRequest, DriverVehicleAssignRequest


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
