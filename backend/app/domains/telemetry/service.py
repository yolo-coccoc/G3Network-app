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
from uuid import UUID, uuid4

from aiomqtt import MqttError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_service
import app.domains.telemetry.publisher as telemetry_publisher
import app.domains.telemetry.repository as telemetry_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.telemetry.exceptions import (
    TelemetryNotFoundError,
    TelemetryPublishError,
)
from app.domains.telemetry.schemas import (
    BatteryThresholdPushResponse,
    TelemetryAlertListResponse,
    TelemetryAlertResponse,
    TelemetryEnvelope,
    VehicleTelemetryHistoryResponse,
    VehicleTelemetryLatestResponse,
    VehicleTelemetryMapItemResponse,
    VehicleTelemetryMapResponse,
)
from app.domains.telemetry.types import TelemetryAlertStatus, TelemetryAlertType
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

# Ngưỡng cố định của MVP; khi có màn hình cấu hình sẽ chuyển thành policy riêng.
BATTERY_LOW_THRESHOLD_PERCENT = 20
BATTERY_ANOMALY_TEMPERATURE_CELSIUS = 55


async def get_latest_vehicle_telemetry_response(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleTelemetryLatestResponse:
    """Lấy telemetry mới nhất sau khi xác nhận xe còn hoạt động.

    Args:
        db: Phiên database do HTTP boundary sở hữu.
        vehicle_id: ID nội bộ của xe cần truy vấn.

    Returns:
        Schema response chứa bản ghi telemetry mới nhất.

    Raises:
        TelemetryNotFoundError: Khi xe không tồn tại hoặc chưa có telemetry.
    """
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db,
        vehicle_id,
    )
    if vehicle_reference is None:
        raise TelemetryNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    telemetry = await telemetry_repository.get_latest_vehicle_telemetry(db, vehicle_id)
    if telemetry is None:
        raise TelemetryNotFoundError(
            f"No telemetry found for vehicle with id '{vehicle_id}'"
        )

    return VehicleTelemetryLatestResponse.model_validate(telemetry)


async def get_vehicle_telemetry_history_response(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleTelemetryHistoryResponse:
    """Lấy toàn bộ lịch sử telemetry sau khi xác nhận xe tồn tại.

    Args:
        db: Phiên database do HTTP boundary sở hữu.
        vehicle_id: ID nội bộ của xe cần truy vấn.

    Returns:
        Toàn bộ telemetry theo thời gian tăng dần.

    Raises:
        TelemetryNotFoundError: Khi xe không tồn tại.
    """
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db, vehicle_id
    )
    if vehicle_reference is None:
        raise TelemetryNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    telemetry = await telemetry_repository.list_vehicle_telemetry_history(
        db, vehicle_id
    )
    return VehicleTelemetryHistoryResponse(
        items=[
            VehicleTelemetryLatestResponse.model_validate(item) for item in telemetry
        ],
        total=len(telemetry),
    )


async def get_vehicle_telemetry_map_response(
    db: AsyncSession,
) -> VehicleTelemetryMapResponse:
    """Lấy xe active kèm telemetry mới nhất để hiển thị trên bản đồ.

    Args:
        db: Phiên database do HTTP boundary sở hữu.

    Returns:
        Một item cho mọi xe active; xe chưa có telemetry vẫn được trả về với
        các trường vị trí nullable.
    """
    vehicle_references = await vehicle_service.list_active_vehicle_references(db)
    latest_telemetry = await telemetry_repository.list_latest_vehicle_telemetry(
        db, [reference.vehicle_id for reference in vehicle_references]
    )
    telemetry_by_vehicle = {item.vehicle_id: item for item in latest_telemetry}

    items = []
    for reference in vehicle_references:
        telemetry = telemetry_by_vehicle.get(reference.vehicle_id)
        items.append(
            VehicleTelemetryMapItemResponse(
                vehicle_id=reference.vehicle_id,
                vin=reference.vin,
                latitude=telemetry.latitude if telemetry else None,
                longitude=telemetry.longitude if telemetry else None,
                soc=telemetry.soc if telemetry else None,
                speed=telemetry.speed if telemetry else None,
                recorded_at=telemetry.recorded_at if telemetry else None,
            )
        )
    return VehicleTelemetryMapResponse(items=items, total=len(items))


