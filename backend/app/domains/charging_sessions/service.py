"""Public service ingest TransactionEvent và từng MeterValues message happy path.

MVP lý tưởng cố định thứ tự message và loại bỏ reliability branching. Caller ở
entry boundary vẫn sở hữu commit/rollback transaction.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as repository
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionNotFoundError,
)
from app.domains.charging_sessions.models import ChargingSession
from app.domains.charging_sessions.types import (
    MeterIngestResult,
    MeterSampleInput,
    SessionEventType,
    SessionStatus,
    TransactionIngestResult,
)


def _utc(value: datetime, field_name: str) -> datetime:
    """Kiểm tra timestamp aware và normalize về UTC.

    Args:
        value: Timestamp đầu vào từ adapter hoặc API.
        field_name: Tên field dùng trong thông báo lỗi.

    Returns:
        Timestamp có timezone UTC.

    Raises:
        ChargingSessionInputError: Nếu timestamp thiếu timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ChargingSessionInputError(f"{field_name} phải có timezone")
    return value.astimezone(timezone.utc)


def _energy(value: Decimal | None, field_name: str) -> Decimal | None:
    """Kiểm tra giá trị energy Decimal không âm.

    Args:
        value: Giá trị năng lượng có thể nullable.
        field_name: Tên field dùng trong thông báo lỗi.

    Returns:
        Decimal hữu hạn, không âm hoặc ``None``.

    Raises:
        ChargingSessionInputError: Nếu giá trị sai kiểu, không hữu hạn hoặc âm.
    """
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ChargingSessionInputError(f"{field_name} phải là Decimal hữu hạn")
    if value < 0:
        raise ChargingSessionInputError(f"{field_name} không được âm")
    return value


def _apply_meter_end(session: ChargingSession, meter_end_wh: Decimal | None) -> None:
    """Cập nhật meter cuối và energy delivered cho ORM session.

    Args:
        session: Aggregate ORM đang được xử lý trong transaction.
        meter_end_wh: Meter mới nhất; không thay đổi nếu là ``None``.

    Side Effects:
        Cập nhật ``meter_end_wh`` và tính lại năng lượng giao nếu có meter đầu.
    """
    if meter_end_wh is None:
        return
    session.meter_end_wh = meter_end_wh
    if session.meter_start_wh is not None:
        session.energy_delivered_wh = meter_end_wh - session.meter_start_wh


async def ingest_transaction_event(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    event_type: SessionEventType,
    event_occurred_at: datetime,
    meter_start_wh: Decimal | None = None,
    meter_end_wh: Decimal | None = None,
) -> TransactionIngestResult:
    """Xử lý một TransactionEvent theo lifecycle happy path.

    Rule:
        ``Started`` tạo aggregate mới; ``Updated`` và ``Ended`` yêu cầu
        aggregate đã tồn tại, đúng topology và message đến đúng thứ tự. Event
        luôn được append trước khi aggregate được cập nhật.

    Args:
        db: Async session do entry boundary sở hữu.
        station_id: UUID station phát sinh transaction.
        evse_id: UUID EVSE của transaction.
        connector_id: UUID connector của transaction.
        transaction_id: OCPP transaction identity.
        event_type: Loại event canonical.
        event_occurred_at: Thời điểm event, bắt buộc có timezone.
        meter_start_wh: Meter đầu phiên cho ``Started``.
        meter_end_wh: Meter mới nhất của ``Updated``/``Ended``.

    Returns:
        Kết quả gồm session UUID, status hiện tại và số event đã append.

    Raises:
        ChargingSessionInputError: Nếu input sai contract hoặc topology lệch.
        ChargingSessionNotFoundError: Nếu event không phải ``Started`` nhưng
            aggregate chưa tồn tại.

    Side Effects:
        Tạo hoặc cập nhật aggregate và append event trong transaction hiện tại;
        không tự commit hoặc rollback.
    """
    transaction_id = transaction_id.strip()
    if not transaction_id or len(transaction_id) > 255:
        raise ChargingSessionInputError("transaction_id rỗng hoặc vượt quá 255 ký tự")
    occurred_at = _utc(event_occurred_at, "event_occurred_at")
    meter_start = _energy(meter_start_wh, "meter_start_wh")
    meter_end = _energy(meter_end_wh, "meter_end_wh")

    session: ChargingSession | None
    if event_type == SessionEventType.STARTED:
        session = await repository.create_session(
            db,
            station_id=station_id,
            evse_id=evse_id,
            connector_id=connector_id,
            transaction_id=transaction_id,
            started_at=occurred_at,
            meter_start_wh=meter_start,
        )
    else:
        session = await repository.get_session_by_transaction(
            db, station_id, transaction_id
        )
        if session is None:
            raise ChargingSessionNotFoundError(
                f"Transaction '{transaction_id}' chưa có Started"
            )
        if session.evse_id != evse_id or session.connector_id != connector_id:
            raise ChargingSessionInputError("Topology của transaction không khớp")

    await repository.insert_event(
        db,
        session_id=session.session_id,
        event_occurred_at=occurred_at,
        event_type=event_type,
    )
    _apply_meter_end(session, meter_end)

    if event_type is SessionEventType.ENDED:
        session.ended_at = occurred_at
        session.status = SessionStatus.COMPLETED
    session.updated_at = repository.utc_now()
    return TransactionIngestResult(
        session_id=session.session_id,
        status=session.status,
        event_count=1,
    )


async def ingest_meter_values(
    db: AsyncSession,
    *,
    session_id: UUID,
    sample: MeterSampleInput,
) -> MeterIngestResult:
    """Lưu một MeterValues message và cập nhật aggregate.

    Rule:
        Mỗi lần gọi xử lý đúng một sample. MVP giả định message đến đúng thứ
        tự và không duplicate; sample hiện tại trở thành meter cuối của
        aggregate.

    Args:
        db: Async session do entry boundary sở hữu.
        session_id: UUID aggregate cần cập nhật.
        sample: Sample cần canonical về Wh và append vào history.

    Returns:
        Kết quả gồm session UUID, status và số sample đã nhận.

    Raises:
        ChargingSessionInputError: Nếu sample thiếu timezone hoặc energy không
            hợp lệ.
        ChargingSessionNotFoundError: Nếu aggregate không tồn tại.

    Side Effects:
        Append một meter sample và cập nhật aggregate trong cùng transaction;
        caller phải commit hoặc rollback transaction ở entry boundary.
    """
    session = await repository.get_session_by_id(db, session_id)
    if session is None:
        raise ChargingSessionNotFoundError(f"Không tìm thấy session '{session_id}'")
    sampled_at = _utc(sample.sampled_at, "sampled_at")
    value_wh = _energy(sample.value_wh, "value_wh")
    if value_wh is None:
        raise ChargingSessionInputError("value_wh là bắt buộc")
    await repository.insert_meter_value(
        db,
        session_id=session.session_id,
        sampled_at=sampled_at,
        value_wh=value_wh,
    )
    _apply_meter_end(session, value_wh)

    session.updated_at = repository.utc_now()
    return MeterIngestResult(
        session_id=session.session_id,
        status=session.status,
        accepted_count=1,
    )
