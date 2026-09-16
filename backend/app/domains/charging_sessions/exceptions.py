"""Business exceptions for the charging_sessions domain."""


class ChargingSessionNotFoundError(Exception):
    """The referenced charging session aggregate was not found."""


class ChargingSessionInputError(Exception):
    """Data normalized from the adapter violates the ingestion contract."""
