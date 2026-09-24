"""Business exceptions for the charging_stations domain."""


class ChargingStationNotFoundError(Exception):
    """The referenced station was not found."""


class ChargingEvseNotFoundError(Exception):
    """The referenced EVSE was not found."""


class ChargingConnectorNotFoundError(Exception):
    """The referenced connector was not found."""


class ChargingTopologyConflictError(Exception):
    """Station, EVSE, or connector topology violates an existing identity."""


class ChargingOcppMessageInputError(Exception):
    """An OCPP message to be logged violates the storage contract."""
