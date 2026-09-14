"""Ngoại lệ nghiệp vụ của domain telemetry."""


class TelemetryNotFoundError(Exception):
    """Không tìm thấy xe hoặc telemetry tương ứng."""


class TelemetryPublishError(Exception):
    """Không publish được command telemetry tới MQTT broker."""
