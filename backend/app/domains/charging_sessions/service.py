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
from app.domains.charging_sessions.models import (
    ChargingSessionEventModel,
    ChargingSessionMeterValueModel,
    ChargingSessionModel,
)
from app.domains.charging_sessions.schemas import (
    ChargingSessionEventListResponse,
    ChargingSessionEventResponse,
    ChargingSessionListResponse,
    ChargingSessionMeterValueListResponse,
    ChargingSessionMeterValueResponse,
    ChargingSessionResponse,
)
from app.domains.charging_sessions.types import (
    MeterIngestResult,
    MeterSampleInput,
    SessionEventType,
    SessionStatus,
    TransactionIngestResult,
)
from app.libs.common.config import settings


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


def _apply_charging_session_meter_end(
    session: ChargingSessionModel, meter_end_wh: Decimal | None
) -> None:
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
        # MVP giả định register tăng đơn điệu; kiểm tra meter reset/decrease
        # thuộc reliability path và không được tự mở trong service này.
        session.energy_delivered_wh = meter_end_wh - session.meter_start_wh


def _paging(page: int, page_size: int) -> tuple[int, int, int]:
    """Chuẩn hóa tham số phân trang monitoring theo settings chung.

    Args:
        page: Trang caller yêu cầu.
        page_size: Kích thước trang caller yêu cầu.

    Returns:
        Tuple ``(page, page_size, offset)`` đã nằm trong giới hạn API.
    """
    normalized_page = max(page, settings.API_DEFAULT_PAGE)
    normalized_page_size = min(
        max(page_size, settings.API_DEFAULT_PAGE_SIZE), settings.API_MAX_PAGE_SIZE
    )
    return (
        normalized_page,
        normalized_page_size,
        (normalized_page - 1) * normalized_page_size,
    )


async def get_charging_session(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionResponse:
    """Lấy aggregate session cho endpoint monitoring.

    Args:
        db: Async session do HTTP boundary sở hữu.
        session_id: UUID aggregate cần xem.

    Returns:
        Response session chỉ chứa schema active MVP.

    Raises:
        ChargingSessionNotFoundError: Nếu session không tồn tại.

    Side Effects:
        Thực hiện một truy vấn aggregate; không commit hoặc rollback.
    """
    session = await repository.get_session_by_id(db, session_id)
    if session is None:
        raise ChargingSessionNotFoundError(f"Không tìm thấy session '{session_id}'")
    return ChargingSessionResponse.model_validate(session)


async def list_charging_sessions(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
) -> ChargingSessionListResponse:
    """Lấy danh sách session mới nhất cho endpoint monitoring.

    Args:
        db: Async session do HTTP boundary sở hữu.
        page: Trang bắt đầu từ một.
        page_size: Kích thước trang.

    Returns:
        Danh sách session và metadata phân trang.

    Side Effects:
        Thực hiện một truy vấn items và một truy vấn count; không load quan hệ
        ORM nên endpoint không tạo N+1 query và không commit/rollback.
    """
    normalized_page, normalized_page_size, offset = _paging(page, page_size)
    sessions = await repository.list_charging_sessions(
        db,
        offset=offset,
        limit=normalized_page_size,
    )
    total = await repository.count_sessions(db)
    return ChargingSessionListResponse(
        items=[ChargingSessionResponse.model_validate(session) for session in sessions],
        total=total,
        page=normalized_page,
        page_size=normalized_page_size,
    )


async def list_charging_session_events(
    db: AsyncSession,
    session_id: UUID,
    *,
    page: int,
    page_size: int,
) -> ChargingSessionEventListResponse:
    """Lấy lifecycle event phân trang cho endpoint monitoring.

    Args:
        db: Async session do HTTP boundary sở hữu.
        session_id: UUID session cần xem event.
        page: Trang bắt đầu từ một.
        page_size: Kích thước trang.

    Returns:
        Event response và metadata phân trang.

    Raises:
        ChargingSessionNotFoundError: Nếu session không tồn tại.

    Side Effects:
        Thực hiện một lookup session và hai truy vấn event (items/count); không
        load quan hệ ORM nên endpoint không tạo N+1 query.
    """
    await require_charging_session(db, session_id)
    normalized_page, normalized_page_size, offset = _paging(page, page_size)
    events = await repository.list_charging_session_events(
        db,
        session_id,
        offset=offset,
        limit=normalized_page_size,
    )
    total = await repository.count_session_events(db, session_id)
    return ChargingSessionEventListResponse(
        items=[to_charging_session_event_response(event) for event in events],
        total=total,
        page=normalized_page,
        page_size=normalized_page_size,
    )


async def list_charging_session_meter_values(
    db: AsyncSession,
    session_id: UUID,
    *,
    page: int,
    page_size: int,
) -> ChargingSessionMeterValueListResponse:
    """Lấy meter sample phân trang cho endpoint monitoring.

    Args:
        db: Async session do HTTP boundary sở hữu.
        session_id: UUID session cần xem meter.
        page: Trang bắt đầu từ một.
        page_size: Kích thước trang.

    Returns:
        Meter response và metadata phân trang.

    Raises:
        ChargingSessionNotFoundError: Nếu session không tồn tại.

    Side Effects:
        Thực hiện một lookup session và hai truy vấn meter (items/count); không
        load quan hệ ORM nên endpoint không tạo N+1 query.
    """
    await require_charging_session(db, session_id)
    normalized_page, normalized_page_size, offset = _paging(page, page_size)
    meter_values = await repository.list_charging_session_meter_values(
        db,
        session_id,
        offset=offset,
        limit=normalized_page_size,
    )
    total = await repository.count_session_meter_values(db, session_id)
    return ChargingSessionMeterValueListResponse(
        items=[
            to_charging_session_meter_value_response(meter) for meter in meter_values
        ],
        total=total,
        page=normalized_page,
        page_size=normalized_page_size,
    )


async def require_charging_session(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionModel:
    """Đảm bảo session tồn tại trước khi đọc history.

    Args:
        db: Async session hiện tại.
        session_id: UUID session cần kiểm tra.

    Returns:
        Aggregate session tồn tại.

    Raises:
        ChargingSessionNotFoundError: Nếu không tìm thấy session.
    """
    session = await repository.get_session_by_id(db, session_id)
    if session is None:
        raise ChargingSessionNotFoundError(f"Không tìm thấy session '{session_id}'")
    return session


def to_charging_session_event_response(
    event: ChargingSessionEventModel,
) -> ChargingSessionEventResponse:
    """Chuyển ORM event thành response schema monitoring.

    Args:
        event: ORM event đã được repository truy vấn.

    Returns:
        Event response không chứa raw payload.
    """
    return ChargingSessionEventResponse.model_validate(event)


def to_charging_session_meter_value_response(
    meter_value: ChargingSessionMeterValueModel,
) -> ChargingSessionMeterValueResponse:
    """Chuyển ORM meter sample thành response schema monitoring.

    Args:
        meter_value: ORM meter sample đã được repository truy vấn.

    Returns:
        Meter response canonical Wh.
    """
    return ChargingSessionMeterValueResponse.model_validate(meter_value)


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

    session: ChargingSessionModel | None
    if event_type == SessionEventType.STARTED:
        # Duplicate/idempotency và conflict được bảo vệ bởi unique constraint
        # nhưng chưa có nhánh xử lý riêng trong happy path MVP.
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
    _apply_charging_session_meter_end(session, meter_end)

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
    _apply_charging_session_meter_end(session, value_wh)

    session.updated_at = repository.utc_now()
    return MeterIngestResult(
        session_id=session.session_id,
        status=session.status,
        accepted_count=1,
    )
