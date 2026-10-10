"""Business exceptions for the charging_stations domain."""

from app.libs.common.errors import ConflictError, InvalidInputError, NotFoundError


class ChargingStationNotFoundError(NotFoundError):
    """The referenced station was not found."""


class ChargingLocationNotFoundError(NotFoundError):
    """The referenced location was not found."""


class ChargingLocationAccessNotFoundError(NotFoundError):
    """The referenced access grant was not found."""


class ChargingLocationAccessConflictError(ConflictError):
    """An access grant is not allowed (to the owner itself, a duplicate, a closed one)."""


class ChargingEvseNotFoundError(NotFoundError):
    """The referenced EVSE was not found."""


class ChargingConnectorNotFoundError(NotFoundError):
    """The referenced connector was not found."""


class ChargingTopologyConflictError(ConflictError):
    """Station, EVSE, or connector topology violates an existing identity."""


class ChargingOcppMessageInputError(InvalidInputError):
    """An OCPP message to be logged violates the storage contract."""


class ChargingStationReportRangeError(InvalidInputError):
    """A report time window lacks a timezone or does not move forward (F-C5)."""


class ChargingStationCommandInputError(InvalidInputError):
    """A command misses a parameter its type needs, or points to a missing row (CS-20)."""
