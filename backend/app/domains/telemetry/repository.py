"""Repository truy cập dữ liệu của domain telemetry.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Module này chứa cả thao tác singular đang dùng cho luồng MVP hiện tại và các
thao tác batch được giữ lại để tái sử dụng khi throughput thực tế cần tối ưu.
Repository không sở hữu transaction: entry boundary truyền vào session và
quyết định commit hoặc rollback.
"""

import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telemetry.models import VehicleTelemetryModel

logger = logging.getLogger(__name__)


async def insert_telemetry(
    db: AsyncSession,
    message: dict[str, object],
) -> int:
    """Insert một bản ghi telemetry bằng SQLAlchemy Core.

    Args:
        db: Phiên database do entry boundary sở hữu.
        message: Dict dữ liệu đã được service chuyển đổi theo model database.

    Returns:
        Số row được database báo đã insert.

    Side Effects:
        Ghi một row vào session hiện tại. Hàm không commit hoặc rollback.
    """
    result = cast(
        CursorResult[Any],
        await db.execute(insert(VehicleTelemetryModel).values(message)),
    )
    logger.debug(
        "insert_telemetry",
        extra={"rows_inserted": result.rowcount},
    )
    return result.rowcount


async def get_latest_vehicle_telemetry(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleTelemetryModel | None:
    """Lấy bản ghi telemetry mới nhất của một xe.

    Args:
        db: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.

    Returns:
        Bản ghi có `recorded_at` lớn nhất hoặc None nếu chưa có dữ liệu.
    """
    result = await db.execute(
        select(VehicleTelemetryModel)
        .where(VehicleTelemetryModel.vehicle_id == vehicle_id)
        .order_by(VehicleTelemetryModel.recorded_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def bulk_insert_telemetry(
    db: AsyncSession,
    messages: Sequence[dict[str, object]],
) -> int:
    """
    Bulk insert telemetry data vào database.

    Dùng SQLAlchemy Core insert (không phải ORM add_all) để tối ưu performance.

    Args:
        db: AsyncSession để thao tác database
        messages: Danh sách dict, mỗi dict là 1 row data
                  (output từ TelemetryMessage.to_vehicle_telemetry_values())

    Returns:
        Số rows đã insert

    Note:
        - Không dùng ORM add_all vì chậm với batch lớn
        - Dùng Core insert với values() để tận dụng bulk insert của PostgreSQL
        - Entry boundary sở hữu transaction và commit/rollback
    """
    if not messages:
        return 0

    # Build insert statement
    stmt = insert(VehicleTelemetryModel).values(messages)

    # Execute
    result = cast(CursorResult[Any], await db.execute(stmt))

    logger.debug(
        "bulk_insert_telemetry",
        extra={
            "rows_requested": len(messages),
            "rows_inserted": result.rowcount,
        },
    )

    return result.rowcount
