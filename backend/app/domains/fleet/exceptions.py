"""Business exceptions raised by the fleet domain."""


class FleetError(Exception):
    """Base exception for fleet business-rule failures."""


class FleetNotFoundError(FleetError):
    """Raised when a requested fleet does not exist."""


class FleetConflictError(FleetError):
    """Raised when unique fleet data conflicts with an existing fleet."""


class FleetVehicleNotFoundError(FleetError):
    """Raised when a VIN given for a membership doesn't resolve to a vehicle."""


class FleetMembershipConflictError(FleetError):
    """Raised when the target vehicle already has a different active membership."""


class FleetMembershipNotFoundError(FleetError):
    """Raised when removing a vehicle that has no active membership in the fleet."""
