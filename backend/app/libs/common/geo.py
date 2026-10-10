"""Shared conversion helpers between (latitude, longitude) pairs and PostGIS.

Pure functions only, no business logic and no I/O - fits `libs/common`'s
role as generic backend-wide utilities. Used wherever a domain stores
location as a PostGIS ``geography(Point, 4326)`` column instead of plain
lat/lon columns (currently `charging_stations` and `telemetry`) but still
wants to accept/expose plain latitude/longitude at its API boundary.
"""

import math

from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point


def location_to_coordinates(
    location: WKBElement | None,
) -> tuple[float | None, float | None]:
    """Convert a stored PostGIS geography point into (latitude, longitude).

    Args:
        location: Geography point read back from the ORM, or ``None``.

    Returns:
        ``(latitude, longitude)``, or ``(None, None)`` if no location is set.
    """
    if location is None:
        return None, None
    point = to_shape(location)
    return point.y, point.x


def coordinates_to_location(
    latitude: float | None, longitude: float | None
) -> WKBElement | None:
    """Convert a (latitude, longitude) pair into a PostGIS geography point.

    Args:
        latitude: Latitude in decimal degrees, or ``None``.
        longitude: Longitude in decimal degrees, or ``None``.

    Returns:
        A geography point ready to persist, or ``None`` if either coordinate
        is missing.

    Note:
        A caller whose contract requires both coordinates together (e.g.
        ``ChargingStationCreateRequest``/``ChargingStationUpdateRequest``)
        validates that at its own boundary; this function only guards
        against a partially-missing pair here.
    """
    if latitude is None or longitude is None:
        return None
    return from_shape(Point(longitude, latitude), srid=4326)


EARTH_RADIUS_M = 6_371_008.8


def calculate_distance_m(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Calculate the great-circle distance between two points (haversine).

    Args:
        latitude_a: Latitude of the first point in decimal degrees.
        longitude_a: Longitude of the first point in decimal degrees.
        latitude_b: Latitude of the second point in decimal degrees.
        longitude_b: Longitude of the second point in decimal degrees.

    Returns:
        The distance in metres on a spherical Earth; precise enough (well
        under 1 %) for "is the phone next to the truck" checks.
    """
    phi_a = math.radians(latitude_a)
    phi_b = math.radians(latitude_b)
    delta_phi = phi_b - phi_a
    delta_lambda = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(haversine)))
