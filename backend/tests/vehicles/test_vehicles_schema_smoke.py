"""Smoke tests for the vehicle and telematic request contracts."""

from uuid import uuid4

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
    organization_id = uuid4()
    vehicle = VehicleCreateRequest(
        organization_id=organization_id,
        license_plate="TEST-001",
        vin="1HGBH41JXMN109186",
        vehicle_model_id=uuid4(),
        year=2026,
        status=VehicleStatus.ACTIVE,
    )
    telematic = TelematicCreateRequest(
        telematic_serial="TBOX-TEST-001",
        organization_id=organization_id,
        vehicle_vin=vehicle.vin,
        status=TelematicStatus.ACTIVE,
    )

    assert telematic.vehicle_vin == vehicle.vin
    with pytest.raises(ValidationError):
        VehicleCreateRequest(
            organization_id=uuid4(),
            license_plate="TEST-002",
            vin="VIN-TOO-SHORT",
            vehicle_model_id=uuid4(),
            year=2026,
        )
