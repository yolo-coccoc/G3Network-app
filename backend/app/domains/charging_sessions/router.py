"""Read-only HTTP router for the charging session monitoring MVP.

The router only accepts HTTP dependencies, calls the public monitoring
service and converts domain exceptions into status codes. It does not
expose raw OCPP payloads or command transport at this stage.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.service as charging_session_service
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionNotFoundError,
)
from app.domains.charging_sessions.schemas import (
    ChargingSessionEventListResponse,
    ChargingSessionListResponse,
    ChargingSessionMeasurementListResponse,
    ChargingSessionMeterValueListResponse,
    ChargingSessionResponse,
    StationEnergySummaryResponse,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["charging-sessions"])


@router.get(
    "/charging-sessions",
    response_model=ChargingSessionListResponse,
    summary="List charging sessions",
)
async def list_charging_sessions_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionListResponse:
    """List sessions newest first, to obtain session IDs for monitoring.

    Args:
        page: The page, starting at one.
        page_size: The maximum number of sessions in the page.
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        A paginated list of sessions, containing no raw payloads or fields
        outside the MVP.
    """
    return await charging_session_service.list_charging_sessions(
        db,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/charging-sessions/{session_id}",
    response_model=ChargingSessionResponse,
    summary="View a charging session",
)
async def get_charging_session_endpoint(
    session_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingSessionResponse:
    """Get the session aggregate by internal UUID.

    Args:
        session_id: UUID of the session to view.
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        A session response containing no raw payloads or fields outside the
        MVP.

    Raises:
        HTTPException: ``404`` if the session does not exist.
    """
    try:
        return await charging_session_service.get_charging_session(db, session_id)
    except ChargingSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/charging-sessions/{session_id}/events",
    response_model=ChargingSessionEventListResponse,
    summary="View events of a charging session",
)
async def list_charging_session_events_endpoint(
    session_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionEventListResponse:
    """Get the session's lifecycle events in ascending time order.

    Args:
        session_id: UUID of the session whose events to view.
        page: The page, starting at one.
        page_size: The maximum number of events in the page.
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        A paginated event history.

    Raises:
        HTTPException: ``404`` if the session does not exist.
    """
    try:
        return await charging_session_service.list_charging_session_events(
            db,
            session_id,
            page=page,
            page_size=page_size,
        )
    except ChargingSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/charging-sessions/{session_id}/meter-values",
    response_model=ChargingSessionMeterValueListResponse,
    summary="View meter values of a charging session",
)
async def list_charging_session_meter_values_endpoint(
    session_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionMeterValueListResponse:
    """Get the session's canonical Wh meter samples in ascending time order.

    Args:
        session_id: UUID of the session whose meter to view.
        page: The page, starting at one.
        page_size: The maximum number of samples in the page.
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        A paginated meter history.

    Raises:
        HTTPException: ``404`` if the session does not exist.
    """
    try:
        return await charging_session_service.list_charging_session_meter_values(
            db,
            session_id,
            page=page,
            page_size=page_size,
        )
    except ChargingSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/charging-sessions/{session_id}/measurements",
    response_model=ChargingSessionMeasurementListResponse,
    summary="View all measurements of a charging session",
)
async def list_charging_session_measurements_endpoint(
    session_id: UUID,
    measurand: str | None = Query(
        None,
        min_length=1,
        max_length=60,
        description="Return only this measurand, for example SoC.",
    ),
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionMeasurementListResponse:
    """Get the session's measurements of every measurand, in ascending time order.

    ``/meter-values`` remains the energy-only view.

    Args:
        session_id: UUID of the session whose measurements to view.
        measurand: Optional filter on the measurand name.
        page: The page, starting at one.
        page_size: The maximum number of measurements in the page.
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        A paginated measurement history.

    Raises:
        HTTPException: ``404`` if the session does not exist.
    """
    try:
        return await charging_session_service.list_charging_session_measurements(
            db,
            session_id,
            measurand=measurand,
            page=page,
            page_size=page_size,
        )
    except ChargingSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/charging-sessions/stations/{station_id}/energy",
    response_model=StationEnergySummaryResponse,
    summary="Get total energy sold at a station within a time window",
)
async def get_station_energy_summary_endpoint(
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
    db: AsyncSession = Depends(get_db),
) -> StationEnergySummaryResponse:
    """Aggregate completed sessions' energy for a station over a window (F-C5).

    Args:
        station_id: UUID of the station to aggregate over.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        Total kWh and completed-session count for the window. An unknown
        ``station_id`` returns a zero summary, not a 404 - see the service
        docstring.

    Raises:
        HTTPException: ``400`` if either timestamp lacks a timezone or
            ``end_time`` isn't after ``start_time``.
    """
    try:
        return await charging_session_service.get_station_energy_summary(
            db,
            station_id=station_id,
            start_time=start_time,
            end_time=end_time,
        )
    except ChargingSessionInputError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
