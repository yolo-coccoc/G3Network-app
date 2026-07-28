"""FastAPI router cho CRUD thiết bị Telematic."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telematics import service
from app.domains.telematics.exceptions import TelematicConflictError, TelematicNotFoundError
from app.domains.telematics.schemas import TelematicCreate, TelematicListResponse, TelematicResponse, TelematicUpdate
from app.domains.telematics.types import TelematicStatus
from app.libs.db.session import get_db

router = APIRouter(tags=["telematics"])


@router.post("/", response_model=TelematicResponse, status_code=status.HTTP_201_CREATED)
async def create(data: TelematicCreate, db: AsyncSession = Depends(get_db)) -> TelematicResponse:
    """Tạo thiết bị Telematic."""
    try:
        return await service.create_telematic(db, data)
    except TelematicConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/", response_model=TelematicListResponse)
async def list_items(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100), status_filter: TelematicStatus | None = Query(None, alias="status"), db: AsyncSession = Depends(get_db)) -> TelematicListResponse:
    """Liệt kê thiết bị chưa bị xoá."""
    return await service.list_telematics(db, page, page_size, status_filter)


@router.get("/{telematic_id}", response_model=TelematicResponse)
async def get(telematic_id: UUID, db: AsyncSession = Depends(get_db)) -> TelematicResponse:
    """Lấy chi tiết thiết bị."""
    try:
        return await service.get_telematic(db, telematic_id)
    except TelematicNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/{telematic_id}", response_model=TelematicResponse)
async def update(telematic_id: UUID, data: TelematicUpdate, db: AsyncSession = Depends(get_db)) -> TelematicResponse:
    """Cập nhật từng phần thiết bị."""
    try:
        return await service.update_telematic(db, telematic_id, data)
    except TelematicNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TelematicConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/{telematic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(telematic_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    """Soft delete thiết bị."""
    try:
        await service.delete_telematic(db, telematic_id)
    except TelematicNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
