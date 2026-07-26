"""Business exceptions raised by the vehicles domain."""


class VehicleError(Exception):
    """Base exception for vehicle business-rule failures."""


class VehicleNotFoundError(VehicleError):
    """Raised when a requested vehicle does not exist."""


class VehicleConflictError(VehicleError):
    """Raised when unique vehicle data conflicts with an existing vehicle."""
