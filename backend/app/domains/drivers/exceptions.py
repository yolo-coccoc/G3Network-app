"""Business exceptions raised by the drivers domain."""


class DriverError(Exception):
    """Base exception for driver business-rule failures."""


class DriverNotFoundError(DriverError):
    """Raised when a requested driver does not exist."""


class DriverConflictError(DriverError):
    """Raised when unique driver data conflicts with an existing driver."""


class DriverVehicleNotFoundError(DriverError):
    """Raised when a VIN given for an assignment doesn't resolve to a vehicle."""


class DriverAssignmentConflictError(DriverError):
    """Raised when the target vehicle or driver already has a different active assignment."""


class DriverAssignmentNotFoundError(DriverError):
    """Raised when unassigning a driver that has no active assignment."""
