"""FastAPI router cho CRUD thiết bị Telematic."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telematics import service
from app.domains.telematics.exceptions import (
    TelematicConflictError,
    TelematicNotFoundError,
)
from app.domains.telematics.schemas import (
    TelematicCreate,
    TelematicListResponse,
    TelematicResponse,
    TelematicUpdate,
)
from app.domains.telematics.types import TelematicStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["telematics"])


@router.post("/", response_model=TelematicResponse, status_code=status.HTTP_201_CREATED)
async def create_telematic_endpoint(
    telematic_create_request: TelematicCreate,
    db_session: AsyncSession = Depends(get_db),
) -> TelematicResponse:
    """Tạo thiết bị Telematic."""
    try:
        return await service.create_telematic(
            db_session,
            telematic_create_request,
        )
    except TelematicConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/", response_model=TelematicListResponse)
async def list_telematics_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE, ge=1, le=settings.API_MAX_PAGE_SIZE
    ),
    status_filter: TelematicStatus | None = Query(None, alias="status"),
    db_session: AsyncSession = Depends(get_db),
) -> TelematicListResponse:
    """Liệt kê thiết bị chưa bị xoá."""
    return await service.list_telematics(
        db_session,
        page,
        page_size,
        status_filter,
    )


@router.get("/{telematic_id}", response_model=TelematicResponse)
async def get_telematic_endpoint(
    telematic_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> TelematicResponse:
    """Lấy chi tiết thiết bị."""
    try:
        return await service.get_telematic(db_session, telematic_id)
    except TelematicNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/{telematic_id}", response_model=TelematicResponse)
async def update_telematic_endpoint(
    telematic_id: UUID,
    telematic_update_request: TelematicUpdate,
    db_session: AsyncSession = Depends(get_db),
) -> TelematicResponse:
    """Cập nhật từng phần thiết bị."""
    try:
        return await service.update_telematic(
            db_session,
            telematic_id,
            telematic_update_request,
        )
    except TelematicNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TelematicConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/{telematic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_telematic_endpoint(
    telematic_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> None:
    """Soft delete thiết bị."""
    try:
        await service.soft_delete_telematic(db_session, telematic_id)
    except TelematicNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
