"""HTTP router for the charging station directory and its topology.

Endpoints: station/EVSE/connector CRUD with soft-delete (F-C1), the
driver-facing nearby search (F-D1), and the latest configuration a charger
reported over OCPP. The router only accepts HTTP dependencies and calls the
public service; domain exceptions are mapped to status codes centrally by
``app/api/main.py`` (each endpoint's ``Raises:`` names them). Business rules,
database queries, and transaction boundaries do not belong in this module.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.service as charging_stations_service
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
    ChargingStationConfigurationResponse,
    ChargingStationCreateRequest,
    ChargingStationListResponse,
    ChargingStationResponse,
    ChargingStationUpdateRequest,
    NearbyChargingStationListResponse,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["charging-stations"])


@router.post(
    "/charging-stations",
    status_code=status.HTTP_201_CREATED,
    response_model=ChargingStationResponse,
    summary="Create a charging station",
)
async def create_charging_station_endpoint(
    station_create_request: ChargingStationCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingStationResponse:
    """Create a pre-provisioned station.

    Args:
        station_create_request: Station creation payload, already Pydantic-validated.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response for the newly created station.

    Raises:
        ChargingTopologyConflictError: 409 if the OCPP identity already
            exists, even on a soft-deleted station.
    """
    return await charging_stations_service.create_charging_station(
        db, station_create_request
    )


@router.get(
    "/charging-stations",
    response_model=ChargingStationListResponse,
    summary="List charging stations",
)
async def list_charging_stations_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ChargingStationListResponse:
    """List active stations with pagination.

    Args:
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the list of stations.

    Raises:
        RequestValidationError: 422 (raised by FastAPI) if a query
            parameter is out of range; the service raises no domain error.
    """
    return await charging_stations_service.list_charging_stations(
        db,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/charging-stations/nearby",
    response_model=NearbyChargingStationListResponse,
    summary="Find charging stations near a point",
)
async def list_nearby_charging_stations_endpoint(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(
        ..., gt=0, le=settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM
    ),
    connector_standard: str | None = Query(None, min_length=1, max_length=20),
    min_power_kw: float | None = Query(None, gt=0),
    is_operational_only: bool = Query(True),
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> NearbyChargingStationListResponse:
    """Find stations within a radius of a point, nearest first (F-D1).

    Registered before ``GET /charging-stations/{station_id}`` so
    ``/nearby`` isn't captured by that path's ``{station_id}`` parameter.

    Args:
        latitude: GPS latitude in decimal degrees of the query point.
        longitude: GPS longitude in decimal degrees of the query point.
        radius_km: Search radius in km.
        connector_standard: Exact-match filter, e.g. ``"CCS2"``.
        min_power_kw: Minimum power rating filter.
        is_operational_only: Whether to only return operational stations.
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the matching stations, nearest first.

    Raises:
        RequestValidationError: 422 (raised by FastAPI) if a query
            parameter is out of range; the service raises no domain error.
    """
    return await charging_stations_service.list_nearby_charging_stations(
        db,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        connector_standard=connector_standard,
        min_power_kw=min_power_kw,
        is_operational_only=is_operational_only,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/charging-stations/{station_id}/evses",
    status_code=status.HTTP_201_CREATED,
    response_model=ChargingEvseResponse,
    summary="Create an EVSE belonging to a station",
)
async def create_charging_evse_endpoint(
    station_id: UUID,
    evse_create_request: ChargingEvseCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingEvseResponse:
    """Create an EVSE belonging to an active station.

    Args:
        station_id: UUID of the parent station.
        evse_create_request: EVSE identity payload.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response for the newly created EVSE.

    Raises:
        ChargingStationNotFoundError: 404 if the parent station is not
            active.
        ChargingTopologyConflictError: 409 if the EVSE identity already
            exists in the station.
    """
    return await charging_stations_service.create_charging_evse(
        db, station_id, evse_create_request
    )


@router.get(
    "/charging-stations/{station_id}/evses",
    response_model=ChargingEvseListResponse,
    summary="List EVSEs of a station",
)
async def list_charging_evses_endpoint(
    station_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ChargingEvseListResponse:
    """List active EVSEs belonging to a station.

    Args:
        station_id: UUID of the parent station.
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the list of EVSEs.

    Raises:
        ChargingStationNotFoundError: 404 if the parent station is not
            active.
    """
    return await charging_stations_service.list_charging_evses(
        db, station_id, page=page, page_size=page_size
    )


@router.post(
    "/charging-evses/{evse_id}/connectors",
    status_code=status.HTTP_201_CREATED,
    response_model=ChargingConnectorResponse,
    summary="Create a connector belonging to an EVSE",
)
async def create_charging_connector_endpoint(
    evse_id: UUID,
    connector_create_request: ChargingConnectorCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingConnectorResponse:
    """Create a connector belonging to an active EVSE.

    Args:
        evse_id: UUID of the parent EVSE.
        connector_create_request: Connector identity payload.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response for the newly created connector.

    Raises:
        ChargingEvseNotFoundError: 404 if the parent EVSE is not active.
        ChargingTopologyConflictError: 409 if the connector identity
            already exists in the EVSE.
    """
    return await charging_stations_service.create_charging_connector(
        db, evse_id, connector_create_request
    )


@router.get(
    "/charging-evses/{evse_id}/connectors",
    response_model=ChargingConnectorListResponse,
    summary="List connectors of an EVSE",
)
async def list_charging_connectors_endpoint(
    evse_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ChargingConnectorListResponse:
    """List active connectors belonging to an EVSE.

    Args:
        evse_id: UUID of the parent EVSE.
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the list of connectors.

    Raises:
        ChargingEvseNotFoundError: 404 if the parent EVSE is not active.
    """
    return await charging_stations_service.list_charging_connectors(
        db, evse_id, page=page, page_size=page_size
    )


@router.get(
    "/charging-stations/{station_id}",
    response_model=ChargingStationResponse,
    summary="Get a charging station",
)
async def get_charging_station_endpoint(
    station_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingStationResponse:
    """Get an active station by UUID.

    Args:
        station_id: UUID of the station to fetch.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the station.

    Raises:
        ChargingStationNotFoundError: 404 if the station does not exist or
            was soft-deleted.
    """
    return await charging_stations_service.get_charging_station(db, station_id)


@router.get(
    "/charging-stations/{station_id}/configuration",
    response_model=ChargingStationConfigurationResponse,
    summary="Get the latest configuration a charger reported",
)
async def get_charging_station_configuration_endpoint(
    station_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingStationConfigurationResponse:
    """Get the charger's latest ``GetConfiguration`` capture.

    The capture includes ``SupportedFeatureProfiles``, the charger's own
    answer to which OCPP profiles it supports. The response is empty (with
    ``null`` capture fields) until the charger has reported.

    Args:
        station_id: UUID of the station.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        The newest capture's keys, sorted by name.

    Raises:
        ChargingStationNotFoundError: 404 if the station does not exist or
            was soft-deleted.
    """
    return await charging_stations_service.get_latest_station_configuration(
        db, station_id
    )


@router.patch(
    "/charging-stations/{station_id}",
    response_model=ChargingStationResponse,
    summary="Update a charging station",
)
async def update_charging_station_endpoint(
    station_id: UUID,
    station_update_request: ChargingStationUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingStationResponse:
    """PATCH a station with the fields sent in the request.

    Args:
        station_id: UUID of the station to update.
        station_update_request: PATCH payload, already validated.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the updated station.

    Raises:
        ChargingStationNotFoundError: 404 if the station does not exist or
            was soft-deleted.
        ChargingTopologyConflictError: 409 if the new OCPP identity is
            already in use.
    """
    return await charging_stations_service.update_charging_station(
        db, station_id, station_update_request
    )


@router.delete(
    "/charging-stations/{station_id}",
    response_model=ChargingResourceDeleteResponse,
    summary="Soft-delete a charging station",
)
async def soft_delete_charging_station_endpoint(
    station_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingResourceDeleteResponse:
    """Soft-delete a station and its child topology.

    Args:
        station_id: UUID of the station to soft-delete.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response confirming the soft-delete.

    Raises:
        ChargingStationNotFoundError: 404 if the station does not exist or
            was soft-deleted.
    """
    return await charging_stations_service.soft_delete_charging_station(db, station_id)


@router.get(
    "/charging-evses/{evse_id}",
    response_model=ChargingEvseResponse,
    summary="Get an EVSE",
)
async def get_charging_evse_endpoint(
    evse_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingEvseResponse:
    """Get an active EVSE by UUID.

    Args:
        evse_id: UUID of the EVSE to fetch.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the EVSE.

    Raises:
        ChargingEvseNotFoundError: 404 if the EVSE does not exist or was
            soft-deleted.
    """
    return await charging_stations_service.get_charging_evse(db, evse_id)


@router.patch(
    "/charging-evses/{evse_id}",
    response_model=ChargingEvseResponse,
    summary="Update an EVSE",
)
async def update_charging_evse_endpoint(
    evse_id: UUID,
    evse_update_request: ChargingEvseUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingEvseResponse:
    """PATCH an EVSE with the fields sent in the request.

    Args:
        evse_id: UUID of the EVSE to update.
        evse_update_request: PATCH payload, already validated.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the updated EVSE.

    Raises:
        ChargingEvseNotFoundError: 404 if the EVSE does not exist or was
            soft-deleted.
        ChargingTopologyConflictError: 409 if the new identity already
            exists in the station.
    """
    return await charging_stations_service.update_charging_evse(
        db, evse_id, evse_update_request
    )


@router.delete(
    "/charging-evses/{evse_id}",
    response_model=ChargingResourceDeleteResponse,
    summary="Soft-delete an EVSE",
)
async def soft_delete_charging_evse_endpoint(
    evse_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingResourceDeleteResponse:
    """Soft-delete an EVSE and its child connectors.

    Args:
        evse_id: UUID of the EVSE to soft-delete.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response confirming the soft-delete.

    Raises:
        ChargingEvseNotFoundError: 404 if the EVSE does not exist or was
            soft-deleted.
    """
    return await charging_stations_service.soft_delete_charging_evse(db, evse_id)


@router.get(
    "/charging-connectors/{connector_id}",
    response_model=ChargingConnectorResponse,
    summary="Get a connector",
)
async def get_charging_connector_endpoint(
    connector_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingConnectorResponse:
    """Get an active connector by UUID.

    Args:
        connector_id: UUID of the connector to fetch.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the connector.

    Raises:
        ChargingConnectorNotFoundError: 404 if the connector does not exist
            or was soft-deleted.
    """
    return await charging_stations_service.get_charging_connector(db, connector_id)


@router.patch(
    "/charging-connectors/{connector_id}",
    response_model=ChargingConnectorResponse,
    summary="Update a connector",
)
async def update_charging_connector_endpoint(
    connector_id: UUID,
    connector_update_request: ChargingConnectorUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingConnectorResponse:
    """PATCH a connector with the fields sent in the request.

    Args:
        connector_id: UUID of the connector to update.
        connector_update_request: PATCH payload, already validated.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the updated connector.

    Raises:
        ChargingConnectorNotFoundError: 404 if the connector does not exist
            or was soft-deleted.
        ChargingTopologyConflictError: 409 if the new identity already
            exists in the EVSE.
    """
    return await charging_stations_service.update_charging_connector(
        db, connector_id, connector_update_request
    )


@router.delete(
    "/charging-connectors/{connector_id}",
    response_model=ChargingResourceDeleteResponse,
    summary="Soft-delete a connector",
)
async def soft_delete_charging_connector_endpoint(
    connector_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingResourceDeleteResponse:
    """Soft-delete a connector.

    Args:
        connector_id: UUID of the connector to soft-delete.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response confirming the soft-delete.

    Raises:
        ChargingConnectorNotFoundError: 404 if the connector does not exist
            or was soft-deleted.
    """
    return await charging_stations_service.soft_delete_charging_connector(
        db, connector_id
    )
