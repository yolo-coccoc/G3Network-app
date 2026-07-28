"""Repository truy vấn bảng thiết bị Telematic."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telematics.models import Telematic


async def get_by_id(db: AsyncSession, telematic_id: UUID) -> Telematic | None:
    """Lấy thiết bị chưa bị soft delete theo ID."""
    result = await db.execute(select(Telematic).where(Telematic.telematic_id == telematic_id, Telematic.deleted_at.is_(None)))
    return result.scalar_one_or_none()


async def get_by_serial(db: AsyncSession, serial: str, include_deleted: bool = False) -> Telematic | None:
    """Lấy thiết bị theo serial."""
    stmt = select(Telematic).where(Telematic.telematic_serial == serial)
    if not include_deleted:
        stmt = stmt.where(Telematic.deleted_at.is_(None))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_mapping(db: AsyncSession, serial: str) -> tuple[UUID, UUID] | None:
    """Trả về ID thiết bị và xe cho ingestion."""
    result = await db.execute(select(Telematic.telematic_id, Telematic.vehicle_id).where(Telematic.telematic_serial == serial, Telematic.deleted_at.is_(None), Telematic.vehicle_id.is_not(None)))
    row = result.one_or_none()
    return (row.telematic_id, row.vehicle_id) if row else None


async def list_items(db: AsyncSession, skip: int, limit: int, status: object | None) -> list[Telematic]:
    """Lấy danh sách thiết bị chưa bị xoá."""
    stmt = select(Telematic).where(Telematic.deleted_at.is_(None)).order_by(Telematic.created_at.desc()).offset(skip).limit(limit)
    if status is not None:
        stmt = stmt.where(Telematic.status == status)
    return list((await db.execute(stmt)).scalars().all())


async def count_items(db: AsyncSession, status: object | None) -> int:
    """Đếm thiết bị chưa bị xoá."""
    stmt = select(func.count()).select_from(Telematic).where(Telematic.deleted_at.is_(None))
    if status is not None:
        stmt = stmt.where(Telematic.status == status)
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, values: dict[str, object]) -> Telematic:
    """Tạo thiết bị và flush để lấy ID."""
    item = Telematic(**values)
    db.add(item)
    await db.flush()
    return item


async def update(db: AsyncSession, item: Telematic, values: dict[str, object]) -> Telematic:
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
