"""HTTP router cho CRUD topology station, EVSE và connector."""

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
    ConnectorCreate,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorUpdate,
    DeleteResponse,
    EvseCreate,
    EvseListResponse,
    EvseResponse,
    EvseUpdate,
    StationCreate,
    StationListResponse,
    StationResponse,
    StationUpdate,
)
from app.domains.charging_stations.types import (
    StationAdministrativeStatus,
    StationConnectionStatus,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["charging-stations"])


@router.post(
    "/charging-stations",
    status_code=status.HTTP_201_CREATED,
    response_model=StationResponse,
    summary="Tạo charging station",
)
async def create_station(
    station_data: StationCreate, db: AsyncSession = Depends(get_db)
) -> StationResponse:
    """Tạo station đã pre-provision."""
    try:
        return await charging_service.create_station(db, station_data)
    except ChargingTopologyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error


@router.get(
    "/charging-stations",
    response_model=StationListResponse,
    summary="Liệt kê charging station",
)
async def list_stations(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    administrative_status: StationAdministrativeStatus | None = Query(
        None, alias="administrative_status"
    ),
    connection_status: StationConnectionStatus | None = Query(
        None, alias="connection_status"
    ),
    db: AsyncSession = Depends(get_db),
) -> StationListResponse:
    """Liệt kê station active với filter trạng thái và phân trang."""
    return await charging_service.list_stations(
        db,
        page=page,
        page_size=page_size,
        administrative_status=administrative_status,
        connection_status=connection_status,
    )


@router.post(
    "/charging-stations/{station_id}/evses",
    status_code=status.HTTP_201_CREATED,
    response_model=EvseResponse,
    summary="Tạo EVSE thuộc station",
)
async def create_evse(
    station_id: UUID,
    evse_data: EvseCreate,
    db: AsyncSession = Depends(get_db),
) -> EvseResponse:
    """Tạo EVSE thuộc station active."""
    try:
        return await charging_service.create_evse(db, station_id, evse_data)
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
    response_model=EvseListResponse,
    summary="Liệt kê EVSE của station",
)
async def list_evses(
    station_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> EvseListResponse:
    """Liệt kê EVSE active thuộc station."""
    try:
        return await charging_service.list_evses(
            db, station_id, page=page, page_size=page_size
        )
    except ChargingStationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.post(
    "/charging-evses/{evse_id}/connectors",
    status_code=status.HTTP_201_CREATED,
    response_model=ConnectorResponse,
    summary="Tạo connector thuộc EVSE",
)
async def create_connector(
    evse_id: UUID,
    connector_data: ConnectorCreate,
    db: AsyncSession = Depends(get_db),
) -> ConnectorResponse:
    """Tạo connector thuộc EVSE active."""
    try:
        return await charging_service.create_connector(db, evse_id, connector_data)
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
    response_model=ConnectorListResponse,
    summary="Liệt kê connector của EVSE",
)
async def list_connectors(
    evse_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ConnectorListResponse:
    """Liệt kê connector active thuộc EVSE."""
    try:
        return await charging_service.list_connectors(
            db, evse_id, page=page, page_size=page_size
        )
    except ChargingEvseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/charging-stations/{station_id}",
    response_model=StationResponse,
    summary="Lấy charging station",
)
async def get_station(
    station_id: UUID, db: AsyncSession = Depends(get_db)
) -> StationResponse:
    """Lấy station active theo UUID."""
    try:
        return await charging_service.get_station(db, station_id)
    except ChargingStationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/charging-stations/{station_id}",
    response_model=StationResponse,
    summary="Cập nhật charging station",
)
async def update_station(
    station_id: UUID,
    station_data: StationUpdate,
    db: AsyncSession = Depends(get_db),
) -> StationResponse:
    """PATCH station với các field được gửi trong request."""
    try:
        return await charging_service.update_station(db, station_id, station_data)
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
    response_model=DeleteResponse,
    summary="Xoá mềm charging station",
)
async def delete_station(
    station_id: UUID, db: AsyncSession = Depends(get_db)
) -> DeleteResponse:
    """Soft-delete station và topology con."""
    try:
        return await charging_service.delete_station(db, station_id)
    except ChargingStationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/charging-evses/{evse_id}",
    response_model=EvseResponse,
    summary="Lấy EVSE",
)
async def get_evse(evse_id: UUID, db: AsyncSession = Depends(get_db)) -> EvseResponse:
    """Lấy EVSE active theo UUID."""
    try:
        return await charging_service.get_evse(db, evse_id)
    except ChargingEvseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/charging-evses/{evse_id}",
    response_model=EvseResponse,
    summary="Cập nhật EVSE",
)
async def update_evse(
    evse_id: UUID,
    evse_data: EvseUpdate,
    db: AsyncSession = Depends(get_db),
) -> EvseResponse:
    """PATCH EVSE với các field được gửi trong request."""
    try:
        return await charging_service.update_evse(db, evse_id, evse_data)
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
    response_model=DeleteResponse,
    summary="Xoá mềm EVSE",
)
async def delete_evse(
    evse_id: UUID, db: AsyncSession = Depends(get_db)
) -> DeleteResponse:
    """Soft-delete EVSE và connector con."""
    try:
        return await charging_service.delete_evse(db, evse_id)
    except ChargingEvseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/charging-connectors/{connector_id}",
    response_model=ConnectorResponse,
    summary="Lấy connector",
)
async def get_connector(
    connector_id: UUID, db: AsyncSession = Depends(get_db)
) -> ConnectorResponse:
    """Lấy connector active theo UUID."""
    try:
        return await charging_service.get_connector(db, connector_id)
    except ChargingConnectorNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.patch(
    "/charging-connectors/{connector_id}",
    response_model=ConnectorResponse,
    summary="Cập nhật connector",
)
async def update_connector(
    connector_id: UUID,
    connector_data: ConnectorUpdate,
    db: AsyncSession = Depends(get_db),
) -> ConnectorResponse:
    """PATCH connector với các field được gửi trong request."""
    try:
        return await charging_service.update_connector(db, connector_id, connector_data)
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
    response_model=DeleteResponse,
    summary="Xoá mềm connector",
)
async def delete_connector(
    connector_id: UUID, db: AsyncSession = Depends(get_db)
) -> DeleteResponse:
    """Soft-delete connector."""
    try:
        return await charging_service.delete_connector(db, connector_id)
    except ChargingConnectorNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
