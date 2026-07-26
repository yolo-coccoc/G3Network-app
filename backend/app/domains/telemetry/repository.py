"""
Telemetry repository layer.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Repository xử lý database operations cho telemetry data:
- Batch lookup telematic mappings
- Bulk insert telemetry data
- Update telematic last_seen_at
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update, case, bindparam
from sqlalchemy.dialects.postgresql import insert

from app.domains.telemetry.models import Telematic, VehicleTelemetry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_telematic_mappings(
    db: "AsyncSession",
    serials: "Sequence[str]",
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
        row.telematic_serial: (row.telematic_id, row.vehicle_id)
        for row in rows
    }
    
    logger.debug(
        "get_telematic_mappings",
        extra={
            "requested": len(unique_serials),
            "found": len(mappings),
        }
    )
    
    return mappings


async def bulk_insert_telemetry(
    db: "AsyncSession",
    messages: "Sequence[dict]",
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
        - Caller phải commit transaction
    """
    if not messages:
        return 0
    
    # Build insert statement
    stmt = insert(VehicleTelemetry).values(messages)
    
    # Execute
    result = await db.execute(stmt)
    
    logger.debug(
        "bulk_insert_telemetry",
        extra={
            "rows_requested": len(messages),
            "rows_inserted": result.rowcount,
        }
    )
    
    return result.rowcount


async def update_telematic_last_seen(
    db: "AsyncSession",
    telematic_data: "Sequence[tuple[UUID, datetime]]",
) -> int:
    """
    Update last_seen_at cho nhiều telematics cùng lúc.
    
    Chỉ update nếu timestamp mới lớn hơn giá trị hiện tại.
    Dùng batch update để tối ưu performance.
    
    Args:
        db: AsyncSession để thao tác database
        telematic_data: Danh sách tuple (telematic_id, max_received_at)
                        - telematic_id: UUID của telematic
                        - max_received_at: Thời điểm nhận message cuối cùng của telematic đó
        
    Returns:
        Số rows đã update
        
    Note:
        - Chỉ update khi timestamp mới > timestamp hiện tại
        - Dùng CASE WHEN để update nhiều rows trong 1 query
        - Caller phải commit transaction
    """
    if not telematic_data:
        return 0
    
    # Build batch update với CASE WHEN
    # UPDATE telematics SET last_seen_at = CASE
    #   WHEN telematic_id = :id1 AND (:ts1 > last_seen_at OR last_seen_at IS NULL) THEN :ts1
    #   WHEN telematic_id = :id2 AND (:ts2 > last_seen_at OR last_seen_at IS NULL) THEN :ts2
    #   ...
    #   ELSE last_seen_at
    # END
    # WHERE telematic_id IN (:id1, :id2, ...)
    
    # Build CASE WHEN clauses
    case_clauses = []
    telematic_ids = []
    
    for telematic_id, max_received_at in telematic_data:
        telematic_ids.append(telematic_id)
        case_clauses.append(
            (Telematic.telematic_id == telematic_id, max_received_at)
        )
    
    # Build WHERE clause for last_seen_at comparison
    # Chỉ update khi timestamp mới > timestamp hiện tại HOẶC last_seen_at IS NULL
    case_whens = [
        (
            (Telematic.telematic_id == telematic_id) & 
            ((bindparam(f"ts_{i}", max_received_at) > Telematic.last_seen_at) | (Telematic.last_seen_at.is_(None))),
            bindparam(f"ts_{i}", max_received_at)
        )
        for i, (telematic_id, max_received_at) in enumerate(telematic_data)
    ]
    
    # Build update statement
    stmt = (
        update(Telematic)
        .where(Telematic.telematic_id.in_(telematic_ids))
        .values(
            last_seen_at=case(*case_whens, else_=Telematic.last_seen_at),
            updated_at=datetime.utcnow(),
        )
    )
    
    result = await db.execute(stmt)
    
    logger.debug(
        "update_telematic_last_seen",
        extra={
            "telematics_requested": len(telematic_data),
            "rows_updated": result.rowcount,
        }
    )
    
    return result.rowcount
