"""Smoke tests for the fleet request schemas."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domains.fleet.schemas import (
    FleetCreateRequest,
    FleetVehicleAddRequest,
    GeofenceCreateRequest,
    GeofencePolygonGeoJson,
)


def test_fleet_create_request_validates_core_contract() -> None:
    """A fleet request accepts valid data and rejects an empty fleet code (F-E1)."""
    organization_id = uuid4()
    fleet = FleetCreateRequest(
        organization_id=organization_id, fleet_code="FLEET-001", name="Hanoi Fleet"
    )

    assert fleet.fleet_code == "FLEET-001"
    with pytest.raises(ValidationError):
        FleetCreateRequest(
            organization_id=organization_id, fleet_code="", name="Hanoi Fleet"
        )
    # The owner is the caller's organization unless internal staff name one.
    assert FleetCreateRequest(fleet_code="FLEET-001").organization_id is None


def test_fleet_create_request_needs_a_name_or_a_code() -> None:
    """Either a name or a code is enough; a fleet with neither is rejected (FL-08)."""
    organization_id = uuid4()
    assert (
        FleetCreateRequest(
            organization_id=organization_id, name="Hanoi Fleet"
        ).fleet_code
        is None
    )
    assert (
        FleetCreateRequest(organization_id=organization_id, fleet_code="HN-01").name
        is None
    )
    with pytest.raises(ValidationError):
        FleetCreateRequest(organization_id=organization_id)


def test_fleet_vehicle_add_request_rejects_malformed_vin() -> None:
    """F-E1's membership request enforces the same 17-character VIN length as vehicles."""
    FleetVehicleAddRequest(vehicle_vin="1HGBH41JXMN109186")
    with pytest.raises(ValidationError):
        FleetVehicleAddRequest(vehicle_vin="TOO-SHORT")


_SQUARE_RING = [
    [106.70, 10.77],
    [106.71, 10.77],
    [106.71, 10.78],
    [106.70, 10.78],
    [106.70, 10.77],
]


def test_geofence_polygon_accepts_a_closed_ring() -> None:
    """A closed ring of [longitude, latitude] positions is a valid boundary (F-A5)."""
    boundary = GeofencePolygonGeoJson.model_validate(
        {"type": "Polygon", "coordinates": [_SQUARE_RING]}
    )

    assert boundary.coordinates[0][0] == (106.70, 10.77)
    assert len(boundary.coordinates[0]) == 5


@pytest.mark.parametrize(
    ("coordinates", "reason"),
    [
        ([_SQUARE_RING[:3]], "too few positions"),
        ([_SQUARE_RING[:4]], "ring not closed"),
        (
            [[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]],
            "ring crosses itself",
        ),
        ([[[200.0, 10.0], *_SQUARE_RING[1:]]], "longitude out of range"),
        ([_SQUARE_RING, _SQUARE_RING], "a hole (second ring)"),
        ([], "no ring"),
    ],
)
def test_geofence_polygon_rejects_an_invalid_ring(
    coordinates: list[list[list[float]]], reason: str
) -> None:
    """An unusable boundary is rejected at the schema, before any query (F-A5)."""
    with pytest.raises(ValidationError):
        GeofencePolygonGeoJson.model_validate(
            {"type": "Polygon", "coordinates": coordinates}
        )


def test_geofence_create_request_rejects_a_non_polygon_type() -> None:
    """Only the GeoJSON Polygon type is accepted (F-A5)."""
    with pytest.raises(ValidationError):
        GeofenceCreateRequest.model_validate(
            {
                "name": "Depot",
                "boundary": {"type": "Point", "coordinates": [_SQUARE_RING]},
            }
        )
