"""Repository truy cập dữ liệu của domain telemetry.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Module này chứa cả thao tác singular đang dùng cho luồng MVP hiện tại và các
thao tác batch được giữ lại để tái sử dụng khi throughput thực tế cần tối ưu.
Repository không sở hữu transaction: entry boundary truyền vào session và
quyết định commit hoặc rollback.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telemetry.models import TelemetryAlertModel, VehicleTelemetryModel
from app.domains.telemetry.types import TelemetryAlertStatus, TelemetryAlertType

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
        await db.execute(
            insert(VehicleTelemetryModel)
            .values(message)
            .on_conflict_do_nothing(constraint="uq_telematic_recorded_at")
        ),
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


async def list_vehicle_telemetry_history(
    db: AsyncSession, vehicle_id: UUID
) -> list[VehicleTelemetryModel]:
    """Lấy toàn bộ lịch sử telemetry của một xe theo thời gian tăng dần.

    Args:
        db: Phiên database hiện tại.
        vehicle_id: ID nội bộ của xe.

    Returns:
        Danh sách telemetry đầy đủ, không phân trang theo contract MVP.
    """
    result = await db.execute(
        select(VehicleTelemetryModel)
        .where(VehicleTelemetryModel.vehicle_id == vehicle_id)
        .order_by(
            VehicleTelemetryModel.recorded_at.asc(),
            VehicleTelemetryModel.message_id.asc(),
        )
    )
    return list(result.scalars().all())


async def list_latest_vehicle_telemetry(
    db: AsyncSession, vehicle_ids: list[UUID]
) -> list[VehicleTelemetryModel]:
    """Lấy một telemetry mới nhất cho mỗi xe bằng truy vấn ``DISTINCT ON``.

    Args:
        db: Phiên database hiện tại.
        vehicle_ids: Các xe cần lấy vị trí gần nhất.

    Returns:
        Danh sách tối đa một record cho mỗi xe.
    """
    if not vehicle_ids:
        return []
    result = await db.execute(
        select(VehicleTelemetryModel)
        .where(VehicleTelemetryModel.vehicle_id.in_(vehicle_ids))
        .distinct(VehicleTelemetryModel.vehicle_id)
        .order_by(
            VehicleTelemetryModel.vehicle_id,
            VehicleTelemetryModel.recorded_at.desc(),
        )
    )
    return list(result.scalars().all())


async def open_telemetry_alert(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    alert_type: TelemetryAlertType,
    severity: int,
    triggered_at: datetime,
    payload: Mapping[str, object],
) -> bool:
    """Mở cảnh báo nếu xe chưa có cảnh báo cùng loại đang mở.

    Args:
        db: Phiên database hiện tại.
        vehicle_id: Xe phát sinh cảnh báo.
        alert_type: Loại cảnh báo cần mở.
        severity: Mức độ cảnh báo.
        triggered_at: Timestamp telemetry kích hoạt cảnh báo.
        payload: Giá trị telemetry dùng để tra cứu.

    Returns:
        ``True`` nếu tạo record mới; ``False`` nếu đã có cảnh báo mở.
    """
    result = cast(
        CursorResult[Any],
        await db.execute(
            insert(TelemetryAlertModel)
            .values(
                vehicle_id=vehicle_id,
                alert_type=alert_type,
                status=TelemetryAlertStatus.OPEN,
                severity=severity,
                triggered_at=triggered_at,
                payload=dict(payload),
            )
            .on_conflict_do_nothing()
        ),
    )
    return bool(result.rowcount)


async def resolve_telemetry_alert(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    alert_type: TelemetryAlertType,
    resolved_at: datetime,
) -> int:
    """Đóng cảnh báo cùng loại đang mở của một xe.

    Args:
        db: Phiên database hiện tại.
        vehicle_id: Xe cần resolve cảnh báo.
        alert_type: Loại cảnh báo cần đóng.
        resolved_at: Timestamp telemetry xác nhận điều kiện đã hết.

    Returns:
        Số record đã chuyển sang ``resolved``.
    """
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(TelemetryAlertModel)
            .where(
                TelemetryAlertModel.vehicle_id == vehicle_id,
                TelemetryAlertModel.alert_type == alert_type,
                TelemetryAlertModel.status == TelemetryAlertStatus.OPEN,
            )
            .values(
                status=TelemetryAlertStatus.RESOLVED,
                resolved_at=resolved_at,
            )
        ),
    )
    return int(result.rowcount or 0)


async def list_telemetry_alerts(
    db: AsyncSession,
    *,
    vehicle_id: UUID | None = None,
    status_filter: TelemetryAlertStatus | None = None,
) -> list[TelemetryAlertModel]:
    """Lấy danh sách cảnh báo theo thời điểm kích hoạt giảm dần.

    Args:
        db: Phiên database hiện tại.
        vehicle_id: Lọc theo xe nếu được truyền.
        status_filter: Lọc theo trạng thái nếu được truyền.

    Returns:
        Các cảnh báo phù hợp với bộ lọc.
    """
    statement = select(TelemetryAlertModel)
    if vehicle_id is not None:
        statement = statement.where(TelemetryAlertModel.vehicle_id == vehicle_id)
    if status_filter is not None:
        statement = statement.where(TelemetryAlertModel.status == status_filter)
    result = await db.execute(
        statement.order_by(
            TelemetryAlertModel.triggered_at.desc(),
            TelemetryAlertModel.alert_id.desc(),
        )
    )
    return list(result.scalars().all())


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
