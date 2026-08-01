"""Các enum và value type dùng chung trong domain charging_stations."""

import enum


class StationAdministrativeStatus(str, enum.Enum):
    """Trạng thái quản trị của charging station."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"


class StationConnectionStatus(str, enum.Enum):
    """Snapshot kết nối kỹ thuật gần nhất của station."""

    UNKNOWN = "unknown"
    CONNECTED = "connected"
    OFFLINE = "offline"


class EvseAdministrativeStatus(str, enum.Enum):
    """Trạng thái quản trị của EVSE."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class TechnicalStatus(str, enum.Enum):
    """Trạng thái kỹ thuật chuẩn hóa của EVSE hoặc connector."""

    UNKNOWN = "unknown"
    AVAILABLE = "available"
    OCCUPIED = "occupied"
    UNAVAILABLE = "unavailable"
    FAULTED = "faulted"


class StationSourceAction(str, enum.Enum):
    """Loại message OCPP tạo ra technical history."""

    BOOT_NOTIFICATION = "BootNotification"
    HEARTBEAT = "Heartbeat"
    STATUS_NOTIFICATION = "StatusNotification"
    NOTIFY_EVENT = "NotifyEvent"
