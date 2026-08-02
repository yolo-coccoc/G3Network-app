"""Public service ingest dữ liệu TransactionEvent và MeterValues.

Service này là boundary duy nhất để adapter OCPP gọi vào domain
``charging_sessions``. Input chỉ là UUID, enum và kiểu standard library; module
không import ``charging_stations``, không nhận Pydantic/ORM/OCPP object và không
commit/rollback transaction.
"""

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as repository
from app.domains.charging_sessions.exceptions import ChargingSessionInputError
from app.domains.charging_sessions.models import (
    ChargingSession,
    ChargingSessionEvent,
    ChargingSessionMeterValue,
)
from app.domains.charging_sessions.types import (
    ENERGY_MEASURANDS,
    ENERGY_UNIT_TO_WH,
    ChargingState,
    EndReason,
    IngestOutcome,
    InterruptionReason,
    InterruptionResult,
    MeterIngestResult,
    MeterSampleInput,
    ReconciliationStatus,
    SessionEventType,
    SessionStatus,
    TransactionIngestResult,
)

ENERGY_QUANTUM = Decimal("0.001")


def _utc(value: datetime, field_name: str) -> datetime:
    """Validate timestamp aware và normalize về UTC.

    Args:
        value: Timestamp từ adapter.
        field_name: Tên field dùng trong lỗi validation.

    Returns:
        Timestamp timezone UTC.

    Raises:
        ChargingSessionInputError: Nếu timestamp naive hoặc không hợp lệ.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ChargingSessionInputError(f"{field_name} phải có timezone")
    return value.astimezone(timezone.utc)


def _decimal(value: Decimal | None, field_name: str) -> Decimal | None:
    """Chuẩn hóa input Decimal mà không dùng float cho năng lượng."""
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise ChargingSessionInputError(f"{field_name} phải là Decimal")
    if not value.is_finite():
        raise ChargingSessionInputError(f"{field_name} phải là Decimal hữu hạn")
    return value


def _wh(value: Decimal, unit: str) -> Decimal:
    """Đổi giá trị năng lượng sang Wh và lượng tử hóa theo contract DB."""
    multiplier = ENERGY_UNIT_TO_WH.get(unit.strip().lower())
    if multiplier is None:
        raise ChargingSessionInputError(f"Đơn vị năng lượng không hỗ trợ: '{unit}'")
    normalized = (value * multiplier).quantize(ENERGY_QUANTUM, rounding=ROUND_HALF_UP)
    if normalized < 0:
        raise ChargingSessionInputError("Giá trị năng lượng không được âm")
    return normalized


def _normalize_meter(sample: MeterSampleInput) -> MeterSampleInput:
    """Validate và normalize toàn bộ field của meter sample."""
    sampled_at = _utc(sample.sampled_at, "sampled_at")
    measurand = sample.measurand.strip()
    if measurand not in ENERGY_MEASURANDS:
        raise ChargingSessionInputError(
            f"Measurand không thuộc MVP energy meter: '{sample.measurand}'"
        )
    if not sample.sample_idempotency_key.strip():
        raise ChargingSessionInputError("sample_idempotency_key không được rỗng")
    if len(sample.sample_idempotency_key) > 255:
        raise ChargingSessionInputError("sample_idempotency_key vượt quá 255 ký tự")
    if sample.seq_no is not None and sample.seq_no < 0:
        raise ChargingSessionInputError("seq_no không được âm")
    source_value = _decimal(sample.source_value, "source_value")
    if source_value is None:
        raise ChargingSessionInputError("source_value là bắt buộc")
    normalized_wh = _wh(source_value, sample.source_unit)
    provided_wh = _decimal(sample.value_wh, "value_wh")
    if (
        provided_wh is not None
        and provided_wh.quantize(ENERGY_QUANTUM, rounding=ROUND_HALF_UP)
        != normalized_wh
    ):
        raise ChargingSessionInputError(
            "value_wh không khớp source_value/source_unit sau khi normalize"
        )
    return MeterSampleInput(
        sampled_at=sampled_at,
        measurand=measurand,
        phase=sample.phase.strip() if sample.phase is not None else None,
        context=sample.context.strip() if sample.context is not None else None,
        source_value=source_value,
        source_unit=sample.source_unit.strip(),
        value_wh=normalized_wh,
        seq_no=sample.seq_no,
        sample_idempotency_key=sample.sample_idempotency_key.strip(),
        sanitized_raw_payload=sample.sanitized_raw_payload,
    )


def _event_matches(
    event: ChargingSessionEvent,
    *,
    event_occurred_at: datetime,
    event_type: SessionEventType,
    end_reason: EndReason | None,
    charging_state: ChargingState | None,
    meter_start_wh: Decimal | None,
    meter_end_wh: Decimal | None,
    idempotency_key: str,
    sanitized_raw_payload: dict[str, object] | None,
) -> bool:
    """So sánh fingerprint payload của event replay."""
    return (
        event.event_occurred_at == event_occurred_at
        and event.event_type == event_type
        and event.end_reason == end_reason
        and event.charging_state == charging_state
        and event.idempotency_key == idempotency_key
        and event.sanitized_raw_payload == sanitized_raw_payload
        and _event_meter_value(event, "meter_start_wh") == meter_start_wh
        and _event_meter_value(event, "meter_end_wh") == meter_end_wh
    )


def _event_meter_value(event: ChargingSessionEvent, field_name: str) -> Decimal | None:
    """Lấy meter field từ sanitized payload nếu event history có lưu field đó.

    Model history MVP hiện chưa có cột meter start/end. Các field này được
    fingerprint bằng raw payload chuẩn hóa ở service; helper giữ lại một điểm
    mở rộng rõ ràng để migration tương lai bổ sung cột mà không đổi state rule.
    """
    payload = event.sanitized_raw_payload
    if payload is None:
        return None
    value = payload.get(field_name)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _event_payload(
    payload: dict[str, object] | None,
    meter_start_wh: Decimal | None,
    meter_end_wh: Decimal | None,
) -> dict[str, object] | None:
    """Tạo bản copy payload có fingerprint meter, không mutate input caller."""
    if payload is None and meter_start_wh is None and meter_end_wh is None:
        return None
    result = dict(payload or {})
    if meter_start_wh is not None:
        result["meter_start_wh"] = str(meter_start_wh)
    if meter_end_wh is not None:
        result["meter_end_wh"] = str(meter_end_wh)
    return result


def _mark_meter_reconciliation(session: ChargingSession, message: str) -> None:
    """Đánh dấu đối soát inconsistent và không làm giảm energy aggregate."""
    session.reconciliation_status = ReconciliationStatus.INCONSISTENT
    session.reconciliation_error = message[:1000]
    session.updated_at = repository.utc_now()


def _apply_meter_end(session: ChargingSession, meter_end_wh: Decimal | None) -> None:
    """Cập nhật meter end theo invariant không lùi và không tạo energy âm."""
    if meter_end_wh is None:
        return
    if session.meter_end_wh is not None and meter_end_wh < session.meter_end_wh:
        _mark_meter_reconciliation(session, "meter_reset_or_decrease")
        return
    if session.meter_start_wh is not None and meter_end_wh < session.meter_start_wh:
        _mark_meter_reconciliation(session, "meter_end_before_meter_start")
        return
    session.meter_end_wh = meter_end_wh
    if session.meter_start_wh is not None:
        session.energy_delivered_wh = meter_end_wh - session.meter_start_wh


def _set_last_event(session: ChargingSession, event: ChargingSessionEvent) -> None:
    """Cập nhật ordering aggregate bằng max, không làm timestamp lùi."""
    if session.last_event_at is None or event.event_occurred_at > session.last_event_at:
        session.last_event_at = event.event_occurred_at
    if (
        session.last_transaction_seq_no is None
        or event.seq_no > session.last_transaction_seq_no
    ):
        session.last_transaction_seq_no = event.seq_no
    session.updated_at = repository.utc_now()


def _status_for_started(
    charging_state: ChargingState | None, meter_start_wh: Decimal | None
) -> SessionStatus:
    """Chọn trạng thái ban đầu theo charging state và meter hợp lệ."""
    if charging_state == ChargingState.ACTIVE or meter_start_wh is not None:
        return SessionStatus.ACTIVE
    return SessionStatus.PENDING


async def ingest_transaction_event(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    event_type: SessionEventType,
    seq_no: int,
    event_occurred_at: datetime,
    received_at: datetime,
    charging_state: ChargingState | None,
    end_reason: EndReason | None,
    meter_start_wh: Decimal | None,
    meter_end_wh: Decimal | None,
    idempotency_key: str,
    sanitized_raw_payload: dict[str, object] | None,
) -> TransactionIngestResult:
    """Nhận một TransactionEvent theo state machine và idempotency contract.

    Event và thay đổi aggregate dùng cùng transaction của caller. Event mới luôn
    được append trước khi cập nhật aggregate; duplicate/conflict được quyết định
    theo logical key ``(session, seq_no)``. Event out-of-order vẫn được lưu audit
    nhưng không thể làm lùi state, timestamp hoặc sequence.

    Raises:
        ChargingSessionInputError: Input primitive không hợp lệ.
    """
    if not transaction_id.strip() or len(transaction_id) > 255:
        raise ChargingSessionInputError("transaction_id rỗng hoặc vượt quá 255 ký tự")
    if not idempotency_key.strip() or len(idempotency_key) > 255:
        raise ChargingSessionInputError("idempotency_key rỗng hoặc vượt quá 255 ký tự")
    if seq_no < 0:
        raise ChargingSessionInputError("seq_no không được âm")
    if event_type == SessionEventType.STARTED and end_reason is not None:
        raise ChargingSessionInputError("Started không được có end_reason")
    if event_type == SessionEventType.ENDED and end_reason is None:
        raise ChargingSessionInputError("Ended phải có end_reason")
    occurred_at = _utc(event_occurred_at, "event_occurred_at")
    received = _utc(received_at, "received_at")
    meter_start = _decimal(meter_start_wh, "meter_start_wh")
    meter_end = _decimal(meter_end_wh, "meter_end_wh")
    if meter_start is not None and meter_start < 0:
        raise ChargingSessionInputError("meter_start_wh không được âm")
    if meter_end is not None and meter_end < 0:
        raise ChargingSessionInputError("meter_end_wh không được âm")
    transaction_id = transaction_id.strip()
    idempotency_key = idempotency_key.strip()
    event_payload = _event_payload(sanitized_raw_payload, meter_start, meter_end)

    session = await repository.get_session_by_transaction(
        db, station_id, transaction_id, for_update=True
    )
    if session is None and event_type != SessionEventType.STARTED:
        return TransactionIngestResult(
            outcome=IngestOutcome.REJECTED,
            session_id=None,
            status=None,
            event_count=0,
            sample_count=0,
            reason="unknown_transaction",
        )
    if session is None:
        session = await repository.create_session(
            db,
            station_id=station_id,
            evse_id=evse_id,
            connector_id=connector_id,
            transaction_id=transaction_id,
            status=_status_for_started(charging_state, meter_start),
            started_at=occurred_at,
            event_occurred_at=occurred_at,
            seq_no=seq_no,
            meter_start_wh=meter_start,
        )
    elif session.evse_id != evse_id or session.connector_id != connector_id:
        return TransactionIngestResult(
            outcome=IngestOutcome.CONFLICT,
            session_id=session.session_id,
            status=session.status,
            event_count=0,
            sample_count=0,
            reason="transaction_topology_conflict",
        )

    existing_events = await repository.find_event_by_logical_key(
        db, session.session_id, seq_no
    )
    for existing in existing_events:
        if _event_matches(
            existing,
            event_occurred_at=occurred_at,
            event_type=event_type,
            end_reason=end_reason,
            charging_state=charging_state,
            meter_start_wh=meter_start,
            meter_end_wh=meter_end,
            idempotency_key=idempotency_key,
            sanitized_raw_payload=event_payload,
        ):
            return TransactionIngestResult(
                outcome=IngestOutcome.DUPLICATE,
                session_id=session.session_id,
                status=session.status,
                event_count=0,
                sample_count=0,
            )
    if existing_events:
        return TransactionIngestResult(
            outcome=IngestOutcome.CONFLICT,
            session_id=session.session_id,
            status=session.status,
            event_count=0,
            sample_count=0,
            reason="transaction_event_payload_conflict",
        )

    event = await repository.insert_event(
        db,
        session_id=session.session_id,
        event_occurred_at=occurred_at,
        event_type=event_type,
        seq_no=seq_no,
        end_reason=end_reason,
        charging_state=charging_state,
        idempotency_key=idempotency_key,
        received_at=received,
        sanitized_raw_payload=event_payload,
    )

    previous_seq = session.last_transaction_seq_no
    out_of_order = previous_seq is not None and seq_no < previous_seq
    terminal = session.status in {
        SessionStatus.COMPLETED,
        SessionStatus.INTERRUPTED,
    }
    if out_of_order or terminal:
        return TransactionIngestResult(
            outcome=IngestOutcome.IGNORED_OUT_OF_ORDER,
            session_id=session.session_id,
            status=session.status,
            event_count=1,
            sample_count=0,
        )

    if event_type == SessionEventType.STARTED:
        if session.started_at is None:
            session.started_at = occurred_at
        if session.status == SessionStatus.PENDING and (
            charging_state == ChargingState.ACTIVE or meter_start is not None
        ):
            session.status = SessionStatus.ACTIVE
    elif event_type == SessionEventType.UPDATED:
        if session.status == SessionStatus.PENDING and (
            charging_state == ChargingState.ACTIVE
            or meter_start is not None
            or meter_end is not None
        ):
            session.status = SessionStatus.ACTIVE
    elif event_type == SessionEventType.ENDED:
        session.status = SessionStatus.ENDING
        session.ended_at = occurred_at
        session.status = (
            SessionStatus.COMPLETED
            if end_reason == EndReason.NORMAL
            else SessionStatus.INTERRUPTED
        )
    if meter_start is not None and session.meter_start_wh is None:
        session.meter_start_wh = meter_start
    _apply_meter_end(session, meter_end)
    _set_last_event(session, event)
    return TransactionIngestResult(
        outcome=IngestOutcome.ACCEPTED,
        session_id=session.session_id,
        status=session.status,
        event_count=1,
        sample_count=0,
    )


def _meter_matches(
    existing: ChargingSessionMeterValue, sample: MeterSampleInput
) -> bool:
    """So sánh fingerprint canonical của meter sample replay."""
    return existing.value_wh == sample.value_wh


async def ingest_meter_values(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    samples: Sequence[MeterSampleInput],
    received_at: datetime,
) -> MeterIngestResult:
    """Append batch MeterValues và cập nhật aggregate một cách idempotent.

    Batch dùng một transaction do caller sở hữu. Sample unknown transaction bị
    từ chối toàn bộ batch; duplicate không tạo row mới; conflict không ghi đè
    sample cũ và đánh dấu reconciliation inconsistent. Sample out-of-order vẫn
    được lưu audit nhưng không làm lùi aggregate.
    """
    received = _utc(received_at, "received_at")
    normalized = tuple(_normalize_meter(sample) for sample in samples)
    session = await repository.get_session_by_transaction(
        db, station_id, transaction_id.strip(), for_update=True
    )
    if session is None:
        return MeterIngestResult(
            outcome=IngestOutcome.REJECTED,
            session_id=None,
            status=None,
            accepted_count=0,
            duplicate_count=0,
            ignored_out_of_order_count=0,
            conflict_count=0,
            reason="unknown_transaction",
        )
    if session.evse_id != evse_id or session.connector_id != connector_id:
        return MeterIngestResult(
            outcome=IngestOutcome.CONFLICT,
            session_id=session.session_id,
            status=session.status,
            accepted_count=0,
            duplicate_count=0,
            ignored_out_of_order_count=0,
            conflict_count=0,
            reason="transaction_topology_conflict",
        )

    accepted = duplicate = ignored = conflicts = 0
    for sample in normalized:
        sample_wh = sample.value_wh
        if sample_wh is None:
            raise ChargingSessionInputError("Meter sample chưa được normalize về Wh")
        existing = await repository.find_meter_by_logical_identity(
            db,
            session_id=session.session_id,
            sampled_at=sample.sampled_at,
            measurand=sample.measurand,
            phase=sample.phase,
            context=sample.context,
        )
        if existing is not None:
            if _meter_matches(existing, sample):
                duplicate += 1
            else:
                conflicts += 1
                _mark_meter_reconciliation(session, "meter_sample_payload_conflict")
            continue

        previous_latest = await repository.get_latest_meter(db, session.session_id)
        out_of_order = session.last_meter_at is not None and (
            sample.sampled_at < session.last_meter_at
        )
        await repository.insert_meter_value(
            db,
            session_id=session.session_id,
            sampled_at=sample.sampled_at,
            measurand=sample.measurand,
            phase=sample.phase,
            context=sample.context,
            source_value=sample.source_value,
            source_unit=sample.source_unit,
            value_wh=sample_wh,
            seq_no=sample.seq_no,
            sample_idempotency_key=sample.sample_idempotency_key,
            received_at=received,
            sanitized_raw_payload=sample.sanitized_raw_payload,
        )
        if out_of_order or session.status in {
            SessionStatus.COMPLETED,
            SessionStatus.INTERRUPTED,
        }:
            ignored += 1
            continue

        accepted += 1
        if previous_latest is not None and sample_wh < previous_latest.value_wh:
            _mark_meter_reconciliation(session, "meter_reset_or_decrease")
        elif session.meter_start_wh is not None and sample_wh < session.meter_start_wh:
            _mark_meter_reconciliation(session, "meter_end_before_meter_start")
        else:
            if (
                session.last_meter_at is None
                or sample.sampled_at >= session.last_meter_at
            ):
                session.last_meter_at = sample.sampled_at
                session.meter_end_wh = sample_wh
                session.energy_delivered_wh = (
                    sample_wh - session.meter_start_wh
                    if session.meter_start_wh is not None
                    else None
                )
            if session.status == SessionStatus.PENDING:
                session.status = SessionStatus.ACTIVE
        session.updated_at = repository.utc_now()

    if conflicts:
        outcome = IngestOutcome.CONFLICT
    elif accepted:
        outcome = IngestOutcome.ACCEPTED
    elif ignored:
        outcome = IngestOutcome.IGNORED_OUT_OF_ORDER
    else:
        outcome = IngestOutcome.DUPLICATE
    return MeterIngestResult(
        outcome=outcome,
        session_id=session.session_id,
        status=session.status,
        accepted_count=accepted,
        duplicate_count=duplicate,
        ignored_out_of_order_count=ignored,
        conflict_count=conflicts,
    )


async def mark_station_interrupted(
    db: AsyncSession,
    *,
    station_id: UUID,
    interrupted_at: datetime,
    received_at: datetime,
    reason: InterruptionReason,
) -> InterruptionResult:
    """Chuyển mọi session chưa terminal của station sang ``interrupted``.

    Mỗi chuyển trạng thái được append thành ``Interrupted`` history event với
    sequence kế tiếp. Session terminal không bị thay đổi và không tạo event mới.
    """
    interrupted_time = _utc(interrupted_at, "interrupted_at")
    received = _utc(received_at, "received_at")
    sessions = await repository.list_interruptible_sessions(db, station_id)
    for session in sessions:
        next_seq = (session.last_transaction_seq_no or -1) + 1
        event = await repository.insert_event(
            db,
            session_id=session.session_id,
            event_occurred_at=interrupted_time,
            event_type=SessionEventType.INTERRUPTED,
            seq_no=next_seq,
            end_reason=(
                EndReason.OFFLINE
                if reason == InterruptionReason.OFFLINE
                else EndReason.UNKNOWN
            ),
            charging_state=None,
            idempotency_key=(
                f"station:{station_id}:transaction:{session.ocpp_transaction_id}:"
                f"interrupted:{interrupted_time.isoformat()}"
            )[:255],
            received_at=received,
            sanitized_raw_payload=None,
        )
        session.status = SessionStatus.INTERRUPTED
        session.ended_at = interrupted_time
        _set_last_event(session, event)
    return InterruptionResult(
        station_id=station_id,
        interrupted_count=len(sessions),
        session_ids=tuple(session.session_id for session in sessions),
        reason=reason,
    )
