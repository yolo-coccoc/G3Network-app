"""Smoke tests for the charging station request schemas."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domains.charging_stations.schemas import (
    ChargingEvseUpdateRequest,
    ChargingLocationCreateRequest,
    ChargingLocationUpdateRequest,
    ChargingStationUpdateRequest,
)
from app.domains.charging_stations.types import ChargingResourceStatus


def test_location_create_request_requires_the_map_pin() -> None:
    """A location is entered only once its position is known (CS-12)."""
    with pytest.raises(ValidationError):
        ChargingLocationCreateRequest.model_validate(
            {
                "organization_id": str(uuid4()),
                "display_name": "No pin",
                "address": "Somewhere",
                "is_public": True,
            }
        )

    location = ChargingLocationCreateRequest(
        organization_id=uuid4(),
        display_name="  Binh Duong  ",
        address="Km 1",
        latitude=10.98,
        longitude=106.65,
        is_public=False,
    )
    assert location.display_name == "Binh Duong"


def test_location_update_request_requires_the_coordinates_together() -> None:
    """One coordinate alone is not a position."""
    with pytest.raises(ValidationError):
        ChargingLocationUpdateRequest.model_validate({"latitude": 10.0})


@pytest.mark.parametrize(
    "request_class",
    [
        ChargingLocationUpdateRequest,
        ChargingStationUpdateRequest,
        ChargingEvseUpdateRequest,
    ],
)
def test_going_inactive_needs_a_reason(request_class: type) -> None:
    """A person taking something out of service must say why (DM-19, DM-25)."""
    with pytest.raises(ValidationError):
        request_class(status=ChargingResourceStatus.INACTIVE)

    inactive = request_class(
        status=ChargingResourceStatus.INACTIVE, status_reason="Under repair"
    )
    assert inactive.status_reason == "Under repair"
    assert request_class(status=ChargingResourceStatus.ACTIVE).status_reason is None
