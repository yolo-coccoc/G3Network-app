"""Các enum và value type dùng chung trong domain charging_sessions."""

import enum


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
