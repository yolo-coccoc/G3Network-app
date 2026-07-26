"""
Telemetry service layer.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Service xử lý business logic cho telemetry data, bao gồm:
- Batch processing messages từ queue
- Validation và enrichment
- Gọi repository để persist data
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, TypedDict
from uuid import UUID

import app.domains.telemetry.repository as telemetry_repository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domains.telemetry.schemas import TelemetryMessage

logger = logging.getLogger(__name__)


class BatchResult(TypedDict):
    """Counters returned after processing a telemetry batch."""

    processed: int
    skipped: int
    errors: int


async def process_batch(
    db: "AsyncSession", messages: "Sequence[TelemetryMessage]"
) -> BatchResult:
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
        - Batch lookup: 1 query cho cả batch, không query từng message
        - Không dùng cache trong MVP
        - Nếu DB error: raise để batch worker xử lý
    """
    if not messages:
        return {"processed": 0, "skipped": 0, "errors": 0}

    logger.info("process_batch started", extra={"batch_size": len(messages)})

    # Step 1: Lấy danh sách telematic_serial duy nhất từ batch
    unique_serials = list(set(msg.telematic_serial for msg in messages))

    # Step 2: Batch lookup telematic mappings (1 query cho cả batch)
    # Returns: {telematic_serial: (telematic_id, vehicle_id)}
    telematic_mappings = await telemetry_repository.get_telematic_mappings(
        db, unique_serials
    )

    # Step 3: Process từng message
    valid_messages = []
    skipped_count = 0
    error_count = 0

    # Track max received_at cho mỗi telematic để update last_seen_at
    telematic_timestamps: dict[UUID, datetime] = {}

    # Thời điểm backend nhận message (cùng 1 timestamp cho cả batch)
    received_at = datetime.now(timezone.utc)

    for message in messages:
        serial = message.telematic_serial

        # Kiểm tra telematic_serial có tồn tại trong mapping không
        if serial not in telematic_mappings:
            logger.warning(
                "telematic_serial not found, skipping message",
                extra={
                    "telematic_serial": serial,
                    "message_uuid": str(message.message_uuid),
                },
            )
            skipped_count += 1
            continue

        telematic_id, vehicle_id = telematic_mappings[serial]

        # Kiểm tra vehicle_id có được gán không
        # (repository.get_telematic_mappings đã filter vehicle_id IS NOT NULL,
        # nhưng check thêm để chắc chắn)
        if vehicle_id is None:
            logger.warning(
                "vehicle_id is None for telematic, skipping message",
                extra={
                    "telematic_serial": serial,
                    "telematic_id": str(telematic_id),
                    "message_uuid": str(message.message_uuid),
                },
            )
            skipped_count += 1
            continue

        # Convert message sang DB dict
        try:
            db_dict = message.to_db_dict(telematic_id, vehicle_id, received_at)
            valid_messages.append(db_dict)

            # Track timestamp để update last_seen_at sau
            # Lưu MAX received_at cho mỗi telematic
            if (
                telematic_id not in telematic_timestamps
                or received_at > telematic_timestamps[telematic_id]
            ):
                telematic_timestamps[telematic_id] = received_at

        except (TypeError, ValueError) as error:
            logger.exception(
                "failed to convert message to DB dict",
                extra={
                    "telematic_serial": serial,
                    "message_uuid": str(message.message_uuid),
                    "error": str(error),
                },
            )
            error_count += 1
            continue

    # Step 4: Bulk insert vào database
    processed_count = 0
    if valid_messages:
        processed_count = await telemetry_repository.bulk_insert_telemetry(
            db, valid_messages
        )
        logger.info(
            "bulk_insert_telemetry completed",
            extra={
                "rows_inserted": processed_count,
                "rows_requested": len(valid_messages),
            },
        )

    # Step 5: Update last_seen_at cho các telematics
    if telematic_timestamps:
        telematic_data = list(telematic_timestamps.items())
        updated_count = await telemetry_repository.update_telematic_last_seen(
            db, telematic_data
        )
        logger.info(
            "update_telematic_last_seen completed",
            extra={"telematics_updated": updated_count},
        )

    # Log summary
    logger.info(
        "process_batch completed",
        extra={
            "processed": processed_count,
            "skipped": skipped_count,
            "total": len(messages),
        },
    )

    return {
        "processed": processed_count,
        "skipped": skipped_count,
        "errors": error_count,
    }
