"""Smoke tests for the shared PostGIS coordinate conversion helpers."""

import pytest

from app.libs.common.geo import coordinates_to_location, location_to_coordinates


def test_geo_location_round_trips_through_postgis_conversion() -> None:
    """Latitude/longitude survive the PostGIS geography conversion round trip.

    Regression guard for the x/y (longitude/latitude) ordering Shapely and
    PostGIS both expect - a swapped pair would still "work" (no exception)
    but silently store the wrong location. Shared by charging_stations and
    telemetry, so this test covers both domains' storage.
    """
    location = coordinates_to_location(10.762622, 106.660172)

    latitude, longitude = location_to_coordinates(location)

    assert latitude == pytest.approx(10.762622)
    assert longitude == pytest.approx(106.660172)


def test_geo_location_conversion_handles_missing_coordinates() -> None:
    """No location, or a partially-missing pair, converts to/from ``None``."""
    assert coordinates_to_location(None, None) is None
    assert coordinates_to_location(10.762622, None) is None
    assert location_to_coordinates(None) == (None, None)
