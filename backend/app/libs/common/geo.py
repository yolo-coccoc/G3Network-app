"""Shared conversion helpers between (latitude, longitude) pairs and PostGIS.

Pure functions only, no business logic and no I/O - fits `libs/common`'s
role as generic backend-wide utilities. Used wherever a domain stores
location as a PostGIS ``geography(Point, 4326)`` column instead of plain
lat/lon columns (currently `charging_stations` and `telemetry`) but still
wants to accept/expose plain latitude/longitude at its API boundary.
"""

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
