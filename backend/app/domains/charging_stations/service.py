"""Business service for charging topology CRUD and soft-delete.

The service is where pre-provisioning invariants are enforced: a station must
exist before an EVSE, an EVSE must belong to a station before a connector,
topology identities must not be reused even after the old record was
soft-deleted, and OCPP must not create topology on its own. The transaction
is owned by FastAPI's ``get_db``; this module does not commit/rollback.
"""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.charging_stations import repository
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingEvseNotFoundError,
    ChargingStationNotFoundError,
    ChargingTopologyConflictError,
)
from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingEvseModel,
    ChargingStationModel,
)
from app.domains.charging_stations.schemas import (
    ChargingConnectorCreateRequest,
    ChargingConnectorListResponse,
    ChargingConnectorResponse,
    ChargingConnectorUpdateRequest,
    ChargingEvseCreateRequest,
    ChargingEvseListResponse,
    ChargingEvseResponse,
    ChargingEvseUpdateRequest,
    ChargingResourceDeleteResponse,
    ChargingStationCreateRequest,
    ChargingStationListResponse,
    ChargingStationResponse,
    ChargingStationUpdateRequest,
    NearbyChargingStationListResponse,
    NearbyChargingStationResponse,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingStationMaintenanceStatus,
    NearestChargingStation,
)
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location, location_to_coordinates


def to_charging_station_response(
    station: ChargingStationModel, *, connector_count: int
) -> ChargingStationResponse:
    """Build a station response from the topology model and its connector count.

    Pure mapping only — the connector count is computed by the caller (via a
    repository query) rather than here, since a pure mapper must never do
    I/O.

    Args:
        station: Station ORM object queried or created by the repository.
        connector_count: Number of active connectors across the station's
            active EVSEs, already computed by the caller.

    Returns:
        Response schema including directory metadata and connector count.
    """
    latitude, longitude = location_to_coordinates(station.location)
    return ChargingStationResponse(
        station_id=station.station_id,
        ocpp_identity=station.ocpp_identity,
        display_name=station.display_name,
        latitude=latitude,
        longitude=longitude,
        power_rating_kw=(
            float(station.power_rating_kw)
            if station.power_rating_kw is not None
            else None
        ),
        connector_standard=station.connector_standard,
        operating_hours=station.operating_hours,
        maintenance_status=station.maintenance_status,
        connector_count=connector_count,
        created_at=station.created_at,
        updated_at=station.updated_at,
        deleted_at=station.deleted_at,
    )


def to_charging_evse_response(evse: ChargingEvseModel) -> ChargingEvseResponse:
    """Build an EVSE response from the ORM model.

    Args:
        evse: EVSE ORM object queried or created by the repository.

    Returns:
        Response schema corresponding to the EVSE.
    """
    return ChargingEvseResponse.model_validate(evse)


def to_charging_connector_response(
    connector: ChargingConnectorModel,
) -> ChargingConnectorResponse:
    """Build a connector response from the ORM model.

    Args:
        connector: Connector ORM object queried or created by the
            repository.

    Returns:
        Response schema corresponding to the connector.
    """
    return ChargingConnectorResponse.model_validate(connector)


def _clean_update_values(data: Mapping[str, object]) -> dict[str, object]:
    """Drop ``None`` fields per the backend's PATCH convention.

    Args:
        data: Mapping from ``model_dump(exclude_unset=True)``.

    Returns:
        Mapping containing only the fields with values to update.

    Note:
        This domain has no dedicated contract yet for clearing a nullable
        value with ``null``; therefore ``None`` is treated as "do not
        update".
    """
    return {
        field_name: value for field_name, value in data.items() if value is not None
    }


