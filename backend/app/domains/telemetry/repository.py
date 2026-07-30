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

from app.domains.telematics.models import Telematic
from app.domains.telemetry.models import VehicleTelemetry

logger = logging.getLogger(__name__)


async def get_telematic_mapping(
    db: AsyncSession,
    serial: str,
) -> tuple[UUID, UUID] | None:
    """Lấy mapping của một thiết bị telematic đã được gán vào xe.

    Args:
        db: Phiên database do entry boundary sở hữu.
        serial: Serial vật lý của thiết bị cần tra cứu.

    Returns:
        Tuple ``(telematic_id, vehicle_id)`` nếu thiết bị tồn tại và đã được
        gán xe; ``None`` nếu không tìm thấy mapping hợp lệ.
    """
    result = await db.execute(
        select(Telematic.telematic_id, Telematic.vehicle_id)
        .where(Telematic.telematic_serial == serial)
        .where(Telematic.vehicle_id.isnot(None))
    )
    row = result.one_or_none()
    if row is None:
        return None

    return row.telematic_id, row.vehicle_id


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
        CursorResult[Any], await db.execute(insert(VehicleTelemetry).values(message))
    )
    logger.debug(
        "insert_telemetry",
        extra={"rows_inserted": result.rowcount},
    )
    return result.rowcount


async def get_latest_vehicle_telemetry(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleTelemetry | None:
    """Lấy bản ghi telemetry mới nhất của một xe.

    Args:
        db: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.

    Returns:
        Bản ghi có `recorded_at` lớn nhất hoặc None nếu chưa có dữ liệu.
    """
    result = await db.execute(
        select(VehicleTelemetry)
        .where(VehicleTelemetry.vehicle_id == vehicle_id)
        .order_by(VehicleTelemetry.recorded_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


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
