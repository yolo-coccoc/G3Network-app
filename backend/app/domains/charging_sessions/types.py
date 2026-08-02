"""Các kiểu dữ liệu tối thiểu của charging_sessions MVP lý tưởng.

MVP giả định message đến đúng thứ tự, không duplicate và không gián đoạn. Vì
vậy module chỉ giữ status active/completed, ba loại TransactionEvent và một
meter sample canonical Wh.
"""

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


class SessionStatus(str, enum.Enum):
    """Trạng thái lifecycle duy nhất của phiên trong happy path."""

    ACTIVE = "active"
    COMPLETED = "completed"


class SessionEventType(str, enum.Enum):
    """Ba TransactionEvent được lưu trong MVP lý tưởng."""

    STARTED = "Started"
    UPDATED = "Updated"
    ENDED = "Ended"


@dataclass(frozen=True, slots=True)
class MeterSampleInput:
    """Một mẫu năng lượng đã canonical về Wh.

    Attributes:
        sampled_at: Thời điểm sample phát sinh, có timezone.
        value_wh: Giá trị năng lượng theo Wh.
    """

    sampled_at: datetime
    value_wh: Decimal


@dataclass(frozen=True, slots=True)
class TransactionIngestResult:
    """Kết quả xử lý một TransactionEvent happy path."""

    session_id: UUID
    status: SessionStatus
    event_count: int


@dataclass(frozen=True, slots=True)
class MeterIngestResult:
    """Kết quả xử lý batch MeterValues happy path."""

    session_id: UUID
    status: SessionStatus
    accepted_count: int
