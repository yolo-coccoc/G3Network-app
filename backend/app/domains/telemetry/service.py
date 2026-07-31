"""Service nghiệp vụ của domain telemetry.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Luồng MVP hiện tại xử lý từng message để giảm độ trễ và cô lập transaction.
Các hàm batch vẫn được giữ nguyên trong module vì phase sau có thể cần tối ưu
throughput bằng batch lookup và bulk insert.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_service
import app.domains.telemetry.repository as telemetry_repository
from app.domains.telemetry.exceptions import TelemetryNotFoundError
from app.domains.telemetry.schemas import (
    LatestVehicleTelemetryResponse,
    TelemetryEnvelope,
)
from app.domains.vehicles import service as vehicle_service

logger = logging.getLogger(__name__)


async def get_latest_vehicle_telemetry_response(
    db: AsyncSession, vehicle_id: UUID
) -> LatestVehicleTelemetryResponse:
    """Lấy telemetry mới nhất sau khi xác nhận xe còn hoạt động.

    Args:
        db: Phiên database do HTTP boundary sở hữu.
        vehicle_id: ID nội bộ của xe cần truy vấn.

    Returns:
        Schema response chứa bản ghi telemetry mới nhất.

    Raises:
        TelemetryNotFoundError: Khi xe không tồn tại hoặc chưa có telemetry.
    """
    vehicle = await vehicle_service.find_active_vehicle_by_id(db, vehicle_id)
    if vehicle is None:
        raise TelemetryNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    telemetry = await telemetry_repository.get_latest_vehicle_telemetry(db, vehicle_id)
    if telemetry is None:
        raise TelemetryNotFoundError(
            f"No telemetry found for vehicle with id '{vehicle_id}'"
        )

    return LatestVehicleTelemetryResponse.model_validate(telemetry)


class BatchResult(TypedDict):
    """Các bộ đếm trả về sau khi xử lý một batch telemetry."""

    processed: int
    skipped: int
    errors: int


class MessageResult(TypedDict):
    """Các bộ đếm trả về sau khi xử lý một message telemetry."""

    processed: int
    skipped: int
    errors: int


async def process_message(
    db: AsyncSession,
    envelope: TelemetryEnvelope,
) -> MessageResult:
    """Xử lý một message telemetry trong phạm vi session hiện tại.

    Quy tắc nghiệp vụ:
    - Lookup đúng một mapping theo ``telematic_serial``.
    - Serial không tồn tại hoặc thiết bị chưa được gán xe sẽ bị skip an toàn.
    - Message hợp lệ được enrich rồi insert đúng một row.
    - Lỗi chuyển đổi message chỉ làm message hiện tại tăng ``errors``; lỗi DB
      được raise để transaction boundary rollback và worker dừng theo policy MVP.

    Args:
        db: AsyncSession do worker sở hữu transaction.
        envelope: Message đã được MQTT consumer validate và raw payload gốc.

    Returns:
        Dict gồm ``processed``, ``skipped`` và ``errors`` cho đúng message đó.

    Raises:
        Exception: Propagate lỗi database hoặc lỗi bất ngờ để worker rollback.

    Side Effects:
        Có thể ghi một row vào session và tạo structured log. Hàm không commit
        hoặc rollback.
    """
    message = envelope.message
    mapping = await telematics_service.resolve_mapping_by_serial(
        db, message.telematic_serial
    )
    if mapping is None:
        logger.warning(
            "telematic mapping not found, skipping message",
            extra={
                "telematic_serial": message.telematic_serial,
                "message_uuid": str(message.message_uuid),
            },
        )
        return {"processed": 0, "skipped": 1, "errors": 0}

    telematic_id, vehicle_id = mapping
    try:
        db_dict = message.to_db_dict(
            telematic_id,
            vehicle_id,
            datetime.now(timezone.utc),
            envelope.raw_payload,
        )
    except (TypeError, ValueError) as error:
        logger.exception(
            "failed to convert message to DB dict",
            extra={
                "telematic_serial": message.telematic_serial,
                "message_uuid": str(message.message_uuid),
                "error": str(error),
            },
        )
        return {"processed": 0, "skipped": 0, "errors": 1}

    processed_count = await telemetry_repository.insert_telemetry(db, db_dict)
    logger.info(
        "telemetry message persisted",
        extra={
            "message_uuid": str(message.message_uuid),
            "processed": processed_count,
        },
    )
    return {"processed": processed_count, "skipped": 0, "errors": 0}


async def process_batch(
    db: AsyncSession, messages: Sequence[TelemetryEnvelope]
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
    unique_serials = list(
        set(envelope.message.telematic_serial for envelope in messages)
    )

    # Step 2: Batch lookup telematic mappings (1 query cho cả batch)
    # Returns: {telematic_serial: (telematic_id, vehicle_id)}
    telematic_mappings = await telematics_service.resolve_mappings_by_serial(
        db, unique_serials
    )

    # Step 3: Process từng message
    valid_messages = []
    skipped_count = 0
    error_count = 0

    # Thời điểm backend nhận message (cùng 1 timestamp cho cả batch)
    received_at = datetime.now(timezone.utc)

    for envelope in messages:
        message = envelope.message
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
        # (telematics.service.resolve_mappings_by_serial đã filter vehicle_id IS NOT NULL,
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
            db_dict = message.to_db_dict(
                telematic_id,
                vehicle_id,
                received_at,
                envelope.raw_payload,
            )
            valid_messages.append(db_dict)

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
