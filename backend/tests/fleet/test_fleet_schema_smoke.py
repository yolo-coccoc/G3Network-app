"""Smoke tests for the fleet request schemas."""

import pytest
from pydantic import ValidationError

from app.domains.fleet.schemas import FleetCreateRequest, FleetVehicleAddRequest


def test_fleet_create_request_validates_core_contract() -> None:
    """A fleet request accepts valid data and rejects an empty fleet code (F-E1)."""
    fleet = FleetCreateRequest(fleet_code="FLEET-001", name="Hanoi Fleet")

    assert fleet.fleet_code == "FLEET-001"
    with pytest.raises(ValidationError):
        FleetCreateRequest(fleet_code="", name="Hanoi Fleet")


def test_fleet_vehicle_add_request_rejects_malformed_vin() -> None:
    """F-E1's membership request enforces the same 17-character VIN length as vehicles."""
    FleetVehicleAddRequest(vehicle_vin="1HGBH41JXMN109186")
    with pytest.raises(ValidationError):
        FleetVehicleAddRequest(vehicle_vin="TOO-SHORT")
