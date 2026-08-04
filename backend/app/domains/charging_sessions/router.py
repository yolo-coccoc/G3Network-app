"""HTTP router read-only cho monitoring charging session MVP.

Router chỉ nhận HTTP dependency, gọi public monitoring service và chuyển domain
exception thành status code. Không expose raw OCPP payload hoặc command
transport trong bước này.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.service as charging_session_service
from app.domains.charging_sessions.exceptions import ChargingSessionNotFoundError
from app.domains.charging_sessions.schemas import (
    ChargingSessionEventListResponse,
    ChargingSessionMeterValueListResponse,
    ChargingSessionResponse,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["charging-sessions"])


@router.get(
    "/charging-sessions/{session_id}",
    response_model=ChargingSessionResponse,
    summary="Xem charging session",
)
async def get_session(
    session_id: UUID, db: AsyncSession = Depends(get_db)
) -> ChargingSessionResponse:
    """Lấy aggregate session theo UUID nội bộ.

    Args:
        session_id: UUID session cần xem.
        db: Async session do dependency ``get_db`` sở hữu transaction.

    Returns:
        Session response không chứa raw payload hoặc field ngoài MVP.

    Raises:
        HTTPException: ``404`` nếu session không tồn tại.
    """
    try:
        return await charging_session_service.get_session(db, session_id)
    except ChargingSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error


@router.get(
    "/charging-sessions/{session_id}/events",
    response_model=ChargingSessionEventListResponse,
    summary="Xem event của charging session",
)
async def list_session_events(
    session_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionEventListResponse:
    """Lấy lifecycle event của session theo thứ tự thời gian tăng dần.

    Args:
        session_id: UUID session cần xem event.
        page: Trang bắt đầu từ một.
        page_size: Số event tối đa trong trang.
        db: Async session do dependency ``get_db`` sở hữu transaction.

    Returns:
        Event history phân trang.

    Raises:
        HTTPException: ``404`` nếu session không tồn tại.
    """
    try:
        return await charging_session_service.list_session_events(
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
    summary="Xem meter values của charging session",
)
async def list_session_meter_values(
    session_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
    ),
    db: AsyncSession = Depends(get_db),
) -> ChargingSessionMeterValueListResponse:
    """Lấy meter sample canonical Wh của session theo thời gian tăng dần.

    Args:
        session_id: UUID session cần xem meter.
        page: Trang bắt đầu từ một.
        page_size: Số sample tối đa trong trang.
        db: Async session do dependency ``get_db`` sở hữu transaction.

    Returns:
        Meter history phân trang.

    Raises:
        HTTPException: ``404`` nếu session không tồn tại.
    """
    try:
        return await charging_session_service.list_session_meter_values(
            db,
            session_id,
            page=page,
            page_size=page_size,
        )
    except ChargingSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
