"""Business exceptions raised by the drivers domain."""

from app.libs.common.errors import ConflictError, DomainError, NotFoundError


class DriverError(DomainError):
    """Base exception for driver business-rule failures."""


class DriverNotFoundError(DriverError, NotFoundError):
    """Raised when a requested driver does not exist."""


class DriverConflictError(DriverError, ConflictError):
    """Raised when unique driver data conflicts with an existing driver."""


class DriverVehicleNotFoundError(DriverError, NotFoundError):
    """Raised when a VIN given for an assignment doesn't resolve to a vehicle."""


class DriverAssignmentConflictError(DriverError, ConflictError):
    """Raised when the target vehicle or driver already has a different active assignment."""


class DriverAssignmentNotFoundError(DriverError, NotFoundError):
    """Raised when unassigning a driver that has no active assignment."""
