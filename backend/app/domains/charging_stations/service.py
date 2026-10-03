"""Public service of the charging_stations domain.

Holds topology CRUD with soft-delete (station -> EVSE -> connector, F-C1), the
directory reads (station list/detail with the derived ``is_online`` and
``available_connector_count``), the station status view (whole charger plus
every gun, F-C2), the nearest-available-station lookup other domains call
(F-A2, used by ``telemetry``), the driver-facing nearby search (F-D1), the
all-stations energy report (F-C5, which asks ``charging_sessions`` for each
station's total), and the read of the latest configuration a charger
reported. This is the only module another domain may import; the OCPP
gateway's own writes live in the internal ``ocpp_state_service.py``.

"Available" (decision D3 of the happy-path completion planner) means: not
soft-deleted, ``maintenance_status == OPERATIONAL`` and at least one connector
whose last reported status is ``Available``; ``is_online`` is not required.

Pre-provisioning invariants are enforced here: a station must exist before an
EVSE, an EVSE must belong to a station before a connector, and topology
identities are never reused, even after the old record was soft-deleted.
Functions run inside the caller's transaction (FastAPI's ``get_db`` for HTTP,
the ingestion worker's for ``find_nearest_operational_station``) and never
commit or roll back.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingEvseNotFoundError,
    ChargingStationNotFoundError,
    ChargingStationReportRangeError,
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
    ChargingStationConfigurationEntryResponse,
    ChargingStationConfigurationResponse,
    ChargingStationConnectorStatusResponse,
    ChargingStationCreateRequest,
    ChargingStationEnergyTotalListResponse,
    ChargingStationEnergyTotalResponse,
    ChargingStationListResponse,
    ChargingStationResponse,
    ChargingStationStatusResponse,
    ChargingStationUpdateRequest,
    NearbyChargingStationListResponse,
    NearbyChargingStationResponse,
)
from app.domains.charging_stations.types import (
    ChargingStationMaintenanceStatus,
    NearestChargingStationReference,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location, location_to_coordinates
from app.libs.common.pagination import normalize_page_window


def _to_optional_float(value: Decimal | None) -> float | None:
    """Convert a nullable ``Numeric`` column value into the float the API uses.

    Args:
        value: The stored decimal (e.g. ``power_rating_kw``), or ``None``.

    Returns:
        ``float(value)``, or ``None`` when no value is stored.
    """
    return float(value) if value is not None else None


def _is_station_online(station: ChargingStationModel, checked_at: datetime) -> bool:
    """Derive whether a charger is online at a given time.

    Args:
        station: Station ORM object carrying ``last_seen_at``.
        checked_at: Reference time of the check.

    Returns:
        ``True`` if the latest frame of any kind arrived within
        ``CHARGING_OFFLINE_TIMEOUT_SECONDS`` of ``checked_at``; ``False`` for
        a station that has never connected.
    """
    return station.last_seen_at is not None and (
        checked_at - station.last_seen_at
    ) <= timedelta(seconds=settings.CHARGING_OFFLINE_TIMEOUT_SECONDS)


def to_charging_station_response(
    station: ChargingStationModel,
    *,
    connector_count: int,
    available_connector_count: int,
    now: datetime | None = None,
) -> ChargingStationResponse:
    """Build a station response from the topology model and its connector counts.

    Pure mapping only — the connector counts are computed by the caller (via
    repository queries) rather than here, since a pure mapper must never do
    I/O. The only outside input is the clock, needed to derive ``is_online``;
    it can be injected through ``now`` (tests do).

    Args:
        station: Station ORM object queried or created by the repository.
        connector_count: Number of active connectors across the station's
            active EVSEs, already computed by the caller.
        available_connector_count: How many of them last reported
            ``Available``, already computed by the caller.
        now: Reference time for the online check; defaults to the current
            UTC time.

    Returns:
        Response schema including directory metadata, connector counts, the
        charger's device info, and ``is_online`` (``last_seen_at`` within
        ``CHARGING_OFFLINE_TIMEOUT_SECONDS``; ``False`` if never seen).
    """
    checked_at = now if now is not None else utc_now()
    latitude, longitude = location_to_coordinates(station.location)
    return ChargingStationResponse(
        station_id=station.station_id,
        ocpp_identity=station.ocpp_identity,
        display_name=station.display_name,
        latitude=latitude,
        longitude=longitude,
        power_rating_kw=_to_optional_float(station.power_rating_kw),
        connector_standard=station.connector_standard,
        operating_hours=station.operating_hours,
        maintenance_status=station.maintenance_status,
        connector_count=connector_count,
        available_connector_count=available_connector_count,
        ocpp_protocol_version=station.ocpp_protocol_version,
        vendor=station.vendor,
        model=station.model,
        serial_number=station.serial_number,
        firmware_version=station.firmware_version,
        last_boot_at=station.last_boot_at,
        last_seen_at=station.last_seen_at,
        charger_status=station.charger_status,
        charger_status_updated_at=station.charger_status_updated_at,
        charger_error_code=station.charger_error_code,
        charger_vendor_error_code=station.charger_vendor_error_code,
        is_online=_is_station_online(station, checked_at),
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


async def _build_charging_station_response(
    db: AsyncSession, station: ChargingStationModel
) -> ChargingStationResponse:
    """Count a station's connectors and build its response.

    Args:
        db: Async session owned by the caller's entry boundary.
        station: Station ORM object already loaded by the caller.

    Returns:
        The station response with ``connector_count`` and
        ``available_connector_count`` filled in.

    Side Effects:
        Runs two count queries (all connectors, ``Available`` connectors);
        does not commit or roll back.
    """
    connector_count = await charging_stations_repository.count_connectors_by_station_id(
        db, station.station_id
    )
    available_connector_count = (
        await charging_stations_repository.count_available_connectors_by_station_id(
            db, station.station_id
        )
    )
    return to_charging_station_response(
        station,
        connector_count=connector_count,
        available_connector_count=available_connector_count,
    )


def _clean_update_values(requested_fields: Mapping[str, object]) -> dict[str, object]:
    """Drop ``None`` fields per the backend's PATCH convention.

    Args:
        requested_fields: Mapping from ``model_dump(exclude_unset=True)``.

    Returns:
        Mapping containing only the fields with values to update.

    Note:
        This domain has no dedicated contract yet for clearing a nullable
        value with ``null``; therefore ``None`` is treated as "do not
        update".
    """
    return {
        field_name: value
        for field_name, value in requested_fields.items()
        if value is not None
    }


async def create_charging_station(
    db: AsyncSession, station_create_request: ChargingStationCreateRequest
) -> ChargingStationResponse:
    """Create a new station after checking the OCPP identity table-wide.

    Args:
        db: Async session owned by the HTTP boundary.
        station_create_request: Station data, already Pydantic-validated.

    Returns:
        The newly created station response.

    Raises:
        ChargingTopologyConflictError: If the identity already exists, even
            if soft-deleted.
    """
    if await charging_stations_repository.get_station_by_identity(
        db, station_create_request.ocpp_identity
    ):
        raise ChargingTopologyConflictError(
            f"OCPP identity '{station_create_request.ocpp_identity}' already exists"
        )
    maintenance_status = (
        station_create_request.maintenance_status
        if station_create_request.maintenance_status is not None
        else ChargingStationMaintenanceStatus.OPERATIONAL
    )
    power_rating_kw = (
        Decimal(str(station_create_request.power_rating_kw))
        if station_create_request.power_rating_kw is not None
        else None
    )
    try:
        station = await charging_stations_repository.create_charging_station(
            db,
            ocpp_identity=station_create_request.ocpp_identity,
            display_name=station_create_request.display_name,
            location=coordinates_to_location(
                station_create_request.latitude, station_create_request.longitude
            ),
            power_rating_kw=power_rating_kw,
            connector_standard=station_create_request.connector_standard,
            operating_hours=station_create_request.operating_hours,
            maintenance_status=maintenance_status,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Station OCPP identity already exists"
        ) from error
    # A brand-new station has no EVSEs/connectors yet - no query needed.
    return to_charging_station_response(
        station, connector_count=0, available_connector_count=0
    )


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
        Performs two read queries plus two connector-count queries per
        station on the page (no batching yet - deliberately deferred until
        throughput needs it, see ``docs/decisions/deferred.md``); does
        not commit or rollback.
    """
    page_window = normalize_page_window(page, page_size)
    stations = await charging_stations_repository.list_charging_stations(
        db,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await charging_stations_repository.count_stations(db)
    items = [
        await _build_charging_station_response(db, station) for station in stations
    ]
    return ChargingStationListResponse(
        items=items,
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
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
    station = await charging_stations_repository.get_station_by_id(db, station_id)
    if station is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    return await _build_charging_station_response(db, station)


async def get_charging_station_status(
    db: AsyncSession, station_id: UUID
) -> ChargingStationStatusResponse:
    """Get the whole charger's status and every gun's status of a station (F-C2).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station.

    Returns:
        The connector-``0`` status fields of the station and one entry per
        active connector, ordered by OCPP EVSE number, then connector number.
        Statuses are returned as last reported, even if the charger is
        offline.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            soft-deleted.

    Side Effects:
        Performs two read queries; does not commit or roll back.
    """
    station = await charging_stations_repository.get_station_by_id(db, station_id)
    if station is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    connectors = await charging_stations_repository.list_connectors_by_station_id(
        db, station_id
    )
    return ChargingStationStatusResponse(
        station_id=station.station_id,
        charger_status=station.charger_status,
        charger_status_updated_at=station.charger_status_updated_at,
        charger_error_code=station.charger_error_code,
        charger_vendor_error_code=station.charger_vendor_error_code,
        connectors=[
            ChargingStationConnectorStatusResponse(
                connector_id=connector.connector_id,
                evse_id=connector.evse_id,
                ocpp_evse_id=ocpp_evse_id,
                ocpp_connector_id=connector.ocpp_connector_id,
                status=connector.status,
                status_updated_at=connector.status_updated_at,
                error_code=connector.error_code,
                vendor_error_code=connector.vendor_error_code,
                status_info=connector.status_info,
            )
            for connector, ocpp_evse_id in connectors
        ],
    )


async def find_nearest_operational_station(
    db: AsyncSession, *, latitude: float, longitude: float
) -> NearestChargingStationReference | None:
    """Find the nearest available station to a point. Public entry point for F-A2.

    Behaviour change (2026-10-01, decision D3 of the happy-path completion
    planner): a station now also needs at least one connector whose last
    reported status is ``Available`` - before, the admin-set
    ``maintenance_status`` alone decided. A station whose guns are all busy,
    faulted or have never reported is skipped in favour of the next nearest.
    The charger's ``is_online`` is deliberately still not consulted (D3):
    a connector's last reported status is the availability signal.

    Args:
        db: Async session owned by the caller's entry boundary (e.g. the
            telemetry ingestion worker's transaction).
        latitude: GPS latitude in decimal degrees of the query point.
        longitude: GPS longitude in decimal degrees of the query point.

    Returns:
        A minimal reference DTO for the nearest station that is not
        soft-deleted, ``OPERATIONAL``, has a known location and at least one
        ``Available`` connector - never the ORM model - or ``None`` if none
        qualifies.
    """
    query_point = coordinates_to_location(latitude, longitude)
    assert query_point is not None, "latitude/longitude are both required here"
    match = await charging_stations_repository.find_nearest_station_by_location(
        db, query_point
    )
    if match is None:
        return None
    station, distance_meters = match
    station_latitude, station_longitude = location_to_coordinates(station.location)
    assert station_latitude is not None and station_longitude is not None, (
        "query filters out stations with a NULL location"
    )
    return NearestChargingStationReference(
        station_id=station.station_id,
        display_name=station.display_name,
        latitude=station_latitude,
        longitude=station_longitude,
        distance_km=distance_meters / 1000,
    )


def to_nearby_charging_station_response(
    station: ChargingStationModel,
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
    latitude, longitude = location_to_coordinates(station.location)
    return NearbyChargingStationResponse(
        station_id=station.station_id,
        display_name=station.display_name,
        latitude=latitude,
        longitude=longitude,
        power_rating_kw=_to_optional_float(station.power_rating_kw),
        connector_standard=station.connector_standard,
        operating_hours=station.operating_hours,
        maintenance_status=station.maintenance_status,
        connector_count=connector_count,
        available_connector_count=available_connector_count,
        is_online=_is_station_online(station, checked_at),
        distance_km=distance_km,
    )


async def list_nearby_charging_stations(
    db: AsyncSession,
    *,
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
            ``maintenance_status == OPERATIONAL`` (admin-set).
        is_available_only: Whether to only return available stations:
            ``OPERATIONAL`` and at least one connector whose last reported
            status is ``Available`` - the same rule as F-A2; ``is_online``
            is not required. Implies ``is_operational_only``.
        page: Page number starting at one; lower values are clamped to the
            default.
        page_size: Page size, clamped according to settings.

    Returns:
        Matching stations nearest-first, and pagination metadata.

    Side Effects:
        Performs two read queries plus two connector-count queries per
        station on the page - the same deliberate, deferred N+1 as
        ``list_charging_stations`` (see ``docs/decisions/deferred.md``
        item 34); does not commit or rollback.
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

    matches = await charging_stations_repository.list_nearby_stations(
        db,
        location=query_point,
        radius_meters=radius_meters,
        connector_standard=connector_standard,
        min_power_kw=min_power_decimal,
        is_operational_only=is_operational_only,
        is_available_only=is_available_only,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await charging_stations_repository.count_nearby_stations(
        db,
        location=query_point,
        radius_meters=radius_meters,
        connector_standard=connector_standard,
        min_power_kw=min_power_decimal,
        is_operational_only=is_operational_only,
        is_available_only=is_available_only,
    )
    items = []
    for station, distance_meters in matches:
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


async def update_charging_station(
    db: AsyncSession,
    station_id: UUID,
    station_update_request: ChargingStationUpdateRequest,
) -> ChargingStationResponse:
    """PATCH a station and check for identity conflicts before flushing.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station to update.
        station_update_request: PATCH fields, already Pydantic-validated.

    Returns:
        The updated station response.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            deleted.
        ChargingTopologyConflictError: If the new identity is already in
            use.
    """
    station = await charging_stations_repository.get_station_by_id(db, station_id)
    if station is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    if (
        station_update_request.ocpp_identity is not None
        and station_update_request.ocpp_identity != station.ocpp_identity
        and await charging_stations_repository.get_station_by_identity(
            db, station_update_request.ocpp_identity
        )
    ):
        raise ChargingTopologyConflictError(
            f"OCPP identity '{station_update_request.ocpp_identity}' already exists"
        )

    update_data = _clean_update_values(
        station_update_request.model_dump(
            exclude_unset=True, exclude={"latitude", "longitude", "power_rating_kw"}
        )
    )
    # latitude/longitude map to one DB column (location) and power_rating_kw
    # needs a float->Decimal conversion - both handled separately from the
    # generic _clean_update_values pass above.
    if (
        station_update_request.latitude is not None
        and station_update_request.longitude is not None
    ):
        update_data["location"] = coordinates_to_location(
            station_update_request.latitude, station_update_request.longitude
        )
    if station_update_request.power_rating_kw is not None:
        update_data["power_rating_kw"] = Decimal(
            str(station_update_request.power_rating_kw)
        )
    if not update_data:
        return await _build_charging_station_response(db, station)
    try:
        updated = await charging_stations_repository.update_charging_station(
            db, station_id, update_data
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Station OCPP identity already exists"
        ) from error
    if updated is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    return await _build_charging_station_response(db, updated)


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
    if not await charging_stations_repository.soft_delete_station(db, station_id):
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    return ChargingResourceDeleteResponse(message="Charging station soft-deleted")


async def create_charging_evse(
    db: AsyncSession, station_id: UUID, evse_create_request: ChargingEvseCreateRequest
) -> ChargingEvseResponse:
    """Create an EVSE only if the parent station is active and the identity is unused.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the parent station.
        evse_create_request: EVSE identity, already validated.

    Returns:
        The newly created EVSE response.

    Raises:
        ChargingStationNotFoundError: If the parent station is not active.
        ChargingTopologyConflictError: If the EVSE identity already exists.
    """
    if await charging_stations_repository.get_station_by_id(db, station_id) is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    if await charging_stations_repository.get_evse_by_identity(
        db, station_id, evse_create_request.ocpp_evse_id
    ):
        raise ChargingTopologyConflictError(
            f"EVSE ID '{evse_create_request.ocpp_evse_id}' already exists in station"
        )
    try:
        evse = await charging_stations_repository.create_charging_evse(
            db,
            station_id=station_id,
            ocpp_evse_id=evse_create_request.ocpp_evse_id,
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
    if await charging_stations_repository.get_station_by_id(db, station_id) is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    page_window = normalize_page_window(page, page_size)
    evses = await charging_stations_repository.list_charging_evses(
        db,
        station_id=station_id,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await charging_stations_repository.count_evses(db, station_id)
    return ChargingEvseListResponse(
        items=[to_charging_evse_response(evse) for evse in evses],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
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
    evse = await charging_stations_repository.get_evse_by_id(db, evse_id)
    if evse is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    return to_charging_evse_response(evse)


async def update_charging_evse(
    db: AsyncSession, evse_id: UUID, evse_update_request: ChargingEvseUpdateRequest
) -> ChargingEvseResponse:
    """PATCH an EVSE while keeping its identity unique within the parent station.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the EVSE to update.
        evse_update_request: PATCH fields, already validated.

    Returns:
        The updated EVSE response.

    Raises:
        ChargingEvseNotFoundError: If the EVSE is not active.
        ChargingTopologyConflictError: If the new identity conflicts within
            the station.
    """
    evse = await charging_stations_repository.get_evse_by_id(db, evse_id)
    if evse is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    if (
        evse_update_request.ocpp_evse_id is not None
        and evse_update_request.ocpp_evse_id != evse.ocpp_evse_id
        and await charging_stations_repository.get_evse_by_identity(
            db, evse.station_id, evse_update_request.ocpp_evse_id
        )
    ):
        raise ChargingTopologyConflictError(
            f"EVSE ID '{evse_update_request.ocpp_evse_id}' already exists in station"
        )
    update_data = _clean_update_values(
        evse_update_request.model_dump(exclude_unset=True)
    )
    if not update_data:
        return to_charging_evse_response(evse)
    try:
        updated = await charging_stations_repository.update_charging_evse(
            db, evse_id, update_data
        )
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
    if not await charging_stations_repository.soft_delete_evse(db, evse_id):
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    return ChargingResourceDeleteResponse(message="EVSE soft-deleted")


async def create_charging_connector(
    db: AsyncSession,
    evse_id: UUID,
    connector_create_request: ChargingConnectorCreateRequest,
) -> ChargingConnectorResponse:
    """Create a connector only if the parent EVSE is active and the identity is unused.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the parent EVSE.
        connector_create_request: Connector identity, already validated.

    Returns:
        The newly created connector response.

    Raises:
        ChargingEvseNotFoundError: If the parent EVSE is not active.
        ChargingTopologyConflictError: If the connector identity already
            exists.
    """
    if await charging_stations_repository.get_evse_by_id(db, evse_id) is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    if await charging_stations_repository.get_connector_by_identity(
        db, evse_id, connector_create_request.ocpp_connector_id
    ):
        raise ChargingTopologyConflictError(
            f"Connector ID '{connector_create_request.ocpp_connector_id}' "
            "already exists in EVSE"
        )
    try:
        connector = await charging_stations_repository.create_charging_connector(
            db,
            evse_id=evse_id,
            ocpp_connector_id=connector_create_request.ocpp_connector_id,
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
    if await charging_stations_repository.get_evse_by_id(db, evse_id) is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    page_window = normalize_page_window(page, page_size)
    connectors = await charging_stations_repository.list_charging_connectors(
        db,
        evse_id=evse_id,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await charging_stations_repository.count_connectors(db, evse_id)
    return ChargingConnectorListResponse(
        items=[to_charging_connector_response(connector) for connector in connectors],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
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
    connector = await charging_stations_repository.get_connector_by_id(db, connector_id)
    if connector is None:
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    return to_charging_connector_response(connector)


async def update_charging_connector(
    db: AsyncSession,
    connector_id: UUID,
    connector_update_request: ChargingConnectorUpdateRequest,
) -> ChargingConnectorResponse:
    """PATCH a connector while keeping its identity unique within the parent EVSE.

    Args:
        db: Async session owned by the HTTP boundary.
        connector_id: UUID of the connector to update.
        connector_update_request: PATCH fields, already validated.

    Returns:
        The updated connector response.

    Raises:
        ChargingConnectorNotFoundError: If the connector is not active.
        ChargingTopologyConflictError: If the new identity conflicts within
            the EVSE.
    """
    connector = await charging_stations_repository.get_connector_by_id(db, connector_id)
    if connector is None:
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    if (
        connector_update_request.ocpp_connector_id is not None
        and connector_update_request.ocpp_connector_id != connector.ocpp_connector_id
        and await charging_stations_repository.get_connector_by_identity(
            db, connector.evse_id, connector_update_request.ocpp_connector_id
        )
    ):
        raise ChargingTopologyConflictError(
            f"Connector ID '{connector_update_request.ocpp_connector_id}' "
            "already exists in EVSE"
        )
    update_data = _clean_update_values(
        connector_update_request.model_dump(exclude_unset=True)
    )
    if not update_data:
        return to_charging_connector_response(connector)
    try:
        updated = await charging_stations_repository.update_charging_connector(
            db, connector_id, update_data
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Connector identity already exists in EVSE"
        ) from error
    if updated is None:
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    return to_charging_connector_response(updated)


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
    if not await charging_stations_repository.soft_delete_connector(db, connector_id):
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    return ChargingResourceDeleteResponse(message="Connector soft-deleted")


async def get_latest_station_configuration(
    db: AsyncSession, station_id: UUID
) -> ChargingStationConfigurationResponse:
    """Get the latest configuration a charger reported.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station.

    Returns:
        The newest capture's keys sorted by name, or an empty response with
        ``null`` capture fields if the charger has not reported yet.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            soft-deleted.

    Side Effects:
        Performs up to three read queries; does not commit.
    """
    station = await charging_stations_repository.get_station_by_id(db, station_id)
    if station is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    latest = await ocpp_state_repository.get_latest_configuration_capture(
        db, station_id
    )
    if latest is None:
        return ChargingStationConfigurationResponse(
            station_id=station_id, capture_id=None, captured_at=None, items=[]
        )
    capture_id, captured_at = latest
    rows = await ocpp_state_repository.list_configuration_entries_by_capture_id(
        db, station_id, capture_id
    )
    return ChargingStationConfigurationResponse(
        station_id=station_id,
        capture_id=capture_id,
        captured_at=captured_at,
        items=[
            ChargingStationConfigurationEntryResponse.model_validate(row)
            for row in rows
        ],
    )


def _normalize_report_bound(value: datetime, field_name: str) -> datetime:
    """Require a timezone-aware report bound and convert it to UTC.

    Args:
        value: The bound from the request.
        field_name: Name used in the error message.

    Returns:
        ``value`` in UTC.

    Raises:
        ChargingStationReportRangeError: If ``value`` has no timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ChargingStationReportRangeError(f"{field_name} must have a timezone")
    return value.astimezone(timezone.utc)


async def list_station_energy_totals(
    db: AsyncSession, *, start_time: datetime, end_time: datetime
) -> ChargingStationEnergyTotalListResponse:
    """Energy sold per station within a window, for every active station (F-C5).

    Rule:
        Same semantics as the per-station summary in ``charging_sessions``
        (completed sessions whose ``ended_at`` falls within the inclusive
        window, summing ``energy_delivered_wh``), applied to every
        non-deleted station; a station with no session is listed at zero.
        Items are ranked by energy, highest first, ties by display name.
        This domain orchestrates because it owns the station directory;
        ``charging_sessions`` never calls back into it.

    Args:
        db: Async session owned by the HTTP boundary.
        start_time: Inclusive lower bound on ``ended_at``; must carry a
            timezone.
        end_time: Inclusive upper bound on ``ended_at``; must carry a
            timezone and be after ``start_time``.

    Returns:
        Per-station totals plus the grand total, in kWh.

    Raises:
        ChargingStationReportRangeError: If a bound lacks a timezone or
            ``end_time`` is not after ``start_time``.

    Side Effects:
        Performs one station query plus one energy query per station
        (deliberately unbatched, like the directory's per-station counts);
        does not commit or roll back.
    """
    normalized_start = _normalize_report_bound(start_time, "start_time")
    normalized_end = _normalize_report_bound(end_time, "end_time")
    if normalized_end <= normalized_start:
        raise ChargingStationReportRangeError("end_time must be after start_time")
    stations = await charging_stations_repository.list_active_stations(db)
    items: list[ChargingStationEnergyTotalResponse] = []
    for station in stations:
        energy_total = await charging_sessions_service.resolve_station_energy_total(
            db,
            station_id=station.station_id,
            start_time=normalized_start,
            end_time=normalized_end,
        )
        items.append(
            ChargingStationEnergyTotalResponse(
                station_id=station.station_id,
                display_name=station.display_name,
                total_energy_kwh=float(energy_total.total_energy_wh / Decimal(1000)),
                session_count=energy_total.session_count,
            )
        )
    # The repository already orders by display name; a stable sort on the
    # energy alone keeps that order among equal totals.
    items.sort(key=lambda item: item.total_energy_kwh, reverse=True)
    return ChargingStationEnergyTotalListResponse(
        start_time=normalized_start,
        end_time=normalized_end,
        total_energy_kwh=sum(item.total_energy_kwh for item in items),
        session_count=sum(item.session_count for item in items),
        items=items,
    )
