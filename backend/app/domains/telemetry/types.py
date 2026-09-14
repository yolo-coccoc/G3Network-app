"""Các enum và kiểu dùng chung của domain telemetry."""

import enum


class TelemetryAlertType(str, enum.Enum):
    """Các loại cảnh báo pin được sinh trực tiếp từ telemetry."""

    BATTERY_LOW = "battery_low"
    BATTERY_ANOMALY = "battery_anomaly"


class TelemetryAlertStatus(str, enum.Enum):
    """Trạng thái vòng đời tối giản của cảnh báo telemetry."""

    OPEN = "open"
    RESOLVED = "resolved"
