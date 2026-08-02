"""Các enum, input value và kết quả immutable của domain charging_sessions."""

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


class SessionStatus(str, enum.Enum):
    """Trạng thái vận hành của aggregate phiên sạc."""

    PENDING = "pending"
    ACTIVE = "active"
    ENDING = "ending"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class ReconciliationStatus(str, enum.Enum):
    """Trạng thái đối soát meter kỹ thuật của phiên."""

    PENDING = "pending"
    RECONCILED = "reconciled"
    INCONSISTENT = "inconsistent"
    UNAVAILABLE = "unavailable"


class SessionEventType(str, enum.Enum):
    """Loại event lifecycle được chuẩn hóa từ TransactionEvent."""

    STARTED = "Started"
    UPDATED = "Updated"
    ENDED = "Ended"
    INTERRUPTED = "Interrupted"


class EndReason(str, enum.Enum):
    """Nguyên nhân kết thúc hoặc gián đoạn phiên."""

    NORMAL = "normal"
    ABNORMAL = "abnormal"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class ChargingState(str, enum.Enum):
    """Charging state chuẩn hóa được lưu cùng event lifecycle."""

    PENDING = "pending"
    ACTIVE = "active"


class IngestOutcome(str, enum.Enum):
    """Kết quả xử lý một event hoặc một batch meter."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    IGNORED_OUT_OF_ORDER = "ignored_out_of_order"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class InterruptionReason(str, enum.Enum):
    """Lý do kỹ thuật khiến các session đang chạy bị gián đoạn."""

    OFFLINE = "offline"
    CONNECTION_LOST = "connection_lost"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MeterSampleInput:
    """Meter sample primitive được truyền qua public boundary.

    Attributes:
        sampled_at: Thời điểm sample phát sinh, phải có timezone.
        measurand: Chỉ nhận measurand năng lượng đã chuẩn hóa.
        phase: Phase nguồn, nullable.
        context: Context sample, nullable.
        source_value: Giá trị theo đơn vị gốc, dùng Decimal.
        source_unit: Đơn vị gốc, hiện hỗ trợ Wh và kWh.
        value_wh: Giá trị Wh đã chuẩn hóa bởi adapter, nullable để service tự tính.
        seq_no: Sequence MeterValues, nullable.
        sample_idempotency_key: Identity ổn định của sample từ adapter.
        sanitized_raw_payload: Payload đã redacted, nullable.
    """

    sampled_at: datetime
    measurand: str
    phase: str | None
    context: str | None
    source_value: Decimal
    source_unit: str
    value_wh: Decimal | None
    seq_no: int | None
    sample_idempotency_key: str
    sanitized_raw_payload: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class TransactionIngestResult:
    """Kết quả immutable của một TransactionEvent."""

    outcome: IngestOutcome
    session_id: UUID | None
    status: SessionStatus | None
    event_count: int
    sample_count: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class MeterIngestResult:
    """Kết quả immutable của một batch MeterValues."""

    outcome: IngestOutcome
    session_id: UUID | None
    status: SessionStatus | None
    accepted_count: int
    duplicate_count: int
    ignored_out_of_order_count: int
    conflict_count: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class InterruptionResult:
    """Kết quả immutable khi đánh dấu session của station bị gián đoạn."""

    station_id: UUID
    interrupted_count: int
    session_ids: tuple[UUID, ...]
    reason: InterruptionReason


# Chỉ các measurand này được phép đi vào bảng energy meter của MVP.
ENERGY_MEASURANDS = frozenset({"energy_import_register", "energy_import_interval"})

# Đơn vị năng lượng hợp lệ theo OCPP sau khi chuẩn hóa chữ hoa/thường.
ENERGY_UNIT_TO_WH: dict[str, Decimal] = {
    "wh": Decimal("1"),
    "kwh": Decimal("1000"),
}
