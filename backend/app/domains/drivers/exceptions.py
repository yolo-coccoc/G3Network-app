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


class DriverMembershipLockedError(DriverError, InvalidInputError):
    """Raised when a profile is created for a membership that is locked."""


class DriverRoleMissingError(DriverError, InvalidInputError):
    """Raised when a profile is created for a membership without the DRIVER role."""


class DriverTooFarFromVehicleError(DriverError, InvalidInputError):
    """Raised when the phone is farther from the truck than allowed (DR-07)."""


class DriverCheckInLocationRequiredError(DriverError, InvalidInputError):
    """Raised when a QR or APP check-in carries no phone position (DR-07)."""


class DrivingSummaryRangeError(DriverError, InvalidInputError):
    """Raised when the summary range is empty or longer than allowed (DR-11)."""


class TripError(DriverError):
    """Base exception for trip business-rule failures (DR-12)."""


class TripNotFoundError(TripError, NotFoundError):
    """Raised when a trip does not exist or is not the caller's."""


class TripStateConflictError(TripError, ConflictError):
    """Raised when a trip's status does not allow the action."""


class TripNotCheckedInError(TripError, ConflictError):
    """Raised when a driver starts a trip without an open driving session."""


class TripInProgressConflictError(TripError, ConflictError):
    """Raised when the driving session already has a trip in progress."""


class TripInvalidPlanError(TripError, InvalidInputError):
    """Raised when a trip plan breaks a rule (end before start, ...)."""


class TripVehicleNotFoundError(TripError, NotFoundError):
    """Raised when the truck of a trip plan does not exist in the caller's reach."""
