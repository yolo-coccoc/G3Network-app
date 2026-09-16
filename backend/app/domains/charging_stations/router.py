"""HTTP router for station, EVSE, and connector topology CRUD.

The router only accepts HTTP dependencies, calls the public service, and
translates domain exceptions into status codes. Business rules, database
queries, and transaction boundaries do not belong in this module.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.service as charging_service
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingEvseNotFoundError,
    ChargingStationNotFoundError,
    ChargingTopologyConflictError,
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
    station_data: ChargingStationCreateRequest, db: AsyncSession = Depends(get_db)
) -> ChargingStationResponse:
    """Create a pre-provisioned station.

    Args:
        station_data: Station creation payload, already Pydantic-validated.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response for the newly created station.

    Raises:
        HTTPException: ``409`` if the OCPP identity already exists.
    """
    try:
        return await charging_service.create_charging_station(db, station_data)
    except ChargingTopologyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


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
    """
    return await charging_service.list_charging_stations(
        db,
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
    evse_data: ChargingEvseCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingEvseResponse:
    """Create an EVSE belonging to an active station.

    Args:
        station_id: UUID of the parent station.
        evse_data: EVSE identity payload.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response for the newly created EVSE.

    Raises:
        HTTPException: ``404`` if the station does not exist; ``409`` if the
            EVSE identity is duplicated.
    """
    try:
        return await charging_service.create_charging_evse(db, station_id, evse_data)
    except ChargingStationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except ChargingTopologyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


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
        HTTPException: ``404`` if the parent station is not active.
    """
    try:
        return await charging_service.list_charging_evses(
            db, station_id, page=page, page_size=page_size
        )
    except ChargingStationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.post(
    "/charging-evses/{evse_id}/connectors",
    status_code=status.HTTP_201_CREATED,
    response_model=ChargingConnectorResponse,
    summary="Create a connector belonging to an EVSE",
)
async def create_charging_connector_endpoint(
    evse_id: UUID,
    connector_data: ChargingConnectorCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingConnectorResponse:
    """Create a connector belonging to an active EVSE.

    Args:
        evse_id: UUID of the parent EVSE.
        connector_data: Connector identity payload.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response for the newly created connector.

    Raises:
        HTTPException: ``404`` if the EVSE does not exist; ``409`` if the
            connector identity is duplicated.
    """
    try:
        return await charging_service.create_charging_connector(
            db, evse_id, connector_data
        )
    except ChargingEvseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except ChargingTopologyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


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
        HTTPException: ``404`` if the parent EVSE is not active.
    """
    try:
        return await charging_service.list_charging_connectors(
            db, evse_id, page=page, page_size=page_size
        )
    except ChargingEvseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


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
        HTTPException: ``404`` if the station does not exist or was deleted.
    """
    try:
        return await charging_service.get_charging_station(db, station_id)
    except ChargingStationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/charging-stations/{station_id}",
    response_model=ChargingStationResponse,
    summary="Update a charging station",
)
async def update_charging_station_endpoint(
    station_id: UUID,
    station_data: ChargingStationUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingStationResponse:
    """PATCH a station with the fields sent in the request.

    Args:
        station_id: UUID of the station to update.
        station_data: PATCH payload, already validated.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the updated station.

    Raises:
        HTTPException: ``404`` if the station does not exist; ``409`` if the
            new identity is duplicated.
    """
    try:
        return await charging_service.update_charging_station(
            db, station_id, station_data
        )
    except ChargingStationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except ChargingTopologyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


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
        HTTPException: ``404`` if the station does not exist or was deleted.
    """
    try:
        return await charging_service.soft_delete_charging_station(db, station_id)
    except ChargingStationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


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
        HTTPException: ``404`` if the EVSE does not exist or was deleted.
    """
    try:
        return await charging_service.get_charging_evse(db, evse_id)
    except ChargingEvseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/charging-evses/{evse_id}",
    response_model=ChargingEvseResponse,
    summary="Update an EVSE",
)
async def update_charging_evse_endpoint(
    evse_id: UUID,
    evse_data: ChargingEvseUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingEvseResponse:
    """PATCH an EVSE with the fields sent in the request.

    Args:
        evse_id: UUID of the EVSE to update.
        evse_data: PATCH payload, already validated.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the updated EVSE.

    Raises:
        HTTPException: ``404`` if the EVSE does not exist; ``409`` if the new
            identity is duplicated within the station.
    """
    try:
        return await charging_service.update_charging_evse(db, evse_id, evse_data)
    except ChargingEvseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except ChargingTopologyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


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
        HTTPException: ``404`` if the EVSE does not exist or was deleted.
    """
    try:
        return await charging_service.soft_delete_charging_evse(db, evse_id)
    except ChargingEvseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


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
        HTTPException: ``404`` if the connector does not exist or was
            deleted.
    """
    try:
        return await charging_service.get_charging_connector(db, connector_id)
    except ChargingConnectorNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/charging-connectors/{connector_id}",
    response_model=ChargingConnectorResponse,
    summary="Update a connector",
)
async def update_charging_connector_endpoint(
    connector_id: UUID,
    connector_data: ChargingConnectorUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> ChargingConnectorResponse:
    """PATCH a connector with the fields sent in the request.

    Args:
        connector_id: UUID of the connector to update.
        connector_data: PATCH payload, already validated.
        db: Async session owned by the ``get_db`` dependency.

    Returns:
        HTTP response with the updated connector.

    Raises:
        HTTPException: ``404`` if the connector does not exist; ``409`` if
            the new identity is duplicated within the EVSE.
    """
    try:
        return await charging_service.update_charging_connector(
            db, connector_id, connector_data
        )
    except ChargingConnectorNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except ChargingTopologyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


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
        HTTPException: ``404`` if the connector does not exist or was
            deleted.
    """
    try:
        return await charging_service.soft_delete_charging_connector(db, connector_id)
    except ChargingConnectorNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
