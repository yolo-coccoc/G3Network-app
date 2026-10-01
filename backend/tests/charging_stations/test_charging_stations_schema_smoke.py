"""Smoke tests for the charging station request schemas."""

import pytest
from pydantic import ValidationError

from app.domains.charging_stations.schemas import ChargingStationCreateRequest


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
