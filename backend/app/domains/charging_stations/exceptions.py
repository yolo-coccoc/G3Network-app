"""Business exceptions for the charging_stations domain."""

from app.libs.common.errors import ConflictError, InvalidInputError, NotFoundError


class ChargingStationNotFoundError(NotFoundError):
    """The referenced station was not found."""


class ChargingEvseNotFoundError(NotFoundError):
    """The referenced EVSE was not found."""


class ChargingConnectorNotFoundError(NotFoundError):
    """The referenced connector was not found."""


class ChargingTopologyConflictError(ConflictError):
    """Station, EVSE, or connector topology violates an existing identity."""


class ChargingOcppMessageInputError(InvalidInputError):
    """An OCPP message to be logged violates the storage contract."""
