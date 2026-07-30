"""Business service cho CRUD thiết bị Telematic."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telematics import repository
from app.domains.telematics.exceptions import (
    TelematicConflictError,
    TelematicNotFoundError,
)
from app.domains.telematics.models import Telematic
from app.domains.telematics.schemas import (
    TelematicCreate,
    TelematicListResponse,
    TelematicResponse,
    TelematicUpdate,
)
from app.domains.telematics.types import TelematicStatus
from app.domains.vehicles.service import (
    find_active_vehicle_by_id,
    find_active_vehicle_by_vin,
)
from app.libs.common.config import settings


async def _response(db: AsyncSession, item: Telematic) -> TelematicResponse:
    """Chuyển model thành response và resolve VIN hiện tại."""
    vin = None
    if item.vehicle_id:
        vehicle = await find_active_vehicle_by_id(db, item.vehicle_id)
        vin = vehicle.vin if vehicle else None
    return TelematicResponse.model_validate({**item.__dict__, "vehicle_vin": vin})


async def create_telematic(
    db: AsyncSession, data: TelematicCreate
) -> TelematicResponse:
    """Tạo thiết bị, resolve VIN nếu xe đang tồn tại."""
    if await repository.get_by_serial(db, data.telematic_serial):
        raise TelematicConflictError("Telematic serial đã tồn tại")
    vehicle_id = None
    if data.vehicle_vin:
        vehicle = await find_active_vehicle_by_vin(db, data.vehicle_vin)
        vehicle_id = vehicle.vehicle_id if vehicle else None
        if vehicle_id and await db.scalar(
            select(Telematic.telematic_id).where(
                Telematic.vehicle_id == vehicle_id, Telematic.deleted_at.is_(None)
            )
        ):
            raise TelematicConflictError("Xe đã được gán cho telematic khác")
    try:
        item = await repository.create(
            db,
            {
                "telematic_serial": data.telematic_serial,
                "vehicle_id": vehicle_id,
                "status": data.status,
                "firmware_version": data.firmware_version,
            },
        )
    except IntegrityError as error:
        raise TelematicConflictError("Telematic serial hoặc xe đã tồn tại") from error
    return await _response(db, item)


async def get_telematic(db: AsyncSession, telematic_id: UUID) -> TelematicResponse:
    """Lấy chi tiết thiết bị."""
    item = await repository.get_by_id(db, telematic_id)
    if not item:
        raise TelematicNotFoundError("Không tìm thấy telematic")
    return await _response(db, item)


async def list_telematics(
    db: AsyncSession, page: int, page_size: int, status: TelematicStatus | None
) -> TelematicListResponse:
    """Lấy danh sách thiết bị có phân trang."""
    page_size = min(max(page_size, 1), settings.API_MAX_PAGE_SIZE)
    page = max(page, settings.API_DEFAULT_PAGE)
    items = await repository.list_items(db, (page - 1) * page_size, page_size, status)
    return TelematicListResponse(
        items=[await _response(db, item) for item in items],
        total=await repository.count_items(db, status),
        page=page,
        page_size=page_size,
    )


async def update_telematic(
    db: AsyncSession, telematic_id: UUID, data: TelematicUpdate
) -> TelematicResponse:
    """Cập nhật thiết bị và resolve lại VIN khi field được gửi."""
    item = await repository.get_by_id(db, telematic_id)
    if not item:
        raise TelematicNotFoundError("Không tìm thấy telematic")
    values = data.model_dump(exclude_unset=True)
    if (
        "telematic_serial" in values
        and values["telematic_serial"] != item.telematic_serial
        and await repository.get_by_serial(db, values["telematic_serial"])
    ):
        raise TelematicConflictError("Telematic serial đã tồn tại")
    if "vehicle_vin" in values:
        vin = values.pop("vehicle_vin")
        vehicle = await find_active_vehicle_by_vin(db, vin) if vin else None
        values["vehicle_id"] = vehicle.vehicle_id if vehicle else None
    values = {
        key: value
        for key, value in values.items()
        if value is not None or key == "vehicle_id"
    }
    try:
        item = await repository.update(db, item, values)
    except IntegrityError as error:
        raise TelematicConflictError("Xe đã được gán cho telematic khác") from error
    return await _response(db, item)


async def delete_telematic(db: AsyncSession, telematic_id: UUID) -> None:
    """Soft delete thiết bị."""
    item = await repository.get_by_id(db, telematic_id)
    if not item:
        raise TelematicNotFoundError("Không tìm thấy telematic")
    await repository.soft_delete(db, item)
