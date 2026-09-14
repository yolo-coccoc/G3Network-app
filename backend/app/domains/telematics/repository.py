"""Repository truy vấn bảng thiết bị Telematic.

Repository này chỉ được gọi từ service của domain ``telematics``. Các domain
khác phải dùng public service để không phụ thuộc trực tiếp vào model hoặc câu
SQL nội bộ của domain này.
"""

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telematics.models import TelematicModel
from app.domains.telematics.types import TelematicVehicleMapping


async def get_by_id(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> TelematicModel | None:
    """Lấy thiết bị chưa bị soft delete theo ID."""
    query_result = await db_session.execute(
        select(TelematicModel).where(
            TelematicModel.telematic_id == telematic_id,
            TelematicModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def get_by_serial(
    db_session: AsyncSession,
    serial: str,
    include_deleted: bool = False,
) -> TelematicModel | None:
    """Lấy thiết bị theo serial."""
    stmt = select(TelematicModel).where(TelematicModel.telematic_serial == serial)
    if not include_deleted:
        stmt = stmt.where(TelematicModel.deleted_at.is_(None))
    query_result = await db_session.execute(stmt)
    return query_result.scalar_one_or_none()


async def find_mapping_by_serial(
    db_session: AsyncSession,
    serial: str,
) -> TelematicVehicleMapping | None:
    """Tìm ánh xạ thiết bị–xe theo serial cho ingestion.

    Args:
        db: Phiên database do entry boundary sở hữu.
        serial: Serial vật lý của thiết bị cần tra cứu.

    Returns:
        Tuple ``(telematic_id, vehicle_id)`` khi thiết bị còn hoạt động trong
        hệ thống và đã được gán xe; ``None`` nếu chưa có mapping hợp lệ.
    """
    query_result = await db_session.execute(
        select(TelematicModel.telematic_id, TelematicModel.vehicle_id).where(
            TelematicModel.telematic_serial == serial,
            TelematicModel.deleted_at.is_(None),
            TelematicModel.vehicle_id.is_not(None),
        )
    )
    mapping_row = query_result.one_or_none()
    return (
        TelematicVehicleMapping(
            telematic_id=mapping_row.telematic_id,
            vehicle_id=mapping_row.vehicle_id,
        )
        if mapping_row
        else None
    )


async def find_mappings_by_serial(
    db_session: AsyncSession,
    serials: Sequence[str],
) -> dict[str, TelematicVehicleMapping]:
    """Trả về mapping thiết bị–xe cho nhiều serial trong một truy vấn.

    Args:
        db: Phiên database do entry boundary sở hữu.
        serials: Các serial vật lý cần tra cứu.

    Returns:
        Dict ánh xạ ``telematic_serial`` sang ``(telematic_id, vehicle_id)``.
        Thiết bị không tồn tại, đã soft delete hoặc chưa được gán xe bị loại.
    """
    unique_serials = list(set(serials))
    if not unique_serials:
        return {}

    query_result = await db_session.execute(
        select(
            TelematicModel.telematic_id,
            TelematicModel.vehicle_id,
            TelematicModel.telematic_serial,
        )
        .where(TelematicModel.telematic_serial.in_(unique_serials))
        .where(TelematicModel.deleted_at.is_(None))
        .where(TelematicModel.vehicle_id.is_not(None))
    )
    return {
        row.telematic_serial: TelematicVehicleMapping(
            telematic_id=row.telematic_id,
            vehicle_id=row.vehicle_id,
        )
        for row in query_result.all()
    }


async def list_all(
    db_session: AsyncSession,
    skip: int,
    limit: int,
    status: object | None,
) -> list[TelematicModel]:
    """Lấy danh sách thiết bị chưa bị xoá."""
    stmt = (
        select(TelematicModel)
        .where(TelematicModel.deleted_at.is_(None))
        .order_by(TelematicModel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(TelematicModel.status == status)
    return list((await db_session.execute(stmt)).scalars().all())


async def count(db_session: AsyncSession, status: object | None) -> int:
    """Đếm thiết bị chưa bị xoá."""
    stmt = (
        select(func.count())
        .select_from(TelematicModel)
        .where(TelematicModel.deleted_at.is_(None))
    )
    if status is not None:
        stmt = stmt.where(TelematicModel.status == status)
    return int((await db_session.execute(stmt)).scalar_one())


async def insert(
    db_session: AsyncSession,
    values: dict[str, object],
) -> TelematicModel:
    """Tạo thiết bị và flush để lấy ID."""
    telematic_record = TelematicModel(**values)
    db_session.add(telematic_record)
    await db_session.flush()
    return telematic_record


async def update_fields(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
    values: dict[str, object],
) -> TelematicModel:
    """Cập nhật các trường đã được service cho phép."""
    for field_name, value in values.items():
        setattr(telematic_record, field_name, value)
    await db_session.flush()
    return telematic_record


async def soft_delete(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
) -> None:
    """Đánh dấu xoá mềm thiết bị."""
    telematic_record.deleted_at = datetime.now(timezone.utc)
    telematic_record.updated_at = telematic_record.deleted_at
    await db_session.flush()
