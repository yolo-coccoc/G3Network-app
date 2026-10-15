"""Station search: the nearest available station and the nearby search (F-A2, F-D1).

"Available" means the charger is ``ACTIVE`` and not deleted, stands at an
``ACTIVE`` location the caller may see and has at least one gun whose last
reported status is ``Available``; ``is_online`` is not required. The system
lookup (``find_nearest_operational_station``, for a battery alert) sees public
locations only. Internal module: ``service.py`` wraps the system lookup for
other domains. Runs in the caller's transaction and never commits.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.access as station_access
import app.domains.charging_stations.mappers as station_mappers
import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.models import (
    ChargingLocationModel,
    ChargingStationModel,
    ChargingStationStateModel,
)
from app.domains.charging_stations.schemas import (
    NearbyChargingStationListResponse,
    NearbyChargingStationResponse,
)
from app.domains.charging_stations.types import (
    ChargingResourceStatus,
    NearestChargingStationReference,
)
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location, location_to_coordinates
from app.libs.common.pagination import normalize_page_window


async def find_nearest_operational_station(
    db: AsyncSession, *, latitude: float, longitude: float
) -> NearestChargingStationReference | None:
    """Find the nearest available station to a point. Public entry point for F-A2.

    A station qualifies when the charger is ``ACTIVE`` and not deleted, its
    location is ``ACTIVE``, public and has a position, and at least one
    connector last reported ``Available``. The charger's ``is_online`` is
    deliberately not consulted (D3): a connector's last reported status is the
    availability signal.

    Args:
        db: Async session owned by the caller's entry boundary (e.g. the
            telemetry ingestion worker's transaction).
        latitude: GPS latitude in decimal degrees of the query point.
        longitude: GPS longitude in decimal degrees of the query point.

    Returns:
        A minimal reference DTO for the nearest qualifying station - never the
        ORM model - or ``None`` if none qualifies. Its name is the location's.
    """
    query_point = coordinates_to_location(latitude, longitude)
    assert query_point is not None, "latitude/longitude are both required here"
    match = await charging_stations_repository.find_nearest_station_by_location(
        db, query_point
    )
    if match is None:
        return None
    station, location, distance_meters = match
    station_latitude, station_longitude = location_to_coordinates(location.coordinates)
    assert station_latitude is not None and station_longitude is not None, (
        "a location always has coordinates"
    )
    return NearestChargingStationReference(
        station_id=station.station_id,
        display_name=location.display_name,
        latitude=station_latitude,
        longitude=station_longitude,
        distance_km=distance_meters / 1000,
    )


def to_nearby_charging_station_response(
    station: ChargingStationModel,
    location: ChargingLocationModel,
    state: ChargingStationStateModel | None,
    *,
    connector_count: int,
    available_connector_count: int,
    distance_km: float,
    now: datetime | None = None,
) -> NearbyChargingStationResponse:
    """Build a driver-facing nearby-station response (F-D1).

    Pure mapping only - the connector counts and distance are computed by
    the caller, since a pure mapper must never do I/O. The clock (for
    ``is_online``) can be injected through ``now``.

    Args:
        station: Station ORM object queried by the repository.
        location: The location the station stands at.
        state: The station's state row, or ``None``.
        connector_count: Number of active connectors, already computed by
            the caller.
        available_connector_count: How many of them last reported
            ``Available``, already computed by the caller.
        distance_km: Distance from the query point, already computed by
            the caller.
        now: Reference time for the online check; defaults to the current
            UTC time.

    Returns:
        Driver-facing response schema, without the internal/admin fields
        ``ChargingStationResponse`` carries.
    """
    checked_at = now if now is not None else utc_now()
    latitude, longitude = location_to_coordinates(location.coordinates)
    return NearbyChargingStationResponse(
        station_id=station.station_id,
        location_id=station.location_id,
        display_name=location.display_name,
        physical_reference=station.physical_reference,
        latitude=latitude,
        longitude=longitude,
        max_power_kw=station_mappers.to_optional_float(station.max_power_kw),
        status=ChargingResourceStatus(station.status),
        connector_count=connector_count,
        available_connector_count=available_connector_count,
        is_online=station_mappers.is_station_online(state, checked_at),
        distance_km=distance_km,
    )


async def list_nearby_charging_stations(
    db: AsyncSession,
    *,
    principal: Principal,
    latitude: float,
    longitude: float,
    radius_km: float,
    connector_standard: str | None = None,
    min_power_kw: float | None = None,
    is_operational_only: bool = True,
    is_available_only: bool = False,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> NearbyChargingStationListResponse:
    """Find stations within a radius of a point, filtered and paginated (F-D1).

    Generalizes ``find_nearest_operational_station`` (F-A2) from "1
    nearest" to "N within a radius, filtered by plug standard/power,
    nearest first". Only chargers at ``ACTIVE`` locations the caller may see
    are found: public ones, the caller's organization's own, those it holds
    a live grant on, and every location for internal staff (CS-10, CS-13).

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller (decides which private locations show).
        latitude: GPS latitude in decimal degrees of the query point.
        longitude: GPS longitude in decimal degrees of the query point.
        radius_km: Search radius in km; clamped to
            ``(0, settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM]``.
        connector_standard: Plug standard (OCPI name or a short name such as
            ``CCS2``): the charger needs an in-service gun of it. ``None`` to
            not filter.
        min_power_kw: Minimum charger ``max_power_kw``, or ``None``.
        is_operational_only: Whether to only return ``ACTIVE`` chargers.
        is_available_only: Whether to only return available stations:
            ``ACTIVE`` and at least one connector whose last reported status
            is ``Available`` - the same rule as F-A2; ``is_online`` is not
            required. Implies ``is_operational_only``.
        page: Page number starting at one; lower values are clamped to the
            default.
        page_size: Page size, clamped according to settings.

    Returns:
        Matching stations nearest-first, and pagination metadata.

    Side Effects:
        Performs two read queries plus the state and two connector-count
        queries per station on the page - the same deliberate, deferred N+1 as
        ``list_charging_stations`` (``docs/decisions/deferred.md`` item 34);
        does not commit or rollback.
    """
    # radius_km > 0 is enforced by the router's Query validation; clamp only
    # the upper bound here so a non-HTTP caller can't request an unbounded
    # PostGIS scan, and floor negative/zero input rather than passing it
    # straight to ST_DWithin.
    radius_km = min(
        max(radius_km, 0.001), settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM
    )
    page_window = normalize_page_window(page, page_size)

    query_point = coordinates_to_location(latitude, longitude)
    assert query_point is not None, "latitude/longitude are both required here"
    radius_meters = radius_km * 1000
    min_power_decimal = Decimal(str(min_power_kw)) if min_power_kw is not None else None
    standard = (
        station_mappers.normalize_connector_standard(connector_standard)
        if connector_standard is not None
        else None
    )

    matches = await charging_stations_repository.list_nearby_stations(
        db,
        point=query_point,
        radius_meters=radius_meters,
        connector_standard=standard,
        min_power_kw=min_power_decimal,
        is_operational_only=is_operational_only,
        is_available_only=is_available_only,
        offset=page_window.offset,
        limit=page_window.page_size,
        viewer=station_access.viewer_of(principal),
    )
    total = await charging_stations_repository.count_nearby_stations(
        db,
        point=query_point,
        radius_meters=radius_meters,
        connector_standard=standard,
        min_power_kw=min_power_decimal,
        is_operational_only=is_operational_only,
        is_available_only=is_available_only,
        viewer=station_access.viewer_of(principal),
    )
    items = []
    for station, location, distance_meters in matches:
        state = await charging_stations_repository.get_station_state(
            db, station.station_id
        )
        connector_count = (
            await charging_stations_repository.count_connectors_by_station_id(
                db, station.station_id
            )
        )
        available_connector_count = (
            await charging_stations_repository.count_available_connectors_by_station_id(
                db, station.station_id
            )
        )
        items.append(
            to_nearby_charging_station_response(
                station,
                location,
                state,
                connector_count=connector_count,
                available_connector_count=available_connector_count,
                distance_km=distance_meters / 1000,
            )
        )
    return NearbyChargingStationListResponse(
        items=items,
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )
