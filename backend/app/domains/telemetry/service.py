"""
Telemetry service layer.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Service xử lý business logic cho telemetry data, bao gồm:
- Batch processing messages từ queue
- Validation và enrichment
- Gọi repository để persist data
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domains.telemetry.schemas import TelemetryMessage

logger = logging.getLogger(__name__)


async def process_batch(db: "AsyncSession", messages: "Sequence[TelemetryMessage]") -> dict:
    """
    Xử lý batch telemetry messages.
    
    Business logic:
    1. Lấy danh sách telematic_serial duy nhất từ batch
    2. Lookup telematic_id và vehicle_id từ repository (batch query)
    3. Với mỗi message:
       - Nếu telematic_serial không tồn tại: log warning, skip
       - Nếu vehicle_id là None: log warning, skip
       - Convert sang DB dict
    4. Bulk insert vào database
    5. Update last_seen_at cho các telematics
    
    Args:
        db: AsyncSession để thao tác database
        messages: Danh sách TelemetryMessage cần xử lý
        
    Returns:
        Dict với keys:
        - processed: số message processed thành công
        - skipped: số message bị skip (telematic không tồn tại, vehicle chưa gán)
        - errors: số message gặp lỗi
        
    Note:
        Method này sẽ được implement chi tiết ở bước 11.
        Hiện tại chỉ là stub để batch worker có thể gọi.
    """
    logger.warning(
        "process_batch is not implemented yet (stub)",
        extra={"batch_size": len(messages)}
    )
    
    return {
        "processed": 0,
        "skipped": len(messages),
        "errors": 0,
    }