async def create_charging_station(
    db: AsyncSession, station_data: ChargingStationCreateRequest
) -> ChargingStationResponse:
    """Create a new station after checking the OCPP identity table-wide.

    Args:
        db: Async session owned by the HTTP boundary.
        station_data: Station data, already Pydantic-validated.

    Returns:
        The newly created station response.

    Raises:
        ChargingTopologyConflictError: If the identity already exists, even
            if soft-deleted.
    """
    if await repository.get_station_by_identity(db, station_data.ocpp_identity):
        raise ChargingTopologyConflictError(
            f"OCPP identity '{station_data.ocpp_identity}' already exists"
        )
    maintenance_status = (
        station_data.maintenance_status
        if station_data.maintenance_status is not None
        else ChargingStationMaintenanceStatus.OPERATIONAL
    )
    power_rating_kw = (
        Decimal(str(station_data.power_rating_kw))
        if station_data.power_rating_kw is not None
        else None
    )
    try:
        station = await repository.create_charging_station(
            db,
            ocpp_identity=station_data.ocpp_identity,
            display_name=station_data.display_name,
            location=coordinates_to_location(
                station_data.latitude, station_data.longitude
            ),
            power_rating_kw=power_rating_kw,
            connector_standard=station_data.connector_standard,
            operating_hours=station_data.operating_hours,
            maintenance_status=maintenance_status,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Station OCPP identity already exists"
        ) from error
    # A brand-new station has no EVSEs/connectors yet - no query needed.
    return to_charging_station_response(station, connector_count=0)


