"""Business service cho CRUD và public lookup thiết bị Telematic.

Các domain khác, đặc biệt ``telemetry``, chỉ được dùng những hàm public trong
module này để resolve mapping thiết bị–xe; chúng không được truy cập trực tiếp
repository hoặc model của ``telematics``.
"""

from collections.abc import Sequence
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
from app.domains.vehicles import service as vehicle_service
from app.libs.common.config import settings


async def resolve_mapping_by_serial(
    db: AsyncSession,
    serial: str,
) -> tuple[UUID, UUID] | None:
    """Resolve một serial telematic thành ID thiết bị và ID xe.

    Args:
        db: Phiên database do entry boundary sở hữu.
        serial: Serial vật lý nhận từ message telemetry.

    Returns:
        Tuple ``(telematic_id, vehicle_id)`` nếu mapping hợp lệ; ``None`` nếu
        thiết bị chưa tồn tại, đã bị xoá mềm hoặc chưa gán xe.

    Side Effects:
        Thực hiện truy vấn read-only trong phiên hiện tại; không commit hoặc
        rollback.
    """
    return await repository.get_mapping(db, serial)


async def resolve_mappings_by_serial(
    db: AsyncSession,
    serials: Sequence[str],
) -> dict[str, tuple[UUID, UUID]]:
    """Resolve batch serial telematic thành mapping thiết bị–xe.

    Args:
        db: Phiên database do entry boundary sở hữu.
        serials: Các serial vật lý cần tra cứu.

    Returns:
        Dict ánh xạ serial sang ``(telematic_id, vehicle_id)``; mapping không
        hợp lệ không xuất hiện trong kết quả.

    Side Effects:
        Thực hiện một truy vấn read-only trong phiên hiện tại; không commit hoặc
        rollback.
    """
    return await repository.get_mappings(db, serials)


async def build_telematic_response(
    db_session: AsyncSession,
    telematic_record: Telematic,
) -> TelematicResponse:
    """Dựng response telematic và bổ sung VIN hiện tại của xe.

    Việc bổ sung VIN cần gọi public service của domain vehicles, vì domain này
    không được truy cập trực tiếp repository hoặc ORM model của vehicles.
    """
    vin = None
    if telematic_record.vehicle_id:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session,
            telematic_record.vehicle_id,
        )
        vin = vehicle_reference.vin if vehicle_reference else None
    return TelematicResponse.model_validate(
        {**telematic_record.__dict__, "vehicle_vin": vin}
    )


async def create_telematic(
    db_session: AsyncSession, telematic_create_request: TelematicCreate
) -> TelematicResponse:
    """Tạo thiết bị, resolve VIN nếu xe đang tồn tại."""
    if await repository.get_by_serial(
        db_session,
        telematic_create_request.telematic_serial,
    ):
        raise TelematicConflictError("Telematic serial đã tồn tại")
    vehicle_id = None
    if telematic_create_request.vehicle_vin:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
            db_session,
            telematic_create_request.vehicle_vin,
        )
        vehicle_id = vehicle_reference.vehicle_id if vehicle_reference else None
        if vehicle_id and await db_session.scalar(
            select(Telematic.telematic_id).where(
                Telematic.vehicle_id == vehicle_id, Telematic.deleted_at.is_(None)
            )
        ):
            raise TelematicConflictError("Xe đã được gán cho telematic khác")
    try:
        telematic_record = await repository.create(
            db_session,
            {
                "telematic_serial": telematic_create_request.telematic_serial,
                "vehicle_id": vehicle_id,
                "status": telematic_create_request.status,
                "firmware_version": telematic_create_request.firmware_version,
            },
        )
    except IntegrityError as error:
        raise TelematicConflictError("Telematic serial hoặc xe đã tồn tại") from error
    return await build_telematic_response(db_session, telematic_record)


async def get_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> TelematicResponse:
    """Lấy chi tiết thiết bị."""
    telematic_record = await repository.get_by_id(db_session, telematic_id)
    if not telematic_record:
        raise TelematicNotFoundError("Không tìm thấy telematic")
    return await build_telematic_response(db_session, telematic_record)


async def list_telematics(
    db_session: AsyncSession,
    page: int,
    page_size: int,
    status: TelematicStatus | None,
) -> TelematicListResponse:
    """Lấy danh sách thiết bị có phân trang."""
    page_size = min(max(page_size, 1), settings.API_MAX_PAGE_SIZE)
    page = max(page, settings.API_DEFAULT_PAGE)
    telematic_records = await repository.list_items(
        db_session,
        (page - 1) * page_size,
        page_size,
        status,
    )
    return TelematicListResponse(
        items=[
            await build_telematic_response(db_session, telematic_record)
            for telematic_record in telematic_records
        ],
        total=await repository.count_items(db_session, status),
        page=page,
        page_size=page_size,
    )


async def update_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
    telematic_update_request: TelematicUpdate,
) -> TelematicResponse:
    """Cập nhật thiết bị và resolve lại VIN khi field được gửi."""
    telematic_record = await repository.get_by_id(db_session, telematic_id)
    if not telematic_record:
        raise TelematicNotFoundError("Không tìm thấy telematic")
    values = telematic_update_request.model_dump(exclude_unset=True)
    if (
        "telematic_serial" in values
        and values["telematic_serial"] != telematic_record.telematic_serial
        and await repository.get_by_serial(db_session, values["telematic_serial"])
    ):
        raise TelematicConflictError("Telematic serial đã tồn tại")
    if "vehicle_vin" in values:
        vin = values.pop("vehicle_vin")
        vehicle_reference = (
            await vehicle_service.resolve_vehicle_reference_by_vin(db_session, vin)
            if vin
            else None
        )
        values["vehicle_id"] = (
            vehicle_reference.vehicle_id if vehicle_reference else None
        )
    values = {
        key: value
        for key, value in values.items()
        if value is not None or key == "vehicle_id"
    }
    try:
        telematic_record = await repository.update(
            db_session,
            telematic_record,
            values,
        )
    except IntegrityError as error:
        raise TelematicConflictError("Xe đã được gán cho telematic khác") from error
    return await build_telematic_response(db_session, telematic_record)


async def soft_delete_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> None:
    """Soft delete thiết bị."""
    telematic_record = await repository.get_by_id(db_session, telematic_id)
    if not telematic_record:
        raise TelematicNotFoundError("Không tìm thấy telematic")
    await repository.soft_delete(db_session, telematic_record)
