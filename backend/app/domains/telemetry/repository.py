"""
Telemetry repository layer.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Repository xử lý database operations cho telemetry data:
- Batch lookup telematic mappings
- Bulk insert telemetry data
"""

import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telematics.models import Telematic
from app.domains.telemetry.models import VehicleTelemetry

logger = logging.getLogger(__name__)


async def get_telematic_mappings(
    db: AsyncSession,
    serials: Sequence[str],
) -> dict[str, tuple[UUID, UUID]]:
    """
    Lấy mapping từ telematic_serial sang (telematic_id, vehicle_id).

    Batch lookup - chỉ 1 query cho cả batch, không query từng message.

    Args:
        db: AsyncSession để thao tác database
        serials: Danh sách telematic_serial cần lookup

    Returns:
        Dict: {telematic_serial: (telematic_id, vehicle_id)}

    Note:
        - Chỉ trả về telematics có vehicle_id NOT NULL
        - Telematics không tồn tại hoặc chưa gán xe sẽ không có trong kết quả
    """
    if not serials:
        return {}

    # Remove duplicates
    unique_serials = list(set(serials))

    # Build query: SELECT telematic_id, vehicle_id, telematic_serial
    # FROM telematics WHERE telematic_serial IN (...) AND vehicle_id IS NOT NULL
    stmt = (
        select(
            Telematic.telematic_id,
            Telematic.vehicle_id,
            Telematic.telematic_serial,
        )
        .where(Telematic.telematic_serial.in_(unique_serials))
        .where(Telematic.vehicle_id.isnot(None))
    )

    result = await db.execute(stmt)
    rows = result.all()

    # Build mapping dict
    mappings = {
        row.telematic_serial: (row.telematic_id, row.vehicle_id) for row in rows
    }

    logger.debug(
        "get_telematic_mappings",
        extra={
            "requested": len(unique_serials),
            "found": len(mappings),
        },
    )

    return mappings


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
                  (output từ TelemetryMessage.to_db_dict())

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
    stmt = insert(VehicleTelemetry).values(messages)

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