async def list_charging_stations(
    db: AsyncSession,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingStationListResponse:
    """List active stations with pagination bounded by settings.

    Args:
        db: Async session owned by the HTTP boundary.
        page: Page number starting at one; lower values are clamped to the
            default.
        page_size: Page size, clamped according to settings.

    Returns:
        List of stations and pagination metadata.

    Side Effects:
        Performs two read queries plus one connector-count query per station
        on the page (no batching yet - deliberately deferred until
        throughput needs it, see ``docs/01-requirements/future.md``); does
        not commit or rollback.
    """
    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(
        max(page_size, settings.API_DEFAULT_PAGE_SIZE), settings.API_MAX_PAGE_SIZE
    )
    offset = (page - 1) * page_size
    stations = await repository.list_charging_stations(
        db,
        offset=offset,
        limit=page_size,
    )
    total = await repository.count_stations(db)
    items = []
    for station in stations:
        connector_count = await repository.count_connectors_by_station_id(
            db, station.station_id
        )
        items.append(
            to_charging_station_response(station, connector_count=connector_count)
        )
    return ChargingStationListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_charging_station(
    db: AsyncSession, station_id: UUID
) -> ChargingStationResponse:
    """Get an active station by internal UUID.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station to query.

    Returns:
        The active station response.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            soft-deleted.
    """
    station = await repository.get_station_by_id(db, station_id)
    if station is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    connector_count = await repository.count_connectors_by_station_id(db, station_id)
    return to_charging_station_response(station, connector_count=connector_count)


async def find_nearest_operational_station(
    db: AsyncSession, *, latitude: float, longitude: float
) -> NearestChargingStation | None:
    """Find the nearest operational station to a point. Public entry point for F-A2.

    Args:
        db: Async session owned by the caller's entry boundary (e.g. the
            telemetry ingestion worker's transaction).
        latitude: GPS latitude in decimal degrees of the query point.
        longitude: GPS longitude in decimal degrees of the query point.

    Returns:
        A minimal reference DTO for the nearest active, operational station
        with a known location - never the ORM model - or ``None`` if none
        qualifies. "Operational" only reflects the admin-set
        ``maintenance_status``; there is no live occupancy signal (see
        ``NearestChargingStation``'s docstring).
    """
    query_point = coordinates_to_location(latitude, longitude)
    assert query_point is not None, "latitude/longitude are both required here"
    match = await repository.find_nearest_station_by_location(db, query_point)
    if match is None:
        return None
    station, distance_meters = match
    station_latitude, station_longitude = location_to_coordinates(station.location)
    assert (
        station_latitude is not None and station_longitude is not None
    ), "query filters out stations with a NULL location"
    return NearestChargingStation(
        station_id=station.station_id,
        display_name=station.display_name,
        latitude=station_latitude,
        longitude=station_longitude,
        distance_km=distance_meters / 1000,
    )


def to_nearby_charging_station_response(
    station: ChargingStationModel, *, connector_count: int, distance_km: float
) -> NearbyChargingStationResponse:
    """Build a driver-facing nearby-station response (F-D1).

    Pure mapping only - the connector count and distance are computed by
    the caller, since a pure mapper must never do I/O.

    Args:
        station: Station ORM object queried by the repository.
        connector_count: Number of active connectors, already computed by
            the caller.
        distance_km: Distance from the query point, already computed by
            the caller.

    Returns:
        Driver-facing response schema, without the internal/admin fields
        ``ChargingStationResponse`` carries.
    """
    latitude, longitude = location_to_coordinates(station.location)
    return NearbyChargingStationResponse(
        station_id=station.station_id,
        display_name=station.display_name,
        latitude=latitude,
        longitude=longitude,
        power_rating_kw=(
            float(station.power_rating_kw)
            if station.power_rating_kw is not None
            else None
        ),
        connector_standard=station.connector_standard,
        operating_hours=station.operating_hours,
        maintenance_status=station.maintenance_status,
        connector_count=connector_count,
        distance_km=distance_km,
    )


async def find_nearby_charging_stations(
    db: AsyncSession,
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    connector_standard: str | None = None,
    min_power_kw: float | None = None,
    is_operational_only: bool = True,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> NearbyChargingStationListResponse:
    """Find stations within a radius of a point, filtered and paginated (F-D1).

    Generalizes ``find_nearest_operational_station`` (F-A2) from "1
    nearest" to "N within a radius, filtered by connector standard/power,
    nearest first".

    Args:
        db: Async session owned by the HTTP boundary.
        latitude: GPS latitude in decimal degrees of the query point.
        longitude: GPS longitude in decimal degrees of the query point.
        radius_km: Search radius in km; clamped to
            ``(0, settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM]``.
        connector_standard: Exact-match filter, or ``None`` to not filter.
        min_power_kw: Minimum power rating filter, or ``None`` to not
            filter.
        is_operational_only: Whether to only return stations with
            ``maintenance_status == OPERATIONAL`` - same approximation of
            "available" as F-A2 (see ``NearestChargingStation``'s
            docstring and ``docs/01-requirements/future.md``).
        page: Page number starting at one; lower values are clamped to the
            default.
        page_size: Page size, clamped according to settings.

    Returns:
        Matching stations nearest-first, and pagination metadata.

    Side Effects:
        Performs two read queries plus one connector-count query per
        station on the page - the same deliberate, deferred N+1 as
        ``list_charging_stations`` (see ``docs/01-requirements/future.md``
        item 34); does not commit or rollback.
    """
    # radius_km > 0 is enforced by the router's Query validation; clamp only
    # the upper bound here so a non-HTTP caller can't request an unbounded
    # PostGIS scan, and floor negative/zero input rather than passing it
    # straight to ST_DWithin.
    radius_km = min(
        max(radius_km, 0.001), settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM
    )
    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(
        max(page_size, settings.API_DEFAULT_PAGE_SIZE), settings.API_MAX_PAGE_SIZE
    )
    offset = (page - 1) * page_size

    query_point = coordinates_to_location(latitude, longitude)
    assert query_point is not None, "latitude/longitude are both required here"
    radius_meters = radius_km * 1000
    min_power_decimal = Decimal(str(min_power_kw)) if min_power_kw is not None else None

    matches = await repository.list_nearby_stations(
        db,
        location=query_point,
        radius_meters=radius_meters,
        connector_standard=connector_standard,
        min_power_kw=min_power_decimal,
        operational_only=is_operational_only,
        offset=offset,
        limit=page_size,
    )
    total = await repository.count_nearby_stations(
        db,
        location=query_point,
        radius_meters=radius_meters,
        connector_standard=connector_standard,
        min_power_kw=min_power_decimal,
        operational_only=is_operational_only,
    )
    items = []
    for station, distance_meters in matches:
        connector_count = await repository.count_connectors_by_station_id(
            db, station.station_id
        )
        items.append(
            to_nearby_charging_station_response(
                station,
                connector_count=connector_count,
                distance_km=distance_meters / 1000,
            )
        )
    return NearbyChargingStationListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


async def resolve_ocpp_topology(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    ocpp_evse_id: int,
    ocpp_connector_id: int,
) -> tuple[UUID, UUID, UUID]:
    """Resolve OCPP topology into internal UUID primitives for the adapter.

    Args:
        db: Async session owned by the OCPP entry boundary.
        ocpp_identity: Station identity from the WebSocket path.
        ocpp_evse_id: EVSE ID in the OCPP message.
        ocpp_connector_id: Connector ID in the OCPP message.

    Returns:
        Tuple ``(station_id, evse_id, connector_id)`` to pass to the
        ``charging_sessions`` domain without exposing the ORM model.

    Raises:
        ChargingStationNotFoundError: If the station has not been
            pre-provisioned or was soft-deleted.
        ChargingEvseNotFoundError: If the EVSE does not belong to an active
            station.
        ChargingConnectorNotFoundError: If the connector does not belong to
            an active EVSE.
    """
    station = await repository.get_station_by_identity(
        db, ocpp_identity, include_deleted=False
    )
    if station is None:
        raise ChargingStationNotFoundError(f"OCPP station '{ocpp_identity}' not found")

    evse = await repository.get_evse_by_identity(
        db, station.station_id, ocpp_evse_id, include_deleted=False
    )
    if evse is None:
        raise ChargingEvseNotFoundError(
            f"OCPP EVSE '{ocpp_evse_id}' not found in station"
        )

    connector = await repository.get_connector_by_identity(
        db, evse.evse_id, ocpp_connector_id, include_deleted=False
    )
    if connector is None:
        raise ChargingConnectorNotFoundError(
            f"OCPP connector '{ocpp_connector_id}' not found in EVSE"
        )
    return station.station_id, evse.evse_id, connector.connector_id


async def update_charging_station(
    db: AsyncSession, station_id: UUID, station_data: ChargingStationUpdateRequest
) -> ChargingStationResponse:
    """PATCH a station and check for identity conflicts before flushing.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station to update.
        station_data: PATCH fields, already Pydantic-validated.

    Returns:
        The updated station response.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            deleted.
        ChargingTopologyConflictError: If the new identity is already in
            use.
    """
    station = await repository.get_station_by_id(db, station_id)
    if station is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    if (
        station_data.ocpp_identity is not None
        and station_data.ocpp_identity != station.ocpp_identity
        and await repository.get_station_by_identity(db, station_data.ocpp_identity)
    ):
        raise ChargingTopologyConflictError(
            f"OCPP identity '{station_data.ocpp_identity}' already exists"
        )

    update_data = _clean_update_values(
        station_data.model_dump(
            exclude_unset=True, exclude={"latitude", "longitude", "power_rating_kw"}
        )
    )
    # latitude/longitude map to one DB column (location) and power_rating_kw
    # needs a float->Decimal conversion - both handled separately from the
    # generic _clean_update_values pass above.
    if station_data.latitude is not None and station_data.longitude is not None:
        update_data["location"] = coordinates_to_location(
            station_data.latitude, station_data.longitude
        )
    if station_data.power_rating_kw is not None:
        update_data["power_rating_kw"] = Decimal(str(station_data.power_rating_kw))
    if not update_data:
        connector_count = await repository.count_connectors_by_station_id(
            db, station_id
        )
        return to_charging_station_response(station, connector_count=connector_count)
    try:
        updated = await repository.update_charging_station(db, station_id, update_data)
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Station OCPP identity already exists"
        ) from error
    if updated is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    connector_count = await repository.count_connectors_by_station_id(db, station_id)
    return to_charging_station_response(updated, connector_count=connector_count)


async def soft_delete_charging_station(
    db: AsyncSession, station_id: UUID
) -> ChargingResourceDeleteResponse:
    """Soft-delete a station and its child topology within the same transaction.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station to soft-delete.

    Returns:
        Confirmation message for the soft-delete.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            deleted.

    Side Effects:
        Marks the station, its EVSEs, and its connectors with
        ``deleted_at``; does not physically delete records and does not
        commit on its own.
    """
    if not await repository.soft_delete_station(db, station_id):
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    return ChargingResourceDeleteResponse(message="Charging station soft-deleted")


async def create_charging_evse(
    db: AsyncSession, station_id: UUID, evse_data: ChargingEvseCreateRequest
) -> ChargingEvseResponse:
    """Create an EVSE only if the parent station is active and the identity is unused.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the parent station.
        evse_data: EVSE identity, already validated.

    Returns:
        The newly created EVSE response.

    Raises:
        ChargingStationNotFoundError: If the parent station is not active.
        ChargingTopologyConflictError: If the EVSE identity already exists.
    """
    if await repository.get_station_by_id(db, station_id) is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    if await repository.get_evse_by_identity(db, station_id, evse_data.ocpp_evse_id):
        raise ChargingTopologyConflictError(
            f"EVSE ID '{evse_data.ocpp_evse_id}' already exists in station"
        )
    try:
        evse = await repository.create_charging_evse(
            db,
            station_id=station_id,
            ocpp_evse_id=evse_data.ocpp_evse_id,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "EVSE identity already exists in station"
        ) from error
    return to_charging_evse_response(evse)


async def list_charging_evses(
    db: AsyncSession,
    station_id: UUID,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingEvseListResponse:
    """List active EVSEs belonging to the parent station.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the parent station.
        page: Page number starting at one.
        page_size: Page size, bounded by settings.

    Returns:
        List of EVSEs and pagination metadata.

    Raises:
        ChargingStationNotFoundError: If the parent station is not active.
    """
    if await repository.get_station_by_id(db, station_id) is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(
        max(page_size, settings.API_DEFAULT_PAGE_SIZE), settings.API_MAX_PAGE_SIZE
    )
    evses = await repository.list_charging_evses(
        db, station_id=station_id, offset=(page - 1) * page_size, limit=page_size
    )
    total = await repository.count_evses(db, station_id)
    return ChargingEvseListResponse(
        items=[to_charging_evse_response(evse) for evse in evses],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_charging_evse(db: AsyncSession, evse_id: UUID) -> ChargingEvseResponse:
    """Get an active EVSE by internal UUID.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the EVSE to query.

    Returns:
        The active EVSE response.

    Raises:
        ChargingEvseNotFoundError: If the EVSE does not exist or was
            deleted.
    """
    evse = await repository.get_evse_by_id(db, evse_id)
    if evse is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    return to_charging_evse_response(evse)


async def update_charging_evse(
    db: AsyncSession, evse_id: UUID, evse_data: ChargingEvseUpdateRequest
) -> ChargingEvseResponse:
    """PATCH an EVSE while keeping its identity unique within the parent station.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the EVSE to update.
        evse_data: PATCH fields, already validated.

    Returns:
        The updated EVSE response.

    Raises:
        ChargingEvseNotFoundError: If the EVSE is not active.
        ChargingTopologyConflictError: If the new identity conflicts within
            the station.
    """
    evse = await repository.get_evse_by_id(db, evse_id)
    if evse is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    if (
        evse_data.ocpp_evse_id is not None
        and evse_data.ocpp_evse_id != evse.ocpp_evse_id
        and await repository.get_evse_by_identity(
            db, evse.station_id, evse_data.ocpp_evse_id
        )
    ):
        raise ChargingTopologyConflictError(
            f"EVSE ID '{evse_data.ocpp_evse_id}' already exists in station"
        )
    update_data = _clean_update_values(evse_data.model_dump(exclude_unset=True))
    if not update_data:
        return to_charging_evse_response(evse)
    try:
        updated = await repository.update_charging_evse(db, evse_id, update_data)
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "EVSE identity already exists in station"
        ) from error
    if updated is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    return to_charging_evse_response(updated)


async def soft_delete_charging_evse(
    db: AsyncSession, evse_id: UUID
) -> ChargingResourceDeleteResponse:
    """Soft-delete an EVSE and its child connectors.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the EVSE to soft-delete.

    Returns:
        Confirmation message for the soft-delete.

    Raises:
        ChargingEvseNotFoundError: If the EVSE is not active.

    Side Effects:
        Marks the EVSE and its child connectors; does not physically delete
        and does not commit.
    """
    if not await repository.soft_delete_evse(db, evse_id):
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    return ChargingResourceDeleteResponse(message="EVSE soft-deleted")


async def create_charging_connector(
    db: AsyncSession, evse_id: UUID, connector_data: ChargingConnectorCreateRequest
) -> ChargingConnectorResponse:
    """Create a connector only if the parent EVSE is active and the identity is unused.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the parent EVSE.
        connector_data: Connector identity, already validated.

    Returns:
        The newly created connector response.

    Raises:
        ChargingEvseNotFoundError: If the parent EVSE is not active.
        ChargingTopologyConflictError: If the connector identity already
            exists.
    """
    if await repository.get_evse_by_id(db, evse_id) is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    if await repository.get_connector_by_identity(
        db, evse_id, connector_data.ocpp_connector_id
    ):
        raise ChargingTopologyConflictError(
            f"Connector ID '{connector_data.ocpp_connector_id}' already exists in EVSE"
        )
    try:
        connector = await repository.create_charging_connector(
            db,
            evse_id=evse_id,
            ocpp_connector_id=connector_data.ocpp_connector_id,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Connector identity already exists in EVSE"
        ) from error
    return to_charging_connector_response(connector)


async def list_charging_connectors(
    db: AsyncSession,
    evse_id: UUID,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingConnectorListResponse:
    """List active connectors belonging to the parent EVSE.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the parent EVSE.
        page: Page number starting at one.
        page_size: Page size, bounded by settings.

    Returns:
        List of connectors and pagination metadata.

    Raises:
        ChargingEvseNotFoundError: If the parent EVSE is not active.
    """
    if await repository.get_evse_by_id(db, evse_id) is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(
        max(page_size, settings.API_DEFAULT_PAGE_SIZE), settings.API_MAX_PAGE_SIZE
    )
    connectors = await repository.list_charging_connectors(
        db, evse_id=evse_id, offset=(page - 1) * page_size, limit=page_size
    )
    total = await repository.count_connectors(db, evse_id)
    return ChargingConnectorListResponse(
        items=[to_charging_connector_response(connector) for connector in connectors],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_charging_connector(
    db: AsyncSession, connector_id: UUID
) -> ChargingConnectorResponse:
    """Get an active connector by internal UUID.

    Args:
        db: Async session owned by the HTTP boundary.
        connector_id: UUID of the connector to query.

    Returns:
        The active connector response.

    Raises:
        ChargingConnectorNotFoundError: If the connector does not exist or
            was deleted.
    """
    connector = await repository.get_connector_by_id(db, connector_id)
    if connector is None:
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    return to_charging_connector_response(connector)


async def update_charging_connector(
    db: AsyncSession, connector_id: UUID, connector_data: ChargingConnectorUpdateRequest
) -> ChargingConnectorResponse:
    """PATCH a connector while keeping its identity unique within the parent EVSE.

    Args:
        db: Async session owned by the HTTP boundary.
        connector_id: UUID of the connector to update.
        connector_data: PATCH fields, already validated.

    Returns:
        The updated connector response.

    Raises:
        ChargingConnectorNotFoundError: If the connector is not active.
        ChargingTopologyConflictError: If the new identity conflicts within
            the EVSE.
    """
    connector = await repository.get_connector_by_id(db, connector_id)
    if connector is None:
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    if (
        connector_data.ocpp_connector_id is not None
        and connector_data.ocpp_connector_id != connector.ocpp_connector_id
        and await repository.get_connector_by_identity(
            db, connector.evse_id, connector_data.ocpp_connector_id
        )
    ):
        raise ChargingTopologyConflictError(
            f"Connector ID '{connector_data.ocpp_connector_id}' already exists in EVSE"
        )
    update_data = _clean_update_values(connector_data.model_dump(exclude_unset=True))
    if not update_data:
        return to_charging_connector_response(connector)
    try:
        updated = await repository.update_charging_connector(
            db, connector_id, update_data
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Connector identity already exists in EVSE"
        ) from error
    if updated is None:
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    return to_charging_connector_response(updated)


async def update_connector_status(
    db: AsyncSession,
    *,
    connector_id: UUID,
    status: ChargingConnectorStatus,
    status_updated_at: datetime,
) -> None:
    """Record a connector's live status from an OCPP ``StatusNotification``. Public entry point for F-C2.

    Args:
        db: Async session owned by the caller's entry boundary (the OCPP
            gateway's own transaction).
        connector_id: UUID of the connector the station reported on.
        status: New live status.
        status_updated_at: Timestamp the station reported, already parsed
            and normalized to UTC.

    Raises:
        ChargingConnectorNotFoundError: If the connector is not active.
    """
    updated = await repository.update_connector_status(
        db,
        connector_id,
        status=status,
        status_updated_at=status_updated_at,
    )
    if updated is None:
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")


async def soft_delete_charging_connector(
    db: AsyncSession, connector_id: UUID
) -> ChargingResourceDeleteResponse:
    """Soft-delete a connector.

    Args:
        db: Async session owned by the HTTP boundary.
        connector_id: UUID of the connector to soft-delete.

    Returns:
        Confirmation message for the soft-delete.

    Raises:
        ChargingConnectorNotFoundError: If the connector is not active.

    Side Effects:
        Marks ``deleted_at`` within the current transaction; does not commit
        on its own.
    """
    if not await repository.soft_delete_connector(db, connector_id):
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    return ChargingResourceDeleteResponse(message="Connector soft-deleted")
