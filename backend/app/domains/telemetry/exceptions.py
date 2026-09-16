"""Business exceptions for the telemetry domain."""


class TelemetryNotFoundError(Exception):
    """Raised when the vehicle or its corresponding telemetry cannot be found."""
