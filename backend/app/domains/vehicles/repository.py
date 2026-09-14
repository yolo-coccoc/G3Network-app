"""Repository truy vấn bảng vehicles, không chứa business rule."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings


async def insert(db_session: AsyncSession, values: dict[str, Any]) -> VehicleModel:
    """Thêm một bản ghi xe vào database.

    Args:
        db_session: Phiên database do entry boundary sở hữu.
        values: Các field dùng để khởi tạo bản ghi ORM.

    Returns:
        Bản ghi xe vừa được tạo.
    """
    vehicle_record = VehicleModel(**values)
    db_session.add(vehicle_record)
    await db_session.flush()
    await db_session.refresh(vehicle_record)
    return vehicle_record


async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel | None:
    """Tìm xe theo ID, loại trừ bản ghi đã soft delete.

    Args:
        db_session: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.

    Returns:
        Bản ghi xe hoặc None nếu không tìm thấy.
    """
    query_result = await db_session.execute(
        select(VehicleModel).where(
            and_(
                VehicleModel.vehicle_id == vehicle_id,
                VehicleModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_license_plate(
    db_session: AsyncSession, license_plate: str
) -> VehicleModel | None:
    """Tìm xe theo biển số, loại trừ bản ghi đã soft delete.

    Args:
        db_session: Phiên database hiện tại.
        license_plate: Biển số xe.

    Returns:
        Bản ghi xe hoặc None nếu không tìm thấy.
    """
    query_result = await db_session.execute(
        select(VehicleModel).where(
            and_(
                VehicleModel.license_plate == license_plate,
                VehicleModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_vin(db_session: AsyncSession, vin: str) -> VehicleModel | None:
    """Tìm xe theo VIN, loại trừ bản ghi đã soft delete.

    Args:
        db_session: Phiên database hiện tại.
        vin: Số khung của xe.

    Returns:
        Bản ghi xe hoặc None nếu không tìm thấy.
    """
    query_result = await db_session.execute(
        select(VehicleModel).where(
            and_(VehicleModel.vin == vin, VehicleModel.deleted_at.is_(None))
        )
    )
    return query_result.scalar_one_or_none()


async def list_all(
    db_session: AsyncSession,
    skip: int = 0,
    limit: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: VehicleStatus | None = None,
) -> list[VehicleModel]:
    """Lấy danh sách xe có phân trang, loại trừ bản ghi đã soft delete.

    Args:
        db_session: Phiên database hiện tại.
        skip: Số bản ghi bỏ qua.
        limit: Số bản ghi tối đa trả về.
        status_filter: Bộ lọc trạng thái nếu có.

    Returns:
        Danh sách bản ghi xe.
    """
    conditions: list[ColumnElement[bool]] = [VehicleModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(VehicleModel.status == status_filter)

    query_result = await db_session.execute(
        select(VehicleModel)
        .where(and_(*conditions))
        .order_by(VehicleModel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def list_all_active(db_session: AsyncSession) -> list[VehicleModel]:
    """Lấy toàn bộ xe active không phân trang cho các API bản đồ.

    Args:
        db_session: Phiên database do entry boundary sở hữu.

    Returns:
        Danh sách xe chưa soft delete theo thời điểm tạo giảm dần.
    """
    query_result = await db_session.execute(
        select(VehicleModel)
        .where(VehicleModel.deleted_at.is_(None))
        .order_by(VehicleModel.created_at.desc(), VehicleModel.vehicle_id.desc())
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession, status_filter: VehicleStatus | None = None
) -> int:
    """Đếm tổng số xe, loại trừ bản ghi đã soft delete.

    Args:
        db_session: Phiên database hiện tại.
        status_filter: Bộ lọc trạng thái nếu có.

    Returns:
        Tổng số xe.
    """
    conditions: list[ColumnElement[bool]] = [VehicleModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(VehicleModel.status == status_filter)

    query_result = await db_session.execute(
        select(func.count(VehicleModel.vehicle_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession, vehicle_id: UUID, values: dict[str, Any]
) -> VehicleModel | None:
    """Cập nhật các field được chỉ định của xe.

    Args:
        db_session: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.
        values: Các field cần cập nhật.

    Returns:
        Bản ghi xe sau cập nhật hoặc None nếu không tìm thấy.
    """
    vehicle_record = await get_by_id(db_session, vehicle_id)
    if not vehicle_record:
        return None

    for field_name, value in values.items():
        if hasattr(vehicle_record, field_name):
            setattr(vehicle_record, field_name, value)

    vehicle_record.updated_at = datetime.now(timezone.utc)
    await db_session.flush()
    await db_session.refresh(vehicle_record)
    return vehicle_record


async def soft_delete(
    db_session: AsyncSession, vehicle_id: UUID
) -> VehicleModel | None:
    """Soft delete xe bằng cách cập nhật deleted_at và status.

    Args:
        db_session: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.

    Returns:
        Bản ghi xe sau khi soft delete hoặc None nếu không tìm thấy.
    """
    vehicle_record = await get_by_id(db_session, vehicle_id)
    if not vehicle_record:
        return None

    vehicle_record.deleted_at = datetime.now(timezone.utc)
    vehicle_record.status = VehicleStatus.DECOMMISSIONED
    await db_session.flush()
    await db_session.refresh(vehicle_record)
    return vehicle_record
