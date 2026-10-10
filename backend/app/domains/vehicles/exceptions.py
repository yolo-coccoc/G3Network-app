"""Business exceptions raised by the vehicles domain."""

from app.libs.common.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
)


class VehicleError(DomainError):
    """Base exception for vehicle business-rule failures."""


class VehicleNotFoundError(VehicleError, NotFoundError):
    """Raised when a requested vehicle does not exist."""


class VehicleConflictError(VehicleError, ConflictError):
    """Raised when unique vehicle data conflicts with an existing vehicle."""


class VehicleModelNotFoundError(VehicleError, NotFoundError):
    """Raised when a requested vehicle model does not exist or was removed."""


class VehicleModelConflictError(VehicleError, ConflictError):
    """Raised when a vehicle model with the same make and model name exists."""


class VehicleTransferInvalidError(VehicleError, InvalidInputError):
    """Raised when an ownership transfer breaks a rule (VH-12).

    The new owner is the current owner, or the effective date is in the future
    or not after the date the current owner took the truck.
    """
