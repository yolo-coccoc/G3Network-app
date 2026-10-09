"""Business exceptions raised by the drivers domain."""

from app.libs.common.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
)


class DriverError(DomainError):
    """Base exception for driver business-rule failures."""


class DriverNotFoundError(DriverError, NotFoundError):
    """Raised when a requested driver does not exist."""


class DriverConflictError(DriverError, ConflictError):
    """Raised when a membership already has a driver profile."""


class DriverMembershipNotFoundError(DriverError, NotFoundError):
    """Raised when the membership given for a driver profile does not exist."""


class DriverMembershipEndedError(DriverError, InvalidInputError):
    """Raised when a profile is created for a membership whose person left."""


class DriverLicenseExpiredError(DriverError, InvalidInputError):
    """Raised when a licence expiry date is already in the past."""


class DriverVehicleNotFoundError(DriverError, NotFoundError):
    """Raised when a VIN given for a check-in doesn't resolve to a vehicle."""


class DriverNotEligibleError(DriverError, InvalidInputError):
    """Raised when a driver may not check in (DR-10): inactive profile, expired
    licence, or a membership or user that is not active."""


class DrivingSessionConflictError(DriverError, ConflictError):
    """Raised when a check-in loses a race for the truck or the driver."""


class DrivingSessionNotFoundError(DriverError, NotFoundError):
    """Raised when checking out a driver that has no open driving session."""
