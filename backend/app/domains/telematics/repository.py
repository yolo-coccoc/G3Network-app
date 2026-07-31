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

from app.domains.telematics.models import Telematic


async def get_by_id(db: AsyncSession, telematic_id: UUID) -> Telematic | None:
    """Lấy thiết bị chưa bị soft delete theo ID."""
    result = await db.execute(
        select(Telematic).where(
            Telematic.telematic_id == telematic_id, Telematic.deleted_at.is_(None)
        )
    )
    return result.scalar_one_or_none()


async def get_by_serial(
    db: AsyncSession, serial: str, include_deleted: bool = False
) -> Telematic | None:
    """Lấy thiết bị theo serial."""
    stmt = select(Telematic).where(Telematic.telematic_serial == serial)
    if not include_deleted:
        stmt = stmt.where(Telematic.deleted_at.is_(None))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_mapping(db: AsyncSession, serial: str) -> tuple[UUID, UUID] | None:
    """Trả về ID thiết bị và xe cho ingestion.

    Args:
        db: Phiên database do entry boundary sở hữu.
        serial: Serial vật lý của thiết bị cần tra cứu.

    Returns:
        Tuple ``(telematic_id, vehicle_id)`` khi thiết bị còn hoạt động trong
        hệ thống và đã được gán xe; ``None`` nếu chưa có mapping hợp lệ.
    """
    result = await db.execute(
        select(Telematic.telematic_id, Telematic.vehicle_id).where(
            Telematic.telematic_serial == serial,
            Telematic.deleted_at.is_(None),
            Telematic.vehicle_id.is_not(None),
        )
    )
    row = result.one_or_none()
    return (row.telematic_id, row.vehicle_id) if row else None


async def get_mappings(
    db: AsyncSession,
    serials: Sequence[str],
) -> dict[str, tuple[UUID, UUID]]:
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

    result = await db.execute(
        select(
            Telematic.telematic_id,
            Telematic.vehicle_id,
            Telematic.telematic_serial,
        )
        .where(Telematic.telematic_serial.in_(unique_serials))
        .where(Telematic.deleted_at.is_(None))
        .where(Telematic.vehicle_id.is_not(None))
    )
    return {
        row.telematic_serial: (row.telematic_id, row.vehicle_id) for row in result.all()
    }


async def list_items(
    db: AsyncSession, skip: int, limit: int, status: object | None
) -> list[Telematic]:
    """Lấy danh sách thiết bị chưa bị xoá."""
    stmt = (
        select(Telematic)
        .where(Telematic.deleted_at.is_(None))
        .order_by(Telematic.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(Telematic.status == status)
    return list((await db.execute(stmt)).scalars().all())


async def count_items(db: AsyncSession, status: object | None) -> int:
    """Đếm thiết bị chưa bị xoá."""
    stmt = (
        select(func.count())
        .select_from(Telematic)
        .where(Telematic.deleted_at.is_(None))
    )
    if status is not None:
        stmt = stmt.where(Telematic.status == status)
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, values: dict[str, object]) -> Telematic:
    """Tạo thiết bị và flush để lấy ID."""
    item = Telematic(**values)
    db.add(item)
    await db.flush()
    return item


async def update(
    db: AsyncSession, item: Telematic, values: dict[str, object]
) -> Telematic:
    """Cập nhật các trường đã được service cho phép."""
    for key, value in values.items():
        setattr(item, key, value)
    await db.flush()
    return item


async def soft_delete(db: AsyncSession, item: Telematic) -> None:
    """Đánh dấu xoá mềm thiết bị."""
    item.deleted_at = datetime.now(timezone.utc)
    item.updated_at = item.deleted_at
    await db.flush()