async def push_battery_threshold_to_vehicle(
    db: AsyncSession,
    vehicle_id: UUID,
) -> BatteryThresholdPushResponse:
    """Publish ngưỡng cảnh báo pin MVP tới telematic của xe.

    Args:
        db: Phiên database do HTTP boundary sở hữu.
        vehicle_id: ID nội bộ của xe cần nhận command.

    Returns:
        Thông tin command đã publish thành công.

    Raises:
        TelemetryNotFoundError: Khi xe chưa có mapping telematic.
        TelemetryPublishError: Khi MQTT broker không nhận được command.
    """
    mapping = await telematics_service.resolve_mapping_by_vehicle_id(db, vehicle_id)
    if mapping is None or mapping.telematic_serial is None:
        raise TelemetryNotFoundError(
            f"No telematic mapping found for vehicle with id '{vehicle_id}'"
        )

    topic = settings.MQTT_BATTERY_THRESHOLD_TOPIC_TEMPLATE.format(
        telematic_serial=mapping.telematic_serial
    )
    published_at = datetime.now(timezone.utc)
    payload = {
        "type": "battery_alert_threshold",
        "version": 1,
        "message_id": str(uuid4()),
        "threshold_percent": BATTERY_LOW_THRESHOLD_PERCENT,
        "published_at": published_at.isoformat(),
    }
    try:
        await telemetry_publisher.MQTTPublisher().publish_json(topic, payload)
    except MqttError as error:
        raise TelemetryPublishError(
            f"Không publish được ngưỡng pin tới telematic '{mapping.telematic_serial}'"
        ) from error

    return BatteryThresholdPushResponse(
        vehicle_id=vehicle_id,
        telematic_serial=mapping.telematic_serial,
        threshold_percent=BATTERY_LOW_THRESHOLD_PERCENT,
        topic=topic,
        published_at=published_at,
    )


async def list_telemetry_alerts_response(
    db: AsyncSession,
    *,
    vehicle_id: UUID | None = None,
    status_filter: TelemetryAlertStatus | None = None,
) -> TelemetryAlertListResponse:
    """Liệt kê cảnh báo pin đã sinh từ telemetry.

    Args:
        db: Phiên database do HTTP boundary sở hữu.
        vehicle_id: Lọc theo xe nếu có.
        status_filter: Lọc theo trạng thái nếu có.

    Returns:
        Danh sách cảnh báo theo thời điểm mới nhất.
    """
    alerts = await telemetry_repository.list_telemetry_alerts(
        db,
        vehicle_id=vehicle_id,
        status_filter=status_filter,
    )
    return TelemetryAlertListResponse(
        items=[TelemetryAlertResponse.model_validate(alert) for alert in alerts],
        total=len(alerts),
    )


async def sync_battery_alerts(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    recorded_at: datetime,
    soc: float,
    battery_temperature: float | None,
) -> None:
    """Đồng bộ hai cảnh báo pin từ telemetry vừa được ghi nhận.

    Cảnh báo chỉ được mở một lần khi điều kiện bắt đầu và chỉ resolve khi
    điều kiện hết. Unique partial index ở database giữ invariant này ngay cả
    khi có nhiều worker cùng xử lý.

    Args:
        db: Phiên database của cùng transaction ingest telemetry.
        vehicle_id: Xe phát sinh dữ liệu.
        recorded_at: Thời điểm telemetry.
        soc: State of Charge hiện tại.
        battery_temperature: Nhiệt độ pin, nullable.

    Side Effects:
        Tạo hoặc resolve các bản ghi cảnh báo; không commit hoặc rollback.
    """
    low_payload: dict[str, object] = {
        "soc": soc,
        "threshold_percent": BATTERY_LOW_THRESHOLD_PERCENT,
    }
    if soc <= BATTERY_LOW_THRESHOLD_PERCENT:
        await telemetry_repository.open_telemetry_alert(
            db,
            vehicle_id=vehicle_id,
            alert_type=TelemetryAlertType.BATTERY_LOW,
            severity=2,
            triggered_at=recorded_at,
            payload=low_payload,
        )
    else:
        await telemetry_repository.resolve_telemetry_alert(
            db,
            vehicle_id=vehicle_id,
            alert_type=TelemetryAlertType.BATTERY_LOW,
            resolved_at=recorded_at,
        )

    if battery_temperature is None:
        return

    anomaly_payload: dict[str, object] = {
        "battery_temperature": battery_temperature,
        "threshold_celsius": BATTERY_ANOMALY_TEMPERATURE_CELSIUS,
    }
    if battery_temperature >= BATTERY_ANOMALY_TEMPERATURE_CELSIUS:
        await telemetry_repository.open_telemetry_alert(
            db,
            vehicle_id=vehicle_id,
            alert_type=TelemetryAlertType.BATTERY_ANOMALY,
            severity=3,
            triggered_at=recorded_at,
            payload=anomaly_payload,
        )
    else:
        await telemetry_repository.resolve_telemetry_alert(
            db,
            vehicle_id=vehicle_id,
            alert_type=TelemetryAlertType.BATTERY_ANOMALY,
            resolved_at=recorded_at,
        )


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

    telematic_id = mapping.telematic_id
    vehicle_id = mapping.vehicle_id
    try:
        telemetry_values = message.to_vehicle_telemetry_values(
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

    processed_count = await telemetry_repository.insert_telemetry(
        db,
        telemetry_values,
    )
    if processed_count:
        await sync_battery_alerts(
            db,
            vehicle_id=vehicle_id,
            recorded_at=message.recorded_at,
            soc=message.battery.soc,
            battery_temperature=message.battery.temperature,
        )
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
    # Mapping đã được chuẩn hóa thành DTO để tránh unpack tuple không rõ nghĩa.
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

        mapping = telematic_mappings[serial]
        telematic_id = mapping.telematic_id
        vehicle_id = mapping.vehicle_id

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
            telemetry_values = message.to_vehicle_telemetry_values(
                telematic_id,
                vehicle_id,
                received_at,
                envelope.raw_payload,
            )
            valid_messages.append(telemetry_values)

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
