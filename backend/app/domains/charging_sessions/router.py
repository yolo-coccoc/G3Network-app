"""Read-only HTTP router for the charging session monitoring MVP.

The router only accepts HTTP dependencies and calls the public monitoring
service; domain exceptions are mapped to status codes centrally in
``app/api/main.py``. It does not expose raw OCPP payloads or command
transport at this stage.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.service as charging_session_service
from app.domains.charging_sessions.schemas import (
    ChargingSessionDetailResponse,
    ChargingSessionEventListResponse,
    ChargingSessionListResponse,
    ChargingSessionMeasurementListResponse,
    ChargingSessionMeterValueListResponse,
    StationEnergySeriesResponse,
    StationEnergySummaryResponse,
)
from app.domains.charging_sessions.types import (
    EnergySeriesGranularity,
    SessionStatus,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["charging-sessions"])


@dataclass(frozen=True)
class _PageQuery:
    """The ``page``/``page_size`` query parameters shared by every list endpoint.

    Attributes:
        page: The page, starting at one.
        page_size: The maximum number of items in the page.
    """

    page: int
    page_size: int


def _page_query(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
) -> _PageQuery:
    """Read and validate the pagination query parameters (FastAPI dependency).

    Args:
        page: The page, starting at one.
        page_size: The maximum number of items in the page.

    Returns:
        The validated page request.
    """
    return _PageQuery(page=page, page_size=page_size)


@router.get(
    "/charging-sessions",
    response_model=ChargingSessionListResponse,
    summary="List charging sessions",
)
async def list_charging_sessions_endpoint(
    station_id: UUID | None = None,
    connector_id: UUID | None = None,
    status: SessionStatus | None = None,
    started_from: datetime | None = Query(
        None, description="Inclusive lower bound on started_at, with a timezone."
    ),
    started_to: datetime | None = Query(
        None, description="Exclusive upper bound on started_at, with a timezone."
    ),
    page_query: _PageQuery = Depends(_page_query),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionListResponse:
    """List sessions newest first, optionally filtered (F-B2).

    Args:
        station_id: Only sessions of this station.
        connector_id: Only sessions on this connector.
        status: Only ``active`` or only ``completed`` sessions.
        started_from: Inclusive lower bound on ``started_at``.
        started_to: Exclusive upper bound on ``started_at``.
        page_query: The page and page size (``page``/``page_size`` query
            parameters).
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        A paginated list of sessions, containing no raw payloads or fields
        outside the MVP.

    Raises:
        ChargingSessionInputError: A time bound lacks a timezone or
            ``started_to`` is not after ``started_from`` (HTTP 400).
    """
    return await charging_session_service.list_charging_sessions(
        db,
        page=page_query.page,
        page_size=page_query.page_size,
        station_id=station_id,
        connector_id=connector_id,
        status=status,
        started_from=started_from,
        started_to=started_to,
    )


@router.get(
    "/charging-sessions/{session_id}",
    response_model=ChargingSessionDetailResponse,
    summary="View a charging session",
)
async def get_charging_session_endpoint(
    session_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingSessionDetailResponse:
    """Get the session aggregate and its read-time summary by internal UUID.

    Args:
        session_id: UUID of the session to view.
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        The session plus ``duration_seconds``, the first/last SoC and the
        maximum import power, computed from its measurements.

    Raises:
        ChargingSessionNotFoundError: The session does not exist (HTTP 404).
    """
    return await charging_session_service.get_charging_session(db, session_id)


@router.get(
    "/charging-sessions/{session_id}/events",
    response_model=ChargingSessionEventListResponse,
    summary="View events of a charging session",
)
async def list_charging_session_events_endpoint(
    session_id: UUID,
    page_query: _PageQuery = Depends(_page_query),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionEventListResponse:
    """Get the session's lifecycle events in ascending time order.

    Args:
        session_id: UUID of the session whose events to view.
        page_query: The page and page size (``page``/``page_size`` query
            parameters).
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        A paginated event history.

    Raises:
        ChargingSessionNotFoundError: The session does not exist (HTTP 404).
    """
    return await charging_session_service.list_charging_session_events(
        db,
        session_id,
        page=page_query.page,
        page_size=page_query.page_size,
    )


@router.get(
    "/charging-sessions/{session_id}/meter-values",
    response_model=ChargingSessionMeterValueListResponse,
    summary="View meter values of a charging session",
)
async def list_charging_session_meter_values_endpoint(
    session_id: UUID,
    page_query: _PageQuery = Depends(_page_query),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionMeterValueListResponse:
    """Get the session's canonical Wh meter samples in ascending time order.

    Args:
        session_id: UUID of the session whose meter to view.
        page_query: The page and page size (``page``/``page_size`` query
            parameters).
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        A paginated meter history.

    Raises:
        ChargingSessionNotFoundError: The session does not exist (HTTP 404).
    """
    return await charging_session_service.list_charging_session_meter_values(
        db,
        session_id,
        page=page_query.page,
        page_size=page_query.page_size,
    )


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
    page_query: _PageQuery = Depends(_page_query),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionMeasurementListResponse:
    """Get the session's measurements of every measurand, in ascending time order.

    ``/meter-values`` remains the energy-only view.

    Args:
        session_id: UUID of the session whose measurements to view.
        measurand: Optional filter on the measurand name.
        page_query: The page and page size (``page``/``page_size`` query
            parameters).
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        A paginated measurement history.

    Raises:
        ChargingSessionNotFoundError: The session does not exist (HTTP 404).
    """
    return await charging_session_service.list_charging_session_measurements(
        db,
        session_id,
        measurand=measurand,
        page=page_query.page,
        page_size=page_query.page_size,
    )


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
        ChargingSessionInputError: Either timestamp lacks a timezone or
            ``end_time`` isn't after ``start_time`` (HTTP 400).
    """
    return await charging_session_service.get_station_energy_summary(
        db,
        station_id=station_id,
        start_time=start_time,
        end_time=end_time,
    )


@router.get(
    "/charging-sessions/stations/{station_id}/energy/series",
    response_model=StationEnergySeriesResponse,
    summary="Get a station's energy per hour or day within a time window",
)
async def get_station_energy_series_endpoint(
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
    granularity: EnergySeriesGranularity = EnergySeriesGranularity.HOUR,
    db: AsyncSession = Depends(get_db),
) -> StationEnergySeriesResponse:
    """Dense hourly or daily energy series of a station (F-C5).

    Args:
        station_id: UUID of the station.
        start_time: Inclusive window start; must carry a timezone.
        end_time: Exclusive window end; must carry a timezone.
        granularity: ``hour`` (default) or ``day``; buckets are cut in
            ``APP_REPORT_TIMEZONE``.
        db: The async session whose transaction is owned by the ``get_db``
            dependency.

    Returns:
        One bucket per hour/day (empty ones at zero) and the total, in kWh.
        An unknown ``station_id`` returns an all-zero series.

    Raises:
        ChargingSessionInputError: A bound lacks a timezone, ``end_time`` is
            not after ``start_time``, or the window exceeds
            ``CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS`` (HTTP 400).
    """
    return await charging_session_service.get_station_energy_series(
        db,
        station_id=station_id,
        start_time=start_time,
        end_time=end_time,
        granularity=granularity,
    )
