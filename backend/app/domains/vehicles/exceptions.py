"""Business exceptions raised by the vehicles domain."""

from app.libs.common.errors import ConflictError, DomainError, NotFoundError


class VehicleError(DomainError):
    """Base exception for vehicle business-rule failures."""


class VehicleNotFoundError(VehicleError, NotFoundError):
    """Raised when a requested vehicle does not exist."""


class VehicleConflictError(VehicleError, ConflictError):
    """Raised when unique vehicle data conflicts with an existing vehicle."""
